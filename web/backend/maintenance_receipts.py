"""Signed local host results consumed by the existing control writer.

The host CLI never opens a competing write connection after Control starts.
Receipts grant no HTTP access and cannot execute a Docker or Agent command.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat

from .maintenance import _atomic_json
from .version import PRODUCT_ID, CONSOLE_VERSION

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z')
_OP = re.compile(r'[a-f0-9]{32}\Z')
_STATES = {'succeeded','rolled_back','failed','rollback_failed'}
_TRANSITIONS = {'planned':{'failed'},'applying':{'succeeded','rollback_failed'},
                'rolling_back':{'rolled_back','rollback_failed'},'rollback_failed':{'rolled_back','rollback_failed'},
                'failed':{'failed'}}


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def _read(path, *, maximum=2*1024*1024):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError('Host result must be a bounded regular file')
    if os.name == 'posix' and (before.st_mode & 0o022 or before.st_uid not in {0,os.geteuid()}):
        raise ValueError('Host result ownership or permissions are unsafe')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev,before.st_ino) != (opened.st_dev,opened.st_ino):
            raise ValueError('Host result changed while opening')
        value = stream.read(maximum+1)
        after = os.fstat(stream.fileno())
    if len(value)>maximum or (opened.st_size,opened.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('Host result changed while reading')
    return value


def _key(data, seed=None):
    if not seed:
        seed = os.getenv('MOTOR_SECRET_KEY') or os.getenv('MOTOR_CONSOLE_TOKEN')
    if not seed:
        configured_file = os.getenv('MOTOR_SECRET_KEY_FILE')
        seed = _read(Path(configured_file) if configured_file else Path(data)/'.secret-key',maximum=8192).decode().strip()
    if not isinstance(seed,str) or len(seed)<24:
        raise ValueError('Persistent control key is unavailable for host receipt authentication')
    return hashlib.sha256(b'motor-console-host-receipt-v1\x00'+seed.encode()).digest()


def _safe_directory(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Host receipt directory must be an independent directory')
    info = path.stat()
    if os.name=='posix' and (info.st_mode & 0o022 or info.st_uid not in {0,os.geteuid()}):
        raise ValueError('Host receipt directory permissions are unsafe')


def write_host_result(data_dir, journal_path, *, seed=None):
    data = Path(data_dir).resolve()
    journal_path = Path(journal_path)
    journal_raw = _read(journal_path)
    journal = json.loads(journal_raw)
    plan_id, operation_id, status = (journal.get(key) for key in ('plan_id','operation_id','status'))
    if not isinstance(plan_id,str) or not _ID.fullmatch(plan_id) or not isinstance(operation_id,str) or not _OP.fullmatch(operation_id) or status not in _STATES:
        raise ValueError('Host result has an invalid plan, operation or terminal state')
    expected = data/'upgrades'/plan_id/'execution.json'
    for directory in (data,data/'upgrades',data/'upgrades'/plan_id):
        _safe_directory(directory)
    if journal_path.resolve()!=expected:
        raise ValueError('Host result is outside the recorded operation directory')
    destination = data/'.host-upgrade-receipts'
    destination.mkdir(mode=0o700,exist_ok=True)
    _safe_directory(destination)
    if os.name=='posix' and os.geteuid()==0:
        owner = data.stat()
        for directory in (data/'upgrades',data/'upgrades'/plan_id,destination):
            os.chown(directory,owner.st_uid,owner.st_gid)
        os.chown(journal_path,owner.st_uid,owner.st_gid)
    payload = {'format':1,'product_id':PRODUCT_ID,'plan_id':plan_id,'operation_id':operation_id,
        'status':status,'manifest_sha256':journal.get('manifest_sha256'),
        'target_version':journal.get('target_version'),'old_version':journal.get('old_version'),
        'journal_sha256':hashlib.sha256(journal_raw).hexdigest(),
        'created_at':datetime.now(timezone.utc).isoformat()}
    signature = hmac.new(_key(data,seed),_canonical(payload),hashlib.sha256).hexdigest()
    target = destination/(operation_id+'-'+status+'.json')
    document = {'payload':payload,'signature':signature}
    if target.exists():
        existing = json.loads(_read(target,maximum=65536))
        expected_signature = hmac.new(_key(data,seed),_canonical(existing.get('payload')),hashlib.sha256).hexdigest()
        if (not hmac.compare_digest(expected_signature,existing.get('signature',''))
                or any(existing['payload'].get(name)!=value for name,value in payload.items() if name!='created_at')):
            raise ValueError('A different immutable host result already uses this identity')
    if not target.exists():
        _atomic_json(target,document)
        if os.name=='posix' and os.geteuid()==0:
            owner=data.stat()
            os.chown(target,owner.st_uid,owner.st_gid)
    return {'pending_sync':True,'receipt_id':operation_id+'-'+status}


def synchronize_host_results(store, *, seed=None,current_version=None):
    """Validate local evidence, then atomically consume result + plan + audit."""
    result = {'applied':0,'already_applied':0,'pending':0,'rejected':0}
    root = store.directory/'.host-upgrade-receipts'
    if not root.exists():
        return result
    try:
        _safe_directory(root)
        key = _key(store.directory,seed)
    except Exception:
        result['rejected'] = 1
        return result
    running = current_version or CONSOLE_VERSION
    paths = sorted(root.glob('*.json'))
    if len(paths)>1000:
        result['rejected'] = len(paths)
        return result
    for path in paths:
        try:
            document = json.loads(_read(path,maximum=65536))
            payload, supplied = document['payload'],document['signature']
            signature = hmac.new(key,_canonical(payload),hashlib.sha256).hexdigest()
            if not isinstance(supplied,str) or not hmac.compare_digest(signature,supplied):
                raise ValueError('Host result authentication failed')
            if payload.get('format')!=1 or payload.get('product_id')!=PRODUCT_ID:
                raise ValueError('Host result product or format mismatch')
            plan_id, operation_id, status = (payload.get(name) for name in ('plan_id','operation_id','status'))
            if not isinstance(plan_id,str) or not _ID.fullmatch(plan_id) or not isinstance(operation_id,str) or not _OP.fullmatch(operation_id) or status not in _STATES:
                raise ValueError('Host result identity is invalid')
            receipt_id = operation_id+'-'+status
            if path.name!=receipt_id+'.json':
                raise ValueError('Host result filename is not bound to its operation')
            consumed = store.get('host_upgrade_receipts',receipt_id)
            if consumed:
                if consumed.get('signature')!=signature:
                    raise ValueError('Host result identity was already used by different evidence')
                result['already_applied']+=1
                continue
            for directory in (store.directory,store.directory/'upgrades',store.directory/'upgrades'/plan_id):
                _safe_directory(directory)
            journal_raw = _read(store.directory/'upgrades'/plan_id/'execution.json')
            journal = json.loads(journal_raw)
            if hashlib.sha256(journal_raw).hexdigest()!=payload.get('journal_sha256') or any(journal.get(name)!=payload.get(name) for name in ('plan_id','operation_id','status','manifest_sha256','target_version','old_version')):
                raise ValueError('Host result is not bound to the current execution journal')
            at = datetime.fromisoformat(payload['created_at'].replace('Z','+00:00'))
            if at.tzinfo is None or (at-datetime.now(timezone.utc)).total_seconds()>60:
                raise ValueError('Host result timestamp is invalid')
            if ((status=='succeeded' and running!=payload.get('target_version'))
                    or (status=='rolled_back' and running!=payload.get('old_version'))):
                result['pending']+=1
                continue
            with store.unit_of_work(write=True) as unit:
                consumed = unit.resources.get('host_upgrade_receipts',receipt_id)
                if consumed:
                    if consumed.get('signature')!=signature:
                        raise ValueError('Host result identity was already used by different evidence')
                    result['already_applied']+=1
                    continue
                plan = unit.resources.get('upgrade_plans',plan_id)
                if not plan or plan.get('manifest_sha256')!=payload.get('manifest_sha256') or plan.get('target_version')!=payload.get('target_version') or plan.get('current_version')!=payload.get('old_version'):
                    raise ValueError('Host result does not match the recorded signed plan')
                owner = plan.get('host_execution',{}).get('operation_id')
                if status not in _TRANSITIONS.get(plan.get('status'),set()) or owner not in (None,operation_id) or owner is None and status!='failed':
                    raise ValueError('Host result transition or execution owner is invalid')
                plan.update(status=status,updated_at=payload['created_at'],host_execution={
                    'operation_id':operation_id,'status':status,'receipt_id':receipt_id,'journal_sha256':payload['journal_sha256']})
                unit.resources.put('upgrade_plans',plan)
                unit.resources.put('host_upgrade_receipts',{'id':receipt_id,'signature':signature,'plan_id':plan_id,
                    'operation_id':operation_id,'status':status,'consumed_at':datetime.now(timezone.utc).isoformat()})
                store.audit('平台升级',plan_id,status,'已核对本机签名执行回执和运行版本',
                    actor='host-cli',request_id=operation_id,metadata={'receipt_id':receipt_id},connection=unit)
                result['applied']+=1
        except Exception:
            result['rejected']+=1
    return result
