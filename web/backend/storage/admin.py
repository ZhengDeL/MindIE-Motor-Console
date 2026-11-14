"""Driver-specific offline restore invoked only by the host maintenance tool."""
from pathlib import Path
import sqlite3
import time

from ..maintenance import MaintenanceError, ProcessLease, verify_backup, _inspect
from .schema import APPLICATION_ID


def restore_offline(data_dir, database_path, manifest_path=None, *, backend='sqlite',database_url=None):
    evidence = verify_backup(database_path,manifest_path)
    if evidence['application_id'] != APPLICATION_ID:
        raise MaintenanceError('Recovery backup belongs to a different product')
    if backend == 'postgresql':
        # This build can import its current portable schema. An unsupported
        # downgrade is rejected with maintenance preserved, never approximated.
        from ..store import Store
        with Store(data_dir,backend=backend,database_url=database_url,initialize=False) as store:
            return store.restore_snapshot(database_path,manifest_path)
    if backend != 'sqlite':
        raise MaintenanceError('No offline restore provider is installed for this database')
    target_path = Path(data_dir).resolve()/'console.sqlite3'
    source_path = Path(database_path).resolve()
    if source_path == target_path or target_path.is_symlink():
        raise MaintenanceError('Offline restore source and target must be independent regular files')
    deadline = time.monotonic()+120
    def progress(*_args):
        if time.monotonic()>deadline:
            raise MaintenanceError('Offline restore exceeded its deadline')
    with ProcessLease(data_dir,name='storage'):
        source = sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True,timeout=20)
        target = sqlite3.connect(target_path,timeout=20)
        try:
            checkpoint = target.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint and checkpoint[0]!=0:
                raise MaintenanceError('A live reader prevents the offline restore checkpoint')
            if target.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete':
                raise MaintenanceError('Offline database could not leave WAL mode')
            source.backup(target,pages=256,progress=progress,sleep=0.05)
            target.commit()
            actual = _inspect(target)
            for name in ('schema_version','application_id','row_counts'):
                if actual[name]!=evidence[name]:
                    raise MaintenanceError('Offline restore verification differs: '+name)
        finally:
            target.close()
            source.close()
    return {'restored':True,'target_backend':'sqlite','schema_version':evidence['schema_version'],
            'snapshot_sha256':evidence['sha256'],'task_replay':'not-performed-by-restore'}
