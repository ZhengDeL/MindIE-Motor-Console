"""Backend-neutral persistence facade. Motor remains runtime authority.

Repositories and the unit of work own persistence. Domains never receive a
SQL connection; database drivers, SQL, migrations and leases stay in plugins.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .observability import actor_identity, context_fields, redact, timestamp_filter
from .ports.storage import SchemaVersionError, RevisionConflict
from .storage.schema import APPLICATION_ID, SCHEMA_VERSION
from .storage.registry import open_database


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str = "") -> str:
    return prefix + uuid4().hex


class Store:
    def __init__(self, directory: str | Path, *, backend: str = "sqlite", database_url: str | None = None,
                 read_only=False, database_path=None, initialize=True):
        self.directory = Path(directory).resolve()
        self._database = open_database(self.directory, backend=backend, database_url=database_url,
            read_only=read_only,database_path=database_path,initialize=initialize)
        self.path = self._database.path if backend == "sqlite" else None
        self.last_migration_backup = getattr(self._database,'last_migration_backup',None)

    @property
    def schema_version(self):
        return self._database.schema_version

    @contextmanager
    def unit_of_work(self, *, write=False):
        with self._database.unit_of_work(write=write) as unit:
            yield unit

    def health(self):
        return self._database.health()

    def storage_info(self):
        return self._database.info()

    def schema_info(self):
        return self._database.schema_info()

    def list(self, kind):
        with self.unit_of_work() as unit:
            return unit.resources.list(kind)

    def get(self, kind, identifier):
        with self.unit_of_work() as unit:
            return unit.resources.get(kind,identifier)

    def page(self, kind, *, limit=100, offset=0, filters=None):
        with self.unit_of_work() as unit:
            return unit.resources.page(kind, limit=limit, offset=offset, filters=filters)

    @staticmethod
    def _put(unit, kind, value, *, revision=False):
        unit.resources.put(kind,value,revision=revision)

    def put(self, kind, value, *, revision=False):
        with self.unit_of_work(write=True) as unit:
            self._put(unit,kind,value,revision=revision)

    def delete(self, kind, identifier):
        with self.unit_of_work(write=True) as unit:
            unit.resources.delete(kind,identifier)

    def put_many(self, records):
        with self.unit_of_work(write=True) as unit:
            for kind,value in records:
                self._put(unit,kind,value)

    def mutate_many_audited(self, records, *, action, target, status="success", detail="",
                            metadata=None, deletes=None, revision_keys=None):
        revision_keys = revision_keys or set()
        with self.unit_of_work(write=True) as unit:
            for kind,value in records:
                self._put(unit,kind,value,revision=(kind,value['id']) in revision_keys)
            for kind,identifier in deletes or []:
                unit.resources.delete(kind,identifier)
            return self.audit(action,target,status,detail,metadata=metadata,connection=unit)

    def mutate_audited(self, kind, identifier, value, *, action, detail="", metadata=None,
                       revision=False, target=None, status="success", expected_revision=None):
        if value is not None and value.get('id') != identifier:
            raise ValueError('Audited resource identity does not match the mutation target')
        with self.unit_of_work(write=True) as unit:
            if expected_revision is not None:
                if type(expected_revision) is not int or expected_revision < 1:
                    raise ValueError('Expected configuration revision must be a positive integer')
                previous = unit.resources.get(kind,identifier)
                if previous is None or int(previous.get('config_revision',1)) != expected_revision:
                    raise RevisionConflict('Configuration changed; reload and compare before saving')
                if value is None:
                    unit.resources.delete(kind,identifier)
                elif not unit.resources.compare_and_replace(kind,identifier,previous,value,revision=revision):
                    raise RevisionConflict('Configuration changed while saving')
            elif value is not None:
                self._put(unit,kind,value,revision=revision)
            else:
                unit.resources.delete(kind,identifier)
            return self.audit(action,target or identifier,status,detail,metadata=metadata,connection=unit)

    def compare_and_mutate_audited(self, kind, identifier, expected, value, *, action,
                                   target, detail="", metadata=None, guards=None):
        if value.get('id') != identifier or expected.get('id') != identifier:
            raise ValueError('Resource identity does not match the mutation target')
        with self.unit_of_work(write=True) as unit:
            for guard_kind,guard_id,guard_value in guards or []:
                if unit.resources.get(guard_kind,guard_id) != guard_value:
                    return False
            if not unit.resources.compare_and_replace(kind,identifier,expected,value):
                return False
            self.audit(action,target,'success',detail,metadata=metadata,connection=unit)
            return True

    def revisions(self, service_id):
        with self.unit_of_work() as unit:
            return unit.resources.history(service_id)

    def audit(self, action, target, status, detail="", *, actor=None, request_id=None,
              metadata=None, connection=None):
        context = context_fields()
        value = redact({'id':uid('evt_'),'time':now(),'action':action,'target':target,
            'status':status,'detail':detail[:4000],
            'actor':actor_identity(actor if actor is not None else context['actor']),
            'request_id':request_id if request_id is not None else context['request_id']})
        if metadata is not None:
            value['metadata'] = redact(metadata)
        with (self.unit_of_work(write=True) if connection is None else nullcontext(connection)) as unit:
            return unit.audit.append(value)

    def audit_items(self, limit=250):
        return self.query_audit(limit=limit)['items']

    def query_audit(self, *, limit=100,offset=0,**filters):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('Audit page must use limit 1..1000 and offset 0..100000')
        with self.unit_of_work() as unit:
            return unit.audit.query(limit=limit,offset=offset,**filters)

    def prune_audit(self, *, before,keep_latest=1000,dry_run=True):
        cutoff = timestamp_filter(before)
        if cutoff is None or type(keep_latest) is not int or keep_latest < 1 or type(dry_run) is not bool:
            raise ValueError('Audit retention requires a timezone cutoff, keep_latest >= 1 and a boolean dry_run')
        if cutoff >= now():
            raise ValueError('Audit retention cutoff must be in the past')
        with self.unit_of_work(write=True) as unit:
            return unit.audit.prune(before=cutoff,keep_latest=keep_latest,dry_run=dry_run)

    def backup(self, destination_dir, *, reason="manual"):
        return self._database.backup(destination_dir,reason=reason)

    def restore_snapshot(self, database_path, manifest_path=None):
        return self._database.restore_snapshot(database_path,manifest_path)

    def close(self):
        self._database.close()

    def __enter__(self):
        return self

    def __exit__(self,*_exc):
        self.close()
