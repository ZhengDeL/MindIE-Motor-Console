"""Consistent plugin backups with a portable, independently verified snapshot.

SQLite uses its online backup API. PostgreSQL exports a repeatable-read
transaction into a private SQLite container; this is explicitly a logical
snapshot, not a pg_dump image or a PostgreSQL physical backup.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL

from ..maintenance import backup_database, verify_backup, MaintenanceError, _atomic_json, _digest, _inspect
from .schema import metadata, APPLICATION_ID, SCHEMA_VERSION


def backup(database, destination_dir, *, reason='manual'):
    destination_dir = Path(destination_dir).resolve()
    destination_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    if destination_dir == database.directory:
        raise MaintenanceError('Use a separate backup directory')
    with database.guard:
        if database.name == 'sqlite':
            database._assert_lease()
            return {**backup_database(database.path,destination_dir,reason=reason),'source_backend':'sqlite'}
        token = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid4().hex[:10]
        destination = destination_dir/('console-'+token+'.sqlite3')
        handle, temporary_name = tempfile.mkstemp(prefix='.snapshot-',suffix='.part',dir=destination_dir)
        os.close(handle)
        temporary = Path(temporary_name)
        temporary.chmod(0o600)
        target = None
        deadline = time.monotonic()+120
        try:
            target = create_engine(URL.create('sqlite+pysqlite',database=str(temporary)),hide_parameters=True)
            metadata.create_all(target)
            # The same dedicated session owns the advisory lock and snapshot.
            # No writer can survive losing this database connection.
            database._connection.execution_options(isolation_level='REPEATABLE READ')
            try:
                with database.transaction() as source, target.begin() as output:
                    for table in metadata.sorted_tables:
                        result = source.execute(select(table).execution_options(stream_results=True,yield_per=256))
                        try:
                            while rows:=result.mappings().fetchmany(256):
                                if time.monotonic()>deadline:
                                    raise MaintenanceError('Database logical snapshot exceeded its deadline')
                                output.execute(table.insert(),[dict(row) for row in rows])
                        finally:
                            result.close()
            finally:
                if not database._connection.invalidated and not database._connection.closed:
                    database._connection.execution_options(isolation_level='READ COMMITTED')
            target.dispose()
            target = None
            with closing(sqlite3.connect(temporary)) as snapshot:
                snapshot.execute('PRAGMA application_id='+str(APPLICATION_ID))
                snapshot.execute('PRAGMA user_version='+str(SCHEMA_VERSION))
                snapshot.execute('PRAGMA journal_mode=DELETE')
                snapshot.commit()
                evidence = _inspect(snapshot)
            os.replace(temporary,destination)
            manifest_path = destination.with_suffix('.json')
            manifest = {'format_version':1,'created_at':datetime.now(timezone.utc).isoformat(),
                'method':'postgresql-repeatable-read-logical-export','source_backend':'postgresql',
                'reason':str(reason)[:200],'database':destination.name,'sha256':_digest(destination),
                'size_bytes':destination.stat().st_size,'sqlite_version':sqlite3.sqlite_version,**evidence}
            _atomic_json(manifest_path,manifest)
            verify_backup(destination,manifest_path)
            return {**manifest,'database_path':str(destination),'manifest_path':str(manifest_path)}
        except Exception as error:
            raise MaintenanceError('Database logical backup could not be completed') from error
        finally:
            if target is not None:
                target.dispose()
            temporary.unlink(missing_ok=True)


def restore(database, database_path, manifest_path=None):
    """Explicit maintenance import under the existing writer lease.

    The caller must stop accepting application changes and use a backup from
    the same product/schema. This method never invokes Agent tasks.
    """
    checked = verify_backup(database_path,manifest_path)
    if checked['application_id'] != APPLICATION_ID or checked['schema_version'] != SCHEMA_VERSION:
        raise MaintenanceError('Backup product or schema is incompatible with this build')
    path = Path(database_path).resolve()
    source = create_engine(URL.create('sqlite+pysqlite',database=str(path)),hide_parameters=True)
    deadline = time.monotonic()+120
    try:
        with database.transaction(write=True) as target, source.connect() as snapshot:
            for table in reversed(metadata.sorted_tables):
                target.execute(table.delete())
            for table in metadata.sorted_tables:
                result = snapshot.execute(select(table))
                try:
                    while rows:=result.mappings().fetchmany(256):
                        if time.monotonic()>deadline:
                            raise MaintenanceError('Database snapshot import exceeded its deadline')
                        target.execute(table.insert(),[dict(row) for row in rows])
                finally:
                    result.close()
    finally:
        source.dispose()
    return {'restored':True,'target_backend':database.name,'schema_version':SCHEMA_VERSION,
            'task_replay':'not-performed-by-restore','snapshot_sha256':checked['sha256']}
