"""Leased built-in SQLite and PostgreSQL database plugins.

The PostgreSQL writer lease and every transaction use the same dedicated
connection. Losing that session aborts its transaction as well as its lock;
there is no separate pooled writer that can continue after losing the lease.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from sqlalchemy import create_engine, event, inspect, text, UniqueConstraint
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

from ..maintenance import ProcessLease, MaintenanceError
from ..ports.storage import LeaseLost, SchemaVersionError, StorageUnavailable, StorageError
from .schema import APPLICATION_ID, SCHEMA_VERSION, metadata, platform_metadata


def _stamp():
    return datetime.now(timezone.utc).isoformat()


class RelationalDatabase:
    """Single control writer. No startup side effect precedes writer exclusion."""
    def __init__(self, directory: Path, *, backend: str = 'sqlite', database_url: str | None = None,
                 read_only: bool = False, database_path: Path | None = None, initialize: bool = True):
        self.directory = Path(directory).resolve()
        self.name = backend
        if database_path is not None and Path(database_path).is_symlink():
            raise SchemaVersionError('Database snapshot paths must not be symbolic links')
        self.path = Path(database_path).resolve() if database_path is not None else self.directory / 'console.sqlite3'
        self.read_only = read_only
        self.schema_version = SCHEMA_VERSION
        self.guard = threading.RLock()
        self._closed, self._lease_lost = False, False
        self._connection = None
        self._lease = None
        self.engine = None
        self.last_migration_backup = None
        if not read_only:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if backend not in {'sqlite', 'postgresql'}:
            raise ValueError('The selected database plugin is not installed or allowed')
        try:
            if backend == 'sqlite':
                if database_url:
                    raise ValueError('SQLite uses the configured data directory, not a database URL')
                if self.path.is_symlink() or (self.path.exists() and self.path.stat().st_nlink != 1):
                    raise SchemaVersionError('SQLite database must be an independent regular file')
                if not initialize and not self.path.is_file():
                    raise StorageUnavailable('Expected database is missing; no empty database was created')
                if read_only:
                    if not self.path.is_file():
                        raise StorageUnavailable('Expected SQLite database is missing; no empty database was created')
                    self.engine = create_engine('sqlite+pysqlite://', creator=lambda: sqlite3.connect(
                        self.path.as_uri()+'?mode=ro',uri=True,timeout=20,check_same_thread=False),hide_parameters=True)
                else:
                    self._lease = ProcessLease(self.directory, name='storage').acquire()
                    self.engine = create_engine(URL.create('sqlite+pysqlite', database=str(self.path)),
                        connect_args={'timeout': 20, 'check_same_thread': False}, pool_pre_ping=True,hide_parameters=True)
                @event.listens_for(self.engine, 'connect')
                def sqlite_connect(dbapi_connection, _record):
                    dbapi_connection.isolation_level = None
                    cursor = dbapi_connection.cursor()
                    cursor.execute('PRAGMA foreign_keys=ON')
                    cursor.execute('PRAGMA busy_timeout=20000')
                    cursor.execute('PRAGMA synchronous=FULL')
                    cursor.close()
                @event.listens_for(self.engine, 'begin')
                def sqlite_begin(connection):
                    connection.exec_driver_sql('BEGIN IMMEDIATE' if connection.get_execution_options().get('qh_write') else 'BEGIN')
            else:
                if not database_url:
                    raise ValueError('PostgreSQL requires an explicit database URL; no SQLite fallback is allowed')
                url = make_url(database_url)
                if url.drivername not in {'postgresql', 'postgresql+psycopg'}:
                    raise ValueError('PostgreSQL plugin requires a postgresql+psycopg URL')
                url = url.set(drivername='postgresql+psycopg')
                if database_path is not None:
                    raise ValueError('PostgreSQL uses its database URL, not a local database path')
                options = '-c statement_timeout=20000 -c lock_timeout=5000'
                if read_only:
                    options += ' -c default_transaction_read_only=on'
                self.engine = create_engine(url, pool_size=1, max_overflow=0, pool_timeout=5,
                    connect_args={'connect_timeout': 5, 'options': options},hide_parameters=True)
                self._connection = self.engine.connect()
                if not read_only:
                    acquired = self._connection.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': APPLICATION_ID}).scalar_one()
                    self._connection.commit()
                    if not acquired:
                        raise LeaseLost('Another control process holds the PostgreSQL writer lease')
            if read_only or not initialize:
                with self.transaction() as connection:
                    application,version,_tables = self._identity(connection)
                    if application != APPLICATION_ID or version not in (1,SCHEMA_VERSION):
                        raise SchemaVersionError('Expected platform database identity or schema is unavailable')
                    self._validate_base_schema(connection)
                    if version == SCHEMA_VERSION:
                        self._validate_current_schema(connection, _tables)
                    self.schema_version = version
            else:
                self._initialize()
        except Exception as error:
            try:
                self.close()
            except Exception:
                pass
            if isinstance(error,(StorageError,ValueError,MaintenanceError)):
                raise
            raise StorageUnavailable('The configured database plugin could not be opened; no fallback was used') from error

    def _assert_lease(self, connection=None):
        if self._closed or self._lease_lost:
            raise LeaseLost('Database writer lease is no longer held')
        if self.read_only:
            return
        if self.name == 'sqlite':
            try:
                if self._lease is None:
                    raise MaintenanceError('Writer lease is unavailable')
                self._lease.assert_held()
            except MaintenanceError as error:
                self._lease_lost = True
                raise LeaseLost('SQLite writer lease is no longer held') from error
        else:
            if self._connection is None or self._connection.closed or self._connection.invalidated:
                self._lease_lost = True
                raise LeaseLost('PostgreSQL writer session was lost; restart is required')
            try:
                held = connection.execute(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND classid=0 AND objid=CAST(:key AS oid) AND objsubid=1 AND granted)"), {'key': APPLICATION_ID}).scalar_one()
                if not held:
                    raise LeaseLost('PostgreSQL writer lock was released; restart is required')
            except Exception as error:
                self._lease_lost = True
                raise LeaseLost('PostgreSQL writer lease could not be verified') from error

    @contextmanager
    def transaction(self, *, write: bool = False):
        with self.guard:
            if self._closed or self._lease_lost:
                raise LeaseLost('Database writer lease is no longer held; restart is required')
            if write and self.read_only:
                raise StorageUnavailable('A read-only database view cannot perform mutations')
            connection = self._connection if self.name == 'postgresql' else self.engine.connect().execution_options(qh_write=write)
            try:
                if self.name == 'postgresql' and (connection is None or connection.closed or connection.invalidated):
                    self._lease_lost = True
                    raise LeaseLost('PostgreSQL writer session was lost; restart is required')
                with connection.begin():
                    self._assert_lease(connection)
                    yield connection
                    self._assert_lease(connection)
            except LeaseLost:
                raise
            except DBAPIError as error:
                if self.name == 'postgresql' and (error.connection_invalidated or connection.invalidated):
                    self._lease_lost = True
                raise StorageUnavailable('Database transaction could not be completed') from error
            finally:
                if self.name == 'sqlite':
                    connection.close()

    @contextmanager
    def unit_of_work(self, *, write=False):
        from .repositories import RelationalUnitOfWork
        with self.transaction(write=write) as connection:
            yield RelationalUnitOfWork(connection,write=write,schema_version=self.schema_version)

    def _identity(self, connection):
        tables = set(inspect(connection).get_table_names())
        if self.name == 'sqlite':
            application = connection.exec_driver_sql('PRAGMA application_id').scalar_one()
            version = connection.exec_driver_sql('PRAGMA user_version').scalar_one()
            if version == SCHEMA_VERSION and 'platform_metadata' in tables:
                identity = dict(connection.execute(text('SELECT key,value FROM platform_metadata')).all())
                if identity.get('application_id')!=str(application) or identity.get('schema_version')!=str(version):
                    raise SchemaVersionError('SQLite metadata and application/schema identity disagree')
        elif 'platform_metadata' in tables:
            rows = dict(connection.execute(text('SELECT key,value FROM platform_metadata')).all())
            try:
                application, version = int(rows['application_id']), int(rows['schema_version'])
            except (KeyError, TypeError, ValueError) as error:
                raise SchemaVersionError('Database identity metadata is missing or malformed') from error
        else:
            application = version = 0
        return application, version, tables

    @staticmethod
    def _validate_base_schema(connection):
        expected = {'resources': {'kind','id','body'}, 'revisions': {'id','service_id','created_at','body'},
                    'audit': {'id','time','body','actor','request_id'},
                    'schema_migrations': {'version','from_version','applied_at','backup_manifest'}}
        inspector = inspect(connection)
        for name, columns in expected.items():
            actual = {item['name'] for item in inspector.get_columns(name)}
            if not columns.issubset(actual):
                raise SchemaVersionError('Platform database schema is malformed: '+name)
            primary = inspector.get_pk_constraint(name).get('constrained_columns', [])
            if primary != (['kind','id'] if name == 'resources' else ['version'] if name == 'schema_migrations' else ['id']):
                raise SchemaVersionError('Platform database primary key is incompatible: '+name)

    @staticmethod
    def _validate_current_schema(connection, tables=None):
        """Do not accept a version stamp over missing identity constraints."""
        inspector = inspect(connection)
        tables = set(inspector.get_table_names()) if tables is None else tables
        if tables != set(metadata.tables):
            raise SchemaVersionError('Platform database tables differ from this schema contract')
        for name, table in metadata.tables.items():
            actual = {item['name']: item for item in inspector.get_columns(name)}
            if set(actual) != {column.name for column in table.columns}:
                raise SchemaVersionError('Platform database columns are incompatible: '+name)
            primary = inspector.get_pk_constraint(name).get('constrained_columns', [])
            if primary != [column.name for column in table.primary_key.columns]:
                raise SchemaVersionError('Platform database primary key is incompatible: '+name)
            for column in table.columns:
                observed = actual[column.name]
                # Legacy SQLite TEXT PRIMARY KEY reports nullable even though
                # all published domain identities are required before insert.
                nullable_legacy_key = connection.dialect.name == 'sqlite' and column.primary_key
                if (observed['type']._type_affinity != column.type._type_affinity
                        or not nullable_legacy_key and observed['nullable'] != column.nullable):
                    raise SchemaVersionError('Platform database column type is incompatible: '+name+'.'+column.name)
            unique = {tuple(item['column_names']) for item in inspector.get_unique_constraints(name)}
            unique.update(tuple(item['column_names']) for item in inspector.get_indexes(name) if item.get('unique'))
            for constraint in table.constraints:
                if isinstance(constraint, UniqueConstraint) and tuple(column.name for column in constraint.columns) not in unique:
                    raise SchemaVersionError('Platform database unique identity constraint is missing: '+name)
            foreign = {(tuple(item['constrained_columns']), item['referred_table'], tuple(item['referred_columns']))
                       for item in inspector.get_foreign_keys(name)}
            for constraint in table.foreign_key_constraints:
                expected = (tuple(element.parent.name for element in constraint.elements),
                            constraint.referred_table.name, tuple(element.column.name for element in constraint.elements))
                if expected not in foreign:
                    raise SchemaVersionError('Platform database account relationship is missing: '+name)

    def _initialize(self):
        with self.transaction() as connection:
            application, version, tables = self._identity(connection)
            if application not in (0, APPLICATION_ID):
                raise SchemaVersionError('Database belongs to another application; implicit cross-product imports are refused')
            if version not in (0, 1, SCHEMA_VERSION):
                raise SchemaVersionError('Database schema is incompatible; use a matching build or reviewed backup')
            if application == 0 and (version or tables):
                raise SchemaVersionError('Existing unrecognized database is not adopted or repaired automatically')
            if version:
                if application != APPLICATION_ID:
                    raise SchemaVersionError('Versioned database has an unknown application identity')
                self._validate_base_schema(connection)
                if version == SCHEMA_VERSION:
                    self._validate_current_schema(connection, tables)
        if version == 1 and self.name != 'sqlite':
            # PostgreSQL was introduced at schema 2. There is no released
            # PostgreSQL schema-1 contract or verified rollback export for it.
            raise SchemaVersionError('No reviewed PostgreSQL schema-1 migration exists; the database was not changed')
        if version == 1:
            from ..maintenance import backup_database
            self.last_migration_backup = backup_database(self.path, self.directory/'migration-backups', reason='schema-1-to-2')
        with self.transaction(write=True) as connection:
            if version == 1:
                connection.exec_driver_sql('ALTER TABLE resources ADD COLUMN created_order BIGINT NOT NULL DEFAULT 0')
                connection.exec_driver_sql('UPDATE resources SET created_order=rowid')
            # This is the only application schema creation site. Auth/OIDC do not
            # create tables in domain constructors or after accepting requests.
            metadata.create_all(connection)
            for table in metadata.sorted_tables:
                actual = {item['name'] for item in inspect(connection).get_columns(table.name)}
                if not {column.name for column in table.columns}.issubset(actual):
                    raise SchemaVersionError('Existing table is incompatible: '+table.name)
                for index in table.indexes:
                    index.create(connection,checkfirst=True)
            for key, value in [('application_id',str(APPLICATION_ID)),('schema_version',str(SCHEMA_VERSION))]:
                existing = connection.execute(platform_metadata.select().where(platform_metadata.c.key == key)).first()
                if existing:
                    connection.execute(platform_metadata.update().where(platform_metadata.c.key==key).values(value=value))
                else:
                    connection.execute(platform_metadata.insert().values(key=key,value=value))
            if version != SCHEMA_VERSION:
                connection.execute(text('INSERT INTO schema_migrations(version,from_version,applied_at,backup_manifest) VALUES(:v,:old,:at,:backup)'),
                    {'v':SCHEMA_VERSION,'old':version,'at':_stamp(),'backup':self.last_migration_backup['manifest_path'] if self.last_migration_backup else None})
            if self.name == 'sqlite':
                connection.exec_driver_sql('PRAGMA application_id='+str(APPLICATION_ID))
                connection.exec_driver_sql('PRAGMA user_version='+str(SCHEMA_VERSION))
            self._validate_current_schema(connection)
        if self.name == 'sqlite':
            raw = self.engine.raw_connection()
            try:
                raw.execute('PRAGMA journal_mode=WAL')
                raw.execute('PRAGMA synchronous=FULL')
            finally:
                raw.close()
            self.path.chmod(0o600)

    def health(self):
        with self.transaction() as connection:
            valid = connection.execute(text('SELECT 1')).scalar_one() == 1
        return {'healthy':valid,'engine':self.name,'schema_version':self.schema_version,'writer_lease':not self.read_only}

    def info(self):
        return {'engine':self.name,'schema_version':self.schema_version,'application_id':APPLICATION_ID,
                'mode':'read-only' if self.read_only else 'single-instance',
                'database_plugin':self.name,'plugin_api_version':1,
                'writer_lease':'not-held-read-only' if self.read_only else 'dedicated-session' if self.name=='postgresql' else 'kernel-file-lock',
                'backup_format':'portable-sqlite-snapshot', 'data_directory':str(self.directory)}

    def schema_info(self):
        with self.transaction() as connection:
            inspector = inspect(connection)
            shape = []
            for name in sorted(inspector.get_table_names()):
                shape.append({'table':name,
                    'columns':[(item['name'],item['type']._type_affinity.__name__,item['nullable'])
                               for item in inspector.get_columns(name)],
                    'primary_key':inspector.get_pk_constraint(name).get('constrained_columns',[]),
                    'foreign_keys':sorted((tuple(item['constrained_columns']),item['referred_table'],tuple(item['referred_columns']))
                                          for item in inspector.get_foreign_keys(name))})
        digest = hashlib.sha256(json.dumps(shape,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return {'schema_version':self.schema_version,'application_id':APPLICATION_ID,'schema_sha256':digest}

    def backup(self, destination_dir, *, reason='manual'):
        from .snapshots import backup
        return backup(self,destination_dir,reason=reason)

    def restore_snapshot(self, database_path, manifest_path=None):
        from .snapshots import restore
        return restore(self,database_path,manifest_path)

    def close(self):
        with self.guard:
            if self._closed:
                return
            self._closed = True
            if self._connection is not None:
                try:
                    self._connection.close()
                finally:
                    self._connection = None
            if self.engine is not None:
                self.engine.dispose()
            if self._lease is not None:
                self._lease.release()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
