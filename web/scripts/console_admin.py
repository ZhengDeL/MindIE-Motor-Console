#!/usr/bin/env python3
"""Apply or roll back a recorded signed control-plane plan on its Docker host.

Requires Python 3.11+ and cryptography. This tool only addresses the production
Compose control and optional independent Web services. Agent and Motor workloads are not upgraded.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

_WEB = Path(__file__).resolve().parents[1]
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

from backend.maintenance import ProcessLease, backup_database, verify_backup
from backend.observability import redact, register_secret
from backend.releases import read_release, version_tuple
from backend.version import AGENT_PROTOCOL_VERSION, PRODUCT_ID
from backend.settings import sqlite_wal_fix_available
from backend.store import APPLICATION_ID, Store
from backend.ports.storage import StorageError, StorageUnavailable
from backend.storage.configuration import database_options, read_environment
from backend.storage.admin import restore_offline
from backend.maintenance_receipts import write_host_result




class AdminError(RuntimeError):
    pass


class DatabaseUnavailable(AdminError):
    """Expected SQL resources cannot be read; distinct from a policy refusal."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise AdminError("Control health probes must not follow redirects")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".part")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            temporary.chmod(0o600)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _dotenv(path: Path) -> dict[str,str]:
    try:
        return read_environment(path)
    except ValueError as error:
        raise AdminError(str(error)) from error





class ConsoleAdmin:
    def __init__(self, project_dir, data_dir, *, env_file=None, runner=None, probe=None,
                 sleep=time.sleep, clock=time.monotonic, health_timeout=90, event_sink=None):
        if sys.version_info < (3, 11):
            raise AdminError("The host upgrade tool requires Python 3.11+ and cryptography")
        if not Path(project_dir).is_absolute() or not Path(data_dir).is_absolute():
            raise AdminError("project-dir and data-dir must be absolute host paths")
        self.project = Path(project_dir).resolve()
        self.data = Path(data_dir).resolve()
        self.database = self.data / "console.sqlite3"
        self.env_file = Path(env_file).resolve() if env_file else self.project / "web/.env.production"
        self.environment = _dotenv(self.env_file)
        compose_name = self.environment.get('MOTOR_COMPOSE_FILE','compose.production.yaml')
        if compose_name not in {'compose.production.yaml','compose.modular.yaml'}:
            raise AdminError('MOTOR_COMPOSE_FILE must select an explicitly supported Compose definition')
        self.modular = compose_name == 'compose.modular.yaml'
        self.compose = self.project/'web'/compose_name
        if not self.compose.is_file() or self.compose.is_symlink():
            raise AdminError('The selected reviewed Compose definition is missing or unsafe')
        self.network_mode = self.environment.get("MOTOR_CONTROL_NETWORK_MODE", "bridge")
        if self.network_mode not in {"bridge", "host"}:
            raise AdminError("MOTOR_CONTROL_NETWORK_MODE must be bridge or host")
        self.compose_files = (self.compose,)
        if self.modular and self.network_mode!='bridge':
            raise AdminError('Separated Web requires the reviewed bridge Compose layout')
        if self.network_mode == "host":
            overlay = self.project / "web/compose.production.host-network.yaml"
            if not overlay.is_file():
                raise AdminError("Host networking requires web/compose.production.host-network.yaml")
            self.compose_files += (overlay,)
        configured_data = self.environment.get("MOTOR_CONTROL_DATA_DIR", "")
        if not Path(configured_data).is_absolute() or Path(configured_data).resolve() != self.data:
            raise AdminError("MOTOR_CONTROL_DATA_DIR does not match --data-dir")
        key = self.environment.get("MOTOR_RELEASE_PUBLIC_KEY_HOST_FILE", "")
        if not Path(key).is_absolute() or not Path(key).is_file():
            raise AdminError("An absolute existing MOTOR_RELEASE_PUBLIC_KEY_HOST_FILE is required")
        self.key_file = Path(key).resolve()
        self.release_dir = self.data / "releases"
        self.runner = runner or subprocess.run
        self.sleep, self.clock, self.health_timeout = sleep, clock, health_timeout
        self.probe = probe or self._http_probe
        self.event_sink = event_sink or (lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
        self.operation_id = uuid4().hex
        self.journal = None
        self.journal_path = None
        self.marker = self.data / "maintenance.json"
        self._expected_env_sha = _sha(self.env_file)
        self._compose_sha = self._compose_digest()
        address = self.environment.get("MOTOR_CONTROL_BIND_IP", "127.0.0.1")
        parsed = ipaddress.ip_address(address)
        address = "127.0.0.1" if parsed.is_unspecified else str(parsed)
        port = int(self.environment.get("MOTOR_CONTROL_PORT", "19080"))
        if not 1 <= port <= 65535:
            raise AdminError("MOTOR_CONTROL_PORT is invalid")
        self.base_url = "http://" + ("[" + address + "]" if ":" in address else address) + ":" + str(port)

    def _http_probe(self, path):
        if path not in {'/health','/ready'}:
            raise AdminError('Only fixed control health paths may be probed')
        if self.modular:
            self._container()
            code = (
                "import sys,json\n"
                "from urllib.request import build_opener,ProxyHandler,HTTPRedirectHandler,Request\n"
                "from urllib.error import HTTPError\n"
                "class NoRedirect(HTTPRedirectHandler):\n"
                " def redirect_request(self,*args,**kwargs):return None\n"
                "try:\n"
                " opener=build_opener(ProxyHandler({}),NoRedirect())\n"
                " with opener.open(Request('http://127.0.0.1:8000'+sys.argv[1],headers={'Accept':'application/json'}),timeout=4) as r:\n"
                "  print(json.dumps({**json.loads(r.read(65537)),'_http_status':r.status}))\n"
                "except HTTPError as e:print(json.dumps({'_http_status':e.code,'status':'not_ready'}))"
            )
            return json.loads(self._compose_command(['exec','-T','control','python3','-c',code,path],timeout=8))
        opener = build_opener(ProxyHandler({}),_NoRedirect())
        try:
            with opener.open(Request(self.base_url+path,headers={'Accept':'application/json'}),timeout=4) as response:
                if response.geturl()!=self.base_url+path:
                    raise AdminError('Control health probe redirected unexpectedly')
                return {**json.loads(response.read(65537)),'_http_status':response.status}
        except HTTPError as error:
            return {'_http_status':error.code,'status':'not_ready'}


    def _emit(self, phase, **fields):
        event = redact({"time": _now(), "operation_id": self.operation_id, "phase": phase, **fields})
        if self.journal is not None:
            self.journal.setdefault("events", []).append(event)
            self.journal["updated_at"] = event["time"]
            _atomic_json(self.journal_path, self.journal)
        self.event_sink(event)

    def _command(self, argv, *, image=None, web_image=None, timeout=180):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("MOTOR_", "COMPOSE_"))}
        if image:
            environment["MOTOR_CONSOLE_IMAGE"] = image
        if web_image:
            environment["MOTOR_WEB_IMAGE"] = web_image
        try:
            result = self.runner(argv, env=environment, cwd=str(self.project), text=True, capture_output=True,
                                 timeout=timeout, shell=False, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdminError("Docker command was unavailable or timed out") from exc
        if result.returncode:
            detail = redact((result.stderr or "Docker returned a nonzero exit status")[-1600:])
            raise AdminError(str(detail))
        return result.stdout

    def _compose_command(self, arguments, *, image=None, web_image=None, timeout=180):
        prefix = ["docker", "compose", "--project-directory", str(self.project / "web"),
                  "--env-file", str(self.env_file)]
        for compose_file in self.compose_files:
            prefix.extend(["-f", str(compose_file)])
        return self._command([*prefix, *arguments], image=image, web_image=web_image, timeout=timeout)

    def _compose_digest(self):
        # Preserve the recorded checksum contract for existing bridge plans.
        if len(self.compose_files) == 1:
            return _sha(self.compose)
        sources = [(path.name, _sha(path)) for path in self.compose_files]
        return hashlib.sha256(json.dumps(sources, separators=(",", ":")).encode("utf-8")).hexdigest()

    @contextmanager
    def _open_storage(self, *, database=None, write=False):
        if database is not None and Path(database).resolve()!=self.database:
            if write:
                raise AdminError('A backup is never opened for modification')
            options={'backend':'sqlite','database_path':Path(database)}
        else:
            options=database_options(self.environment,self.data)
        with Store(self.data,read_only=not write,initialize=False,**options) as storage:
            yield storage

    def _snapshot(self, destination, *, reason):
        with self._open_storage() as storage:
            return storage.backup(destination,reason=reason)

    def _db_info(self, database=None):
        try:
            with self._open_storage(database=database) as storage:
                return storage.schema_info()
        except StorageUnavailable as error:
            raise DatabaseUnavailable('Expected control storage is unavailable') from error


    @staticmethod
    def _check_plan_id(plan_id):
        if not isinstance(plan_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", plan_id):
            raise AdminError("Invalid recorded plan ID")

    def _plan(self, plan_id, database=None):
        self._check_plan_id(plan_id)
        self._db_info(database)
        with self._open_storage(database=database) as storage:
            plan = storage.get('upgrade_plans',plan_id)
        if not isinstance(plan,dict) or plan.get('id')!=plan_id:
            raise AdminError('No matching recorded upgrade plan exists; no Docker action was performed')
        return plan


    def _no_active_jobs(self, database=None):
        with self._open_storage(database=database) as storage:
            if any(value.get('status') in {'queued','running'} for kind in ('jobs','agent_rollout_jobs') for value in storage.list(kind)):
                raise AdminError('Active platform jobs must finish before upgrading or rolling back')


    def _validate_recovery_journal(self, plan_id):
        value = self.journal or {}
        if (value.get("plan_id") != plan_id or value.get("data_dir") != str(self.data)
                or value.get("project_dir") != str(self.project) or value.get("env_file") != str(self.env_file)
                or not value.get("backup") or not value.get("old_image_id")):
            raise AdminError("Rollback evidence is incomplete or belongs to another deployment")
        if self._compose_digest() != value.get("compose_sha256"):
            raise AdminError("Production Compose changed; rollback will not overwrite an unrelated deployment")
        if self.modular and (not value.get('old_web_image_id') or not value.get('old_web_env_image')):
            raise AdminError('Separated Web rollback requires its recorded previous immutable image')
        if _sha(self.env_file) != value.get("env_sha_current"):
            raise AdminError("Production env changed; rollback will not overwrite concurrent configuration")
        self._sources_unchanged()

    def _backup_recovery_plan(self, plan_id, owner_operation):
        """Use the verified pre-upgrade snapshot only under continuous maintenance.

        This is never a substitute for a readable live active-job refusal, and
        is unavailable once maintenance was released or its marker was lost.
        """
        self._validate_recovery_journal(plan_id)
        if self.journal.get("maintenance_continuous") is not True or not self.marker.is_file():
            raise AdminError("Unreadable live database requires continuous original maintenance; backup cannot prove current quiescence")
        self._maintenance_marker(plan_id, owner_operation)
        verified = self._verified_recovery_backup()
        database = Path(verified["database_path"])
        if self._db_info(database) != self.journal.get("db_before"):
            raise AdminError("Recovery backup differs from the recorded pre-upgrade schema")
        self._no_active_jobs(database)
        plan = self._plan(plan_id, database)
        release = self._release(plan)
        if (release["manifest_sha256"] != self.journal.get("manifest_sha256")
                or release["image"] != self.journal.get("target_image")
                or release["version"] != self.journal.get("target_version")):
            raise AdminError("Backup plan differs from the recorded signed execution")
        self._recovery_container()
        self._emit("verified-backup-recovery-evidence-used", backup_sha256=verified["sha256"])
        return plan

    def _recovery_container(self):
        current = self._container(require_running=False, allow_missing=True)
        if current and current["image_id"] not in {self.journal["old_image_id"], self.journal.get("target_image_id")}:
            raise AdminError("Current control image belongs to another deployment; recovery is refused")
        if self.modular:
            web=self._web_container(require_running=False,allow_missing=True)
            if web and web['image_id'] not in {self.journal.get('old_web_image_id'),self.journal.get('target_web_image_id')}:
                raise AdminError('Current Web image belongs to another deployment; recovery is refused')
        return current

    def _verified_recovery_backup(self):
        backup = self.journal["backup"]
        for field in ("database_path", "manifest_path"):
            if not Path(backup[field]).resolve().is_relative_to(self.journal_path.parent.resolve()):
                raise AdminError("Recovery backup is outside this operation's recorded directory")
        verified = verify_backup(backup["database_path"], backup["manifest_path"])
        if verified["sha256"] != backup.get("sha256"):
            raise AdminError("Recovery backup checksum differs from the recorded execution")
        return verified

    def _recovery_quiescence(self, plan_id, owner_operation):
        try:
            self._no_active_jobs()
        except (StorageUnavailable, DatabaseUnavailable):
            self._backup_recovery_plan(plan_id, owner_operation)

    def _release(self, plan):
        release = read_release(self.release_dir, plan.get("release_id", ""), self.key_file)
        if (plan.get("release") != release or plan.get("manifest_sha256") != release["manifest_sha256"]
                or plan.get("target_version") != release["version"] or plan.get("image") != release["image"]
                or plan.get("web_image") != release.get("web_image")):
            raise AdminError("Recorded plan differs from the verified signed release manifest")
        if self.modular != bool(release.get('web_image')):
            raise AdminError('Signed release packaging differs from the selected bundled/separated layout')
        if release["agent_protocol"] != AGENT_PROTOCOL_VERSION:
            raise AdminError("Release changes the agent protocol")
        return release

    def _sources_unchanged(self):
        if self._compose_digest() != self._compose_sha or _sha(self.env_file) != self._expected_env_sha:
            raise AdminError("Production Compose/env changed during the operation; nothing was overwritten")

    def _configuration(self, image=None, web_image=None):
        document=json.loads(self._compose_command(['config','--format','json'],image=image,web_image=web_image))
        services=document.get('services',{})
        expected_services={'control','web'} if self.modular else {'control'}
        if set(services)!=expected_services:
            raise AdminError('Compose services differ from the reviewed control/Web layout')
        control=services['control']
        if control.get('environment',{}).get('MOTOR_MODE')!='control':
            raise AdminError('Compose control must run MOTOR_MODE=control')
        if self.network_mode=='host':
            environment=control.get('environment',{})
            if (control.get('network_mode')!='host' or control.get('ports')
                    or environment.get('MOTOR_LISTEN_HOST')!=self.environment.get('MOTOR_CONTROL_BIND_IP','127.0.0.1')
                    or str(environment.get('MOTOR_LISTEN_PORT'))!=self.environment.get('MOTOR_CONTROL_PORT','19080')):
                raise AdminError('Host Compose must use the configured control listen address without published ports')
        elif control.get('network_mode')=='host':
            raise AdminError('Host Compose requires the explicit host-network configuration')
        expected=[volume for volume in control.get('volumes',[]) if volume.get('target')=='/var/lib/motor-console']
        if (len(expected)!=1 or expected[0].get('type')!='bind' or expected[0].get('read_only')
                or Path(expected[0].get('source','')).resolve()!=self.data):
            raise AdminError('Control data bind does not match the selected data directory')
        if not document.get('name') or not control.get('image') or image and control['image']!=image:
            raise AdminError('Compose project or requested control image is inconsistent')
        result={'name':document['name'],'image':control['image']}
        if self.modular:
            web=services['web']
            if (str(control.get('environment',{}).get('MOTOR_STATIC_ENABLED')).lower()!='false'
                    or control.get('ports') or not web.get('image') or web_image and web['image']!=web_image):
                raise AdminError('Separated Web/API layout or requested Web image is inconsistent')
            result['web_image']=web['image']
        return result


    def _container(self, *, require_running=True, expected_image_id=None, allow_missing=False):
        identifiers = self._compose_command(["ps", "--all", "--quiet", "control"], timeout=15).split()
        if not identifiers and allow_missing:
            return None
        if len(identifiers) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", identifiers[0]):
            raise AdminError("Exactly one existing production control container is required")
        document = json.loads(self._command(["docker", "inspect", identifiers[0]], timeout=15))
        if not isinstance(document, list) or len(document) != 1:
            raise AdminError("Docker container inspection is ambiguous")
        container = document[0]
        labels = container.get("Config", {}).get("Labels", {})
        if labels.get("com.docker.compose.service") != "control" or labels.get("com.docker.compose.project") != self.project_name:
            raise AdminError("Container does not belong to the expected Compose control service")
        env = dict(value.split("=", 1) for value in container.get("Config", {}).get("Env", []) if "=" in value)
        if env.get("MOTOR_MODE") != "control" or env.get("MOTOR_DATA_DIR") != "/var/lib/motor-console":
            raise AdminError("Inspected container is not the expected control/data layout")
        mounts = [mount for mount in container.get("Mounts", []) if mount.get("Destination") == "/var/lib/motor-console"]
        if (len(mounts) != 1 or mounts[0].get("Type") != "bind" or not mounts[0].get("RW")
                or Path(mounts[0].get("Source", "")).resolve() != self.data):
            raise AdminError("Actual control container does not mount --data-dir read/write")
        if require_running and not container.get("State", {}).get("Running"):
            raise AdminError("Production control container is not running")
        image_id = container.get("Image", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) or expected_image_id and image_id != expected_image_id:
            raise AdminError("Actual control image ID does not match the verified image")
        return {"id": identifiers[0], "image_id": image_id, "running": bool(container.get("State", {}).get("Running"))}

    def _web_container(self, *, require_running=True,expected_image_id=None,allow_missing=False):
        if not self.modular:
            return None
        identifiers=self._compose_command(['ps','--all','--quiet','web'],timeout=15).split()
        if not identifiers and allow_missing:
            return None
        if len(identifiers)!=1 or not re.fullmatch(r'[0-9a-f]{12,64}',identifiers[0]):
            raise AdminError('Exactly one independent Web container is required')
        document=json.loads(self._command(['docker','inspect',identifiers[0]],timeout=15))
        if not isinstance(document,list) or len(document)!=1:
            raise AdminError('Web container inspection is ambiguous')
        value=document[0];labels=value.get('Config',{}).get('Labels',{}) or {}
        if labels.get('com.docker.compose.service')!='web' or labels.get('com.docker.compose.project')!=self.project_name:
            raise AdminError('Web container belongs to another deployment')
        image_id=value.get('Image','')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',image_id) or expected_image_id and image_id!=expected_image_id:
            raise AdminError('Actual Web image differs from the verified release')
        state=value.get('State',{})
        expected_mounts={'/etc/nginx/conf.d/default.conf':'MOTOR_WEB_CONFIG_FILE','/run/motor-tls':'MOTOR_WEB_TLS_DIR'}
        for destination,setting in expected_mounts.items():
            expected=self.environment.get(setting,'')
            matches=[mount for mount in value.get('Mounts',[]) if mount.get('Destination')==destination]
            if (not Path(expected).is_absolute() or len(matches)!=1 or matches[0].get('Type')!='bind'
                    or matches[0].get('RW') or Path(matches[0].get('Source','')).resolve()!=Path(expected).resolve()):
                raise AdminError('Actual Web configuration/TLS mounts differ from the reviewed deployment')
        if require_running and (not state.get('Running') or state.get('Health',{}).get('Status')!='healthy'):
            raise AdminError('Independent Web has not passed its Docker health check')
        return {'id':identifiers[0],'image_id':image_id,'running':bool(state.get('Running'))}

    def _image(self, reference, *, digest=None, expected_version=None, role=None):
        documents=json.loads(self._command(['docker','image','inspect',reference]))
        if not isinstance(documents,list) or len(documents)!=1:
            raise AdminError('Docker image inspection is ambiguous')
        image=documents[0]
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',image.get('Id','')):
            raise AdminError('Inspected image has no immutable image ID')
        if reference.startswith('sha256:') and image['Id']!=reference:
            raise AdminError('Cached image ID differs from its recorded reference')
        if digest and not any(value.endswith('@'+digest) for value in image.get('RepoDigests',[])):
            raise AdminError('Pulled image digest does not match the signed release')
        labels=image.get('Config',{}).get('Labels',{}) or {}
        if expected_version and (labels.get('io.motor.product')!=PRODUCT_ID or labels.get('org.opencontainers.image.version')!=expected_version):
            raise AdminError('Image product/version labels differ from the signed release')
        if role and labels.get('io.motor.role') not in (None,role):
            raise AdminError('Image component role is inconsistent')
        return {'id':image['Id'],'repo_digests':image.get('RepoDigests',[])}


    def _new_marker(self, plan_id, *, created_at=None, previous_operation=None):
        value = {"plan_id": plan_id, "operation_id": self.operation_id, "created_at": created_at or _now(),
                 "purpose": "host-control-upgrade"}
        if previous_operation is not None:
            value["previous_operation_id"] = previous_operation
        try:
            with self.marker.open("x", encoding="utf-8") as target:
                self.marker.chmod(0o600)
                json.dump(value, target)
                target.flush()
                os.fsync(target.fileno())
        except FileExistsError as exc:
            raise AdminError("Another maintenance marker exists; it was not adopted or removed") from exc
        if self.journal is not None:
            started = self.journal.get("maintenance_started_at")
            prior = self.journal.get("maintenance_continuous")
            self.journal.setdefault("maintenance_started_at", value["created_at"])
            self.journal["maintenance_continuous"] = prior is None and not bool(started)
            self._emit("maintenance-entered", continuous=self.journal["maintenance_continuous"])

    def _clear_marker(self, plan_id):
        if not self.marker.exists():
            raise AdminError("Maintenance marker disappeared unexpectedly")
        value = json.loads(self.marker.read_text(encoding="utf-8"))
        if value.get("plan_id") != plan_id or value.get("operation_id") != self.operation_id:
            raise AdminError("Maintenance marker belongs to another operation; it was not removed")
        if self.journal is not None:
            self.journal["maintenance_continuous"] = False
            self._emit("maintenance-releasing")
        self.marker.unlink()

    def _maintenance_marker(self, plan_id, expected_operation=None):
        if not self.marker.is_file():
            return None
        value = json.loads(self.marker.read_text(encoding="utf-8"))
        expected = self.journal["operation_id"]
        if expected_operation is not None and expected_operation != expected:
            raise AdminError("Maintenance journal owner differs from the requested recovery")
        if value.get("plan_id") != plan_id:
            raise AdminError("Existing maintenance marker belongs to a different operation")
        if self.journal.get("maintenance_continuous") is True and value.get("created_at") != self.journal.get("maintenance_started_at"):
            raise AdminError("Original maintenance marker creation identity changed")
        intent = self.journal.get("maintenance_transfer")
        if isinstance(intent, dict) and intent.get("phase") == "prepared":
            if (intent.get("plan_id") != plan_id or intent.get("from_operation") != expected
                    or not re.fullmatch(r"[0-9a-f]{32}", str(intent.get("to_operation", "")))
                    or not isinstance(intent.get("marker_existed"), bool)
                    or not isinstance(intent.get("marker_created_at"), str)
                    or value.get("created_at") != intent["marker_created_at"]):
                raise AdminError("Pending maintenance transfer identity does not match its marker")
            before = intent["marker_existed"] and value.get("operation_id") == expected
            after = (value.get("operation_id") == intent["to_operation"]
                     and value.get("previous_operation_id") == expected)
            if before or after:
                return value
        elif value.get("operation_id") == expected:
            return value
        raise AdminError("Existing maintenance marker belongs to a different operation")

    def _commit_marker_transfer(self, intent, *, reconciled=False):
        previous = self.journal.setdefault("previous_operations", [])
        if intent["from_operation"] not in previous:
            previous.append(intent["from_operation"])
        self.journal["operation_id"] = intent["to_operation"]
        intent.update(phase="committed", committed_at=_now())
        self._emit("maintenance-owner-transfer-reconciled" if reconciled else "maintenance-owner-transferred",
                   from_operation=intent["from_operation"], to_operation=intent["to_operation"],
                   marker_created_at=intent["marker_created_at"])

    def _takeover_marker(self, plan_id, previous_operation):
        if previous_operation != self.journal["operation_id"]:
            raise AdminError("Maintenance journal changed before owner transfer")
        value = self._maintenance_marker(plan_id)
        pending = self.journal.get("maintenance_transfer")
        if isinstance(pending, dict) and pending.get("phase") == "prepared":
            if value and value.get("operation_id") == pending["to_operation"]:
                self._commit_marker_transfer(pending, reconciled=True)
            else:
                if value is None:
                    self.journal["maintenance_continuous"] = False
                pending.update(phase="cancelled", cancelled_at=_now())
                self._emit("maintenance-owner-transfer-cancelled", from_operation=pending["from_operation"],
                           to_operation=pending["to_operation"])
        previous = self.journal["operation_id"]
        created_at = value["created_at"] if value else _now()
        intent = {"phase": "prepared", "plan_id": plan_id, "from_operation": previous,
                  "to_operation": self.operation_id, "marker_created_at": created_at,
                  "marker_existed": value is not None, "prepared_at": _now()}
        self.journal["maintenance_transfer"] = intent
        if value is None:
            self.journal["maintenance_continuous"] = False
        self._emit("maintenance-owner-transfer-prepared", from_operation=previous,
                   to_operation=self.operation_id, marker_created_at=created_at,
                   marker_existed=value is not None)  # Durable intent before changing the marker.
        if value:
            value.update(operation_id=self.operation_id, previous_operation_id=previous)
            _atomic_json(self.marker, value)  # No moment without maintenance during transfer.
        else:
            self._new_marker(plan_id, created_at=created_at, previous_operation=previous)
        self._commit_marker_transfer(intent)

    def _record(self, plan_id, status):
        if status in {'succeeded','rolled_back','failed','rollback_failed'}:
            # Control may be running. Only it may consume this signed local
            # result and update its database/audit; no second writer is opened.
            self.journal.update(status=status,result_pending_sync=True)
            _atomic_json(self.journal_path,self.journal)
            seed = self.environment.get('MOTOR_SECRET_KEY') or self.environment.get('MOTOR_CONSOLE_TOKEN')
            if not seed:
                configured = self.environment.get('MOTOR_SECRET_KEY_HOST_FILE') or self.environment.get('MOTOR_SECRET_KEY_FILE')
                if configured:
                    prefix='/run/motor-secrets/'
                    key_path = self.data/'secrets'/configured[len(prefix):] if configured.startswith(prefix) else Path(configured)
                    if not key_path.is_file() or key_path.is_symlink() or key_path.stat().st_size>8192:
                        raise AdminError('Persistent receipt signing key is unavailable')
                    seed=key_path.read_text(encoding='utf-8').strip()
            self.journal['result_receipt'] = write_host_result(self.data,self.journal_path,seed=seed)
            # Do not rewrite the signed journal here: its SHA binds the result.
            return
        with self._open_storage(write=True) as storage, storage.unit_of_work(write=True) as unit:
            plan=unit.resources.get('upgrade_plans',plan_id)
            if not plan:
                raise AdminError('Recorded plan disappeared; no new plan was invented')
            plan.update(status=status,updated_at=_now(),host_execution={key:value for key,value in self.journal.items() if key!='events'})
            unit.resources.put('upgrade_plans',plan)
            storage.audit('平台升级',plan_id,status,'Host-only control operation; agents and Motor workloads untouched',actor='host-cli',request_id=self.operation_id,connection=unit)


    def _write_image_env(self, image, web_image=None):
        self._sources_unchanged()
        lines=self.env_file.read_text(encoding='utf-8').splitlines(keepends=True)
        replacements={'MOTOR_CONSOLE_IMAGE':image}
        if self.modular:
            if not web_image:
                raise AdminError('A separated Web upgrade must retain an explicit Web image')
            replacements['MOTOR_WEB_IMAGE']=web_image
        for name,value in replacements.items():
            matches=[index for index,line in enumerate(lines) if re.match(r'\s*(?:export\s+)?'+name+r'\s*=',line)]
            if len(matches)!=1 or any(character in value for character in '\r\n\"\''):
                raise AdminError('Image must have exactly one safe persistent environment assignment')
            lines[matches[0]]=name+'='+value+'\n'
        temporary=self.env_file.with_name(self.env_file.name+'.'+self.operation_id+'.part')
        try:
            with temporary.open('x',encoding='utf-8') as output:
                temporary.chmod(0o600);output.write(''.join(lines));output.flush();os.fsync(output.fileno())
            self._sources_unchanged()
            os.replace(temporary,self.env_file)
        finally:
            temporary.unlink(missing_ok=True)
        self._expected_env_sha=_sha(self.env_file)
        self.journal['env_sha_current']=self._expected_env_sha
        self._emit('persistent-images-updated',image=image,web_image=web_image)


    def _stop(self):
        self._sources_unchanged()
        current=self._container(require_running=False,allow_missing=True)
        web = self._web_container(require_running=False,allow_missing=True) if self.modular else None
        if current is not None or web is not None:
            self._compose_command(['stop','--timeout','45',*(['web','control'] if self.modular else ['control'])])
        current=self._container(require_running=False,allow_missing=True)
        web=self._web_container(require_running=False,allow_missing=True) if self.modular else None
        if current is not None and current['running'] or web is not None and web['running']:
            raise AdminError('Control/Web did not stop; database restore is forbidden')
        self._emit('control-web-stopped' if self.modular else 'control-stopped')


    def _start(self, image, web_image=None):
        self._sources_unchanged()
        self._configuration(image,web_image)
        self._emit('control-web-starting' if self.modular else 'control-starting',image=image,web_image=web_image)
        self._compose_command(['up','-d','--no-deps','--no-build','--pull','never','--force-recreate',*(['control','web'] if self.modular else ['control'])],image=image,web_image=web_image)


    def _wait_health(self, version, image_id, web_image_id=None):
        deadline = self.clock() + self.health_timeout
        last_error = "No successful health response"
        for _attempt in range(120):
            try:
                container = self._container(expected_image_id=image_id)
                health = self.probe("/health")
                if (health.get("_http_status", 200) == 200 and health.get("status") == "ok"
                        and health.get("product_id") == PRODUCT_ID
                        and health.get("mode") == "control" and health.get("version") == version):
                    if self.modular:
                        web=self._web_container(expected_image_id=web_image_id)
                        self._emit('web-health-verified',container_id=web['id'],image_id=web['image_id'])
                    self._emit("control-health-verified", version=version, container_id=container["id"], image_id=image_id)
                    return
                last_error = "Control health/version does not match the recorded release"
            except Exception as exc:
                last_error = str(redact(str(exc)))
            if self.clock() >= deadline:
                break
            self.sleep(1)
        raise AdminError(last_error)

    def _ready(self):
        value = self.probe("/ready")
        if value.get("_http_status", 200) != 200 or value.get("status") != "ready" or value.get("mode") != "control":
            raise AdminError("Control readiness failed after leaving maintenance")
        self._emit("control-ready-verified")

    def _offline_restore(self):
        verified = self._verified_recovery_backup()
        try:
            failed_shape = self._db_info()
        except Exception:
            failed_shape = None
        try:
            failure = self._snapshot(self.journal_path.parent / "failed-state", reason="before-offline-database-rollback")
        except Exception as exc:
            if database_options(self.environment,self.data)["backend"] != "sqlite":
                raise AdminError("The PostgreSQL failed-state snapshot could not be preserved; maintenance remains active and no restore was attempted") from exc
            # All writers are stopped and the control lease is held. Preserve
            # the complete physical state, including any WAL, even if corrupt.
            directory = self.journal_path.parent / "failed-state" / ("physical-" + uuid4().hex)
            directory.mkdir(parents=True, mode=0o700)
            files = []
            for suffix in ("", "-wal", "-shm"):
                source_path = Path(str(self.database) + suffix)
                if source_path.is_file():
                    target = directory / source_path.name
                    with source_path.open("rb") as source, target.open("xb") as output:
                        target.chmod(0o600)
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    files.append({"name": target.name, "sha256": _sha(target), "size_bytes": target.stat().st_size})
            failure = {"format": "stopped-physical-database-evidence", "files": files, "directory": str(directory),
                       "logical_snapshot_error": str(redact(str(exc))), "valid_sqlite_backup": False}
            _atomic_json(directory / "evidence.json", failure)
        self.journal.setdefault("failed_state_snapshots", []).append(failure)
        self._emit("failed-database-preserved", snapshot=failure.get("database_path", failure.get("directory")))
        if (failed_shape and failed_shape["schema_version"] == self.journal["db_before"]["schema_version"]
                and failed_shape["schema_sha256"] == self.journal["db_before"]["schema_sha256"]):
            self._emit("database-schema-unchanged", restored=False)
            return
        restore_offline(self.data,verified['database_path'],verified['manifest_path'],
                        **database_options(self.environment,self.data))
        if self._db_info() != self.journal["db_before"]:
            raise AdminError("Offline restored schema does not match the recorded pre-upgrade database")
        self._emit("database-restored-offline", backup_sha256=verified["sha256"])

    def _recover(self, plan_id):
        if self.journal.get("target_start_attempted"):
            self._validate_recovery_journal(plan_id)
            self._verified_recovery_backup()
            self._recovery_container()
            self._recovery_quiescence(plan_id, self.operation_id)
        if not self.marker.exists():
            self._new_marker(plan_id)
        else:
            owner = json.loads(self.marker.read_text(encoding="utf-8"))
            if owner.get("plan_id") != plan_id or owner.get("operation_id") != self.operation_id:
                raise AdminError("Cannot recover while another operation owns the maintenance marker")
        self._stop()
        with ProcessLease(self.data, name="control"):
            if self.journal.get("target_start_attempted"):
                self._recovery_quiescence(plan_id, self.operation_id)
            if self.journal.get("target_start_attempted") and self.journal.get("backup"):
                self._offline_restore()
            self.journal["status"] = "rolling_back"
            self._record(plan_id, "rolling_back")
            old = self.journal["old_image_id"]
            self._image(old)
            if self.modular:
                self._image(self.journal['old_web_image_id'])
            self._write_image_env(self.journal["old_env_image"],self.journal.get("old_web_env_image"))
        self._start(old,self.journal.get("old_web_image_id"))
        self._wait_health(self.journal["old_version"],old,self.journal.get("old_web_image_id"))
        self._clear_marker(plan_id)
        self._ready()
        self.journal["status"] = "rolled_back"
        self._emit("rolled-back")
        self._record(plan_id, "rolled_back")

    def apply(self, plan_id):
        plan = self._plan(plan_id)
        if (plan.get("status") != "planned" or plan.get("demo") or not plan.get("checks")
                or any(check.get("status") != "pass" for check in plan["checks"])):
            raise AdminError("Only a recorded, passing, non-demo planned upgrade may be applied")
        release = self._release(plan)
        before = self._db_info()
        if not release["db_schema_min"] <= before["schema_version"] <= release["db_schema_max"]:
            raise AdminError("Current database schema is outside the signed compatibility range")
        if version_tuple(release["version"]) <= version_tuple(plan.get("current_version")):
            raise AdminError("Normal upgrade requires a newer signed release")
        self._no_active_jobs()
        with ProcessLease(self.data, name="console-admin"):
            if self.marker.exists():
                raise AdminError("An existing maintenance operation must be resolved first")
            self.journal_path = self.data / "upgrades" / plan_id / "execution.json"
            if self.journal_path.exists():
                raise AdminError("This plan already has host execution evidence; use explicit rollback or a new plan")
            configuration = self._configuration()
            self.project_name = configuration["name"]
            old_container = self._container()
            old = self._image(old_container["image_id"])
            old_web_container=self._web_container() if self.modular else None
            old_web=self._image(old_web_container['image_id']) if old_web_container else None
            old_web_env=self.environment.get('MOTOR_WEB_IMAGE') if self.modular else None
            if self.modular and (not old_web_env or not re.fullmatch(r'(?:sha256:[0-9a-f]{64}|[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64})',old_web_env)
                    or configuration.get('web_image')!=old_web_env):
                raise AdminError('Persistent Web image differs from the running deployment configuration')
            health = self.probe("/health")
            if (health.get("product_id") != PRODUCT_ID or health.get("status") != "ok"
                    or health.get("mode") != "control" or health.get("version") != plan["current_version"]):
                raise AdminError("Running control version differs from the recorded plan")
            old_env_image = self.environment.get("MOTOR_CONSOLE_IMAGE", "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", old_env_image) or configuration["image"] != old_env_image:
                raise AdminError("Persistent production image differs from the running deployment configuration")
            self.journal = {"plan_id": plan_id, "operation_id": self.operation_id, "status": "preparing",
                            "project_dir": str(self.project), "data_dir": str(self.data), "env_file": str(self.env_file),
                            "compose_sha256": self._compose_sha, "env_sha_before": self._expected_env_sha,
                            "env_sha_current": self._expected_env_sha, "old_env_image": old_env_image,
                            "old_image_id": old["id"], "old_repo_digests": old["repo_digests"],
                            "old_version": plan["current_version"], "target_version": release["version"],
                            "target_image": release["image"], "manifest_sha256": release["manifest_sha256"],
                            "project_name": self.project_name, "db_before": before, "created_at": _now()}
            if self.modular:
                self.journal.update(old_web_env_image=old_web_env,old_web_image_id=old_web['id'],
                    old_web_repo_digests=old_web['repo_digests'],target_web_image=release['web_image'])
            self._emit("preflight-verified")
            stopped = False
            try:
                pull_services = [name for name,reference in [('control',release['image']),
                    *([('web',release['web_image'])] if self.modular else [])] if '@' in reference]
                if pull_services:
                    self._compose_command(['pull',*pull_services],image=release['image'],
                        web_image=release.get('web_image'),timeout=1200)
                target = self._image(release["image"],digest=release["image"].split("@",1)[1] if '@' in release['image'] else None,expected_version=release['version'],role='api' if self.modular else None)
                if self.modular:
                    target_web=self._image(release['web_image'],digest=release['web_image'].split('@',1)[1] if '@' in release['web_image'] else None,expected_version=release['version'],role='web')
                    self.journal['target_web_image_id']=target_web['id']
                self.journal["target_image_id"] = target["id"]
                self._emit("target-image-verified", image_id=target["id"])
                self._sources_unchanged()
                self._release(self._plan(plan_id))
                self._new_marker(plan_id)
                self._no_active_jobs()
                stopped = True  # A failed stop may still have stopped the old container.
                self._stop()
                with ProcessLease(self.data, name="control"):
                    self._no_active_jobs()
                    if self._db_info() != before:
                        raise AdminError("Database schema changed since the plan was checked")
                    backup = self._snapshot(self.journal_path.parent / "backups", reason="before-control-upgrade")
                    verify_backup(backup["database_path"], backup["manifest_path"])
                    self.journal.update(backup=backup, status="applying")
                    self._emit("database-backup-verified", backup_sha256=backup["sha256"])
                    self._record(plan_id, "applying")
                self.journal["target_start_attempted"] = True
                self._emit("target-start-attempted")
                self._start(release["image"],release.get("web_image"))
                self._wait_health(release["version"],target["id"],self.journal.get("target_web_image_id"))
                current = self._db_info()
                if not release["db_schema_min"] <= current["schema_version"] <= release["db_schema_max"]:
                    raise AdminError("Target database schema is outside its signed compatibility range")
                self._write_image_env(release["image"],release.get("web_image"))
                self._clear_marker(plan_id)
                self._ready()
                self.journal["status"] = "succeeded"
                self._emit("upgrade-succeeded")
                self._record(plan_id, "succeeded")
            except Exception as exc:
                self.journal["last_error"] = str(redact(str(exc)))
                self._emit("upgrade-failed", detail=self.journal["last_error"])
                if stopped:
                    try:
                        self._recover(plan_id)
                    except Exception as recovery:
                        self.journal.update(status="rollback_failed", rollback_error=str(redact(str(recovery))))
                        if not self.marker.exists():
                            self._new_marker(plan_id)
                        self._emit("rollback-failed", detail=self.journal["rollback_error"])
                        try:
                            self._record(plan_id, "rollback_failed")
                        except Exception:
                            pass  # The private host journal remains the durable failure evidence.
                        raise AdminError("Upgrade failed and automatic rollback failed; preserve the maintenance marker and execution journal") from recovery
                else:
                    self.journal["status"] = "failed"
                    self._emit("upgrade-aborted-before-stop")
                    if self.marker.exists():
                        self._clear_marker(plan_id)
                    self._record(plan_id, "failed")
                    raise AdminError("Upgrade was aborted before stopping the old control") from exc
            return dict(self.journal)

    def rollback(self, plan_id):
        self._check_plan_id(plan_id)
        with ProcessLease(self.data, name="console-admin"):
            self.journal_path = self.data / "upgrades" / plan_id / "execution.json"
            if not self.journal_path.is_file():
                self._plan(plan_id)  # Preserve the explicit unknown-plan refusal.
                raise AdminError("No recorded host execution exists for this plan")
            self.journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
            self._validate_recovery_journal(plan_id)
            self._verified_recovery_backup()
            configuration = self._configuration()
            self.project_name = self.journal["project_name"]
            if configuration["name"] != self.project_name:
                raise AdminError("Production Compose changed; rollback will not overwrite an unrelated deployment")
            try:
                plan = self._plan(plan_id)
            except (StorageUnavailable, DatabaseUnavailable):
                plan = self._backup_recovery_plan(plan_id, self.journal["operation_id"])
                status = self.journal.get("status")
            else:
                status = plan.get("status")
            if status not in {"applying", "succeeded", "rolling_back", "rollback_failed"}:
                raise AdminError("Recorded plan is not in a rollback-capable execution state")
            self._release(plan)
            self._recovery_quiescence(plan_id, self.journal["operation_id"])
            self._recovery_container()
            previous = self.journal["operation_id"]
            self._takeover_marker(plan_id, previous)
            self._emit("explicit-rollback-requested")
            try:
                self._recover(plan_id)
            except Exception as exc:
                self.journal.update(status="rollback_failed", rollback_error=str(redact(str(exc))))
                if not self.marker.exists():
                    self._new_marker(plan_id)
                self._emit("explicit-rollback-failed", detail=self.journal["rollback_error"])
                try:
                    self._record(plan_id, "rollback_failed")
                except Exception:
                    pass
                raise AdminError("Explicit rollback failed; maintenance and failure evidence are retained") from exc
            return dict(self.journal)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("apply", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--project-dir", required=True)
        command.add_argument("--data-dir", required=True)
        command.add_argument("--plan-id", required=True)
        command.add_argument("--env-file")
    args = parser.parse_args(argv)
    try:
        admin = ConsoleAdmin(args.project_dir, args.data_dir, env_file=args.env_file)
        if database_options(admin.environment,admin.data)['backend']=='sqlite' and not sqlite_wal_fix_available():
            raise AdminError("Host Python SQLite must include the WAL-reset fix (3.51.3+, 3.50.7 or 3.44.6 backport); no upgrade was attempted")
        result = getattr(admin, args.command)(args.plan_id)
        print(json.dumps({"plan_id": args.plan_id, "status": result["status"], "journal": str(admin.journal_path)}, ensure_ascii=False))
        return 0 if result["status"] == "succeeded" or args.command == "rollback" and result["status"] == "rolled_back" else 2
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": redact(str(exc))}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
