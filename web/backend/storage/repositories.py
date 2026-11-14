"""Resource, revision and audit repositories using portable Core expressions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import uuid4

from sqlalchemy import select, insert, update, delete, func, and_, or_, cast
from sqlalchemy.dialects.postgresql import JSONB

from ..ports.storage import StorageError, RevisionConflict
from ..audit_presentation import capture_audit_names, audit_filter_values, project_audit_items
from ..observability import redact, timestamp_filter
from .accounts import AccountRepository, OIDCRepository
from .schema import resources, revisions, audit


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _json_field(connection, column, *path):
    if connection.dialect.name == 'sqlite':
        return func.json_extract(column, '$.'+'.'.join(path))
    value = cast(column, JSONB)
    for part in path[:-1]:
        value = value[part]
    return value[path[-1]].astext


class ResourceRepository:
    def __init__(self, context):
        self._context, self._connection = context, context._connection

    def list(self, kind):
        ordering = [resources.c.id.desc()] if self._context.schema_version == 1 else [resources.c.created_order.desc(),resources.c.id.desc()]
        rows = self._connection.execute(select(resources.c.body).where(resources.c.kind==kind).order_by(*ordering))
        return [json.loads(row[0]) for row in rows]

    def get(self, kind, identifier):
        value = self._connection.execute(select(resources.c.body).where(
            resources.c.kind==kind,resources.c.id==identifier)).scalar_one_or_none()
        return json.loads(value) if value is not None else None

    def page(self, kind, *, limit=100, offset=0, filters=None):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError('Resource pages require limit 1..100 and offset 0..10000')
        filters = {} if filters is None else filters
        if (not isinstance(filters, dict) or set(filters)-{'node_id','action','reference'}
                or any(not isinstance(value,str) or len(value)>1024 for value in filters.values())):
            raise ValueError('Resource page filters must use supported exact string fields')
        clauses = [resources.c.kind == kind, *(_json_field(self._connection,resources.c.body,key) == value
                                              for key,value in filters.items())]
        created = _json_field(self._connection, resources.c.body, 'created_at')
        total = self._connection.execute(select(func.count()).select_from(resources)
            .where(*clauses)).scalar_one()
        rows = self._connection.execute(select(resources.c.body).where(*clauses)
            .order_by(func.coalesce(created, '').desc(), resources.c.id.desc()).limit(limit).offset(offset))
        return {'items':[json.loads(row[0]) for row in rows], 'total':total, 'limit':limit,
                'offset':offset, 'has_more':offset+limit < total}

    def _revision(self, kind, value, previous, enabled):
        if not enabled:
            return
        if kind == 'services':
            value['config_revision'] = (int(previous.get('config_revision',1)) if previous else 0)+1
        record = {'id':'rev_'+uuid4().hex, 'created_at':datetime.now(timezone.utc).isoformat(),
                  **{key:value[key] for key in ('user_config','env','placements','runtime')},
                  'extra_manifests':value.get('extra_manifests',[])}
        if 'config_revision' in value:
            record['config_revision'] = value['config_revision']
        self._connection.execute(insert(revisions).values(id=record['id'],service_id=value['id'],
            created_at=record['created_at'],body=_dump(record)))

    def put(self, kind, value, *, revision=False):
        self._context.require_write()
        previous = self.get(kind,value['id'])
        if kind == 'services' and previous is not None and not revision:
            current_revision = int(previous.get('config_revision',1))
            supplied_revision = value.get('config_revision')
            config_fields = ('user_config','env','placements','runtime','extra_manifests')
            if ((supplied_revision is not None and int(supplied_revision) < current_revision)
                    or supplied_revision is None and current_revision > 1
                    and any(value.get(key) != previous.get(key) for key in config_fields)):
                raise RevisionConflict('An old observation cannot replace a newer service configuration')
            value['config_revision'] = current_revision
        self._revision(kind,value,previous,revision)
        if previous is not None:
            self._connection.execute(update(resources).where(resources.c.kind==kind,resources.c.id==value['id']).values(body=_dump(value)))
        else:
            order = self._connection.execute(select(func.coalesce(func.max(resources.c.created_order),0))).scalar_one()+1
            self._connection.execute(insert(resources).values(kind=kind,id=value['id'],body=_dump(value),created_order=order))

    def delete(self, kind, identifier):
        self._context.require_write()
        self._connection.execute(delete(resources).where(resources.c.kind==kind,resources.c.id==identifier))

    def compare_and_replace(self, kind, identifier, expected, value, *, revision=False):
        self._context.require_write()
        row = self._connection.execute(select(resources.c.body).where(resources.c.kind==kind,resources.c.id==identifier)).first()
        if row is None or json.loads(row[0]) != expected:
            return False
        # The SQL predicate, not only the process guard, protects the observed
        # body. The audit and revision are committed in this same transaction.
        if revision:
            if kind == 'services':
                value['config_revision'] = int(expected.get('config_revision',1))+1
        changed = self._connection.execute(update(resources).where(resources.c.kind==kind,
            resources.c.id==identifier,resources.c.body==row[0]).values(body=_dump(value))).rowcount
        if changed != 1:
            return False
        if revision:
            # _revision also computes the same next configuration revision.
            self._revision(kind,value,expected,True)
        return True

    def history(self, service_id):
        rows = self._connection.execute(select(revisions.c.body).where(revisions.c.service_id==service_id)
            .order_by(revisions.c.created_at.desc(),revisions.c.id.desc()))
        return [json.loads(row[0]) for row in rows]


class AuditRepository:
    def __init__(self, context):
        self._context, self._connection = context, context._connection

    def append(self, value, *, target_snapshot=None):
        self._context.require_write()
        capture_audit_names(self._context,value,target_snapshot=target_snapshot)
        self._connection.execute(insert(audit).values(id=value['id'],time=value['time'],body=_dump(value),
            actor=value['actor'],request_id=value['request_id']))
        return value

    def _json(self, *path):
        return _json_field(self._connection, audit.c.body, *path)

    def query(self, *, limit=100,offset=0,actor=None,actor_username=None,request_id=None,
              action=None,status=None,since=None,until=None):
        actors, actions = audit_filter_values(self._context,actor_username=actor_username,action=action)
        clauses = []
        if actor is not None:
            clauses.append(audit.c.actor==actor)
        if actors is not None:
            clauses.append(or_(audit.c.actor.in_(actors),self._json('actor_snapshot','username')==actor_username))
        if request_id is not None:
            clauses.append(audit.c.request_id==request_id)
        if actions is not None:
            clauses.append(self._json('action').in_(actions))
        if status is not None:
            clauses.append(self._json('status')==status)
        if (value:=timestamp_filter(since)) is not None:
            clauses.append(audit.c.time>=value)
        if (value:=timestamp_filter(until)) is not None:
            clauses.append(audit.c.time<=value)
        total = self._connection.execute(select(func.count()).select_from(audit).where(*clauses)).scalar_one()
        rows = self._connection.execute(select(audit.c.body).where(*clauses)
            .order_by(audit.c.time.desc(),audit.c.id.desc()).limit(limit).offset(offset)).all()
        items = project_audit_items(self._context,[redact(json.loads(row[0])) for row in rows])
        return {'items':items,'total':total,'limit':limit,'offset':offset,'has_more':offset+len(rows)<total}

    def prune(self, *, before,keep_latest=1000,dry_run=True):
        self._context.require_write()
        keep = select(audit.c.id).order_by(audit.c.time.desc(),audit.c.id.desc()).limit(keep_latest)
        condition = and_(audit.c.time<before, audit.c.id.not_in(keep))
        count = self._connection.execute(select(func.count()).select_from(audit).where(condition)).scalar_one()
        if not dry_run:
            self._connection.execute(delete(audit).where(condition))
        return {'dry_run':dry_run,'eligible':count,'deleted':0 if dry_run else count,
                'before':before,'keep_latest':keep_latest}


class RelationalUnitOfWork:
    def __init__(self, connection, *, write=False,schema_version=2):
        self._connection, self._write = connection, write
        self.schema_version = schema_version
        self.resources = ResourceRepository(self)
        self.accounts = AccountRepository(connection,write=write)
        self.oidc = OIDCRepository(connection,write=write)
        self.audit = AuditRepository(self)

    def require_write(self):
        if not self._write:
            raise StorageError('A write unit of work is required for this operation')
