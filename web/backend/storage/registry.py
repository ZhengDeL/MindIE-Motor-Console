"""Explicit trusted database plugins. Loading is not a permission grant.

Database API and schema versions are separate. A missing backend never opens
an empty SQLite database, and arbitrary installed entry points are not loaded.
"""
from dataclasses import dataclass
import re
import threading
from .database import RelationalDatabase
from .schema import SCHEMA_VERSION


@dataclass(frozen=True)
class DatabaseDescriptor:
    identifier: str
    version: str = '1.0.0'
    api_version: int = 1
    schema_version: int = SCHEMA_VERSION
    capabilities: tuple[str,...] = ('transactions','conditional-update','atomic-audit','consistent-backup','offline-restore')


class SQLitePlugin(RelationalDatabase):
    descriptor = DatabaseDescriptor('sqlite')
    def __init__(self,directory,**options):
        super().__init__(directory,backend='sqlite',**options)


class PostgreSQLPlugin(RelationalDatabase):
    descriptor = DatabaseDescriptor('postgresql')
    def __init__(self,directory,**options):
        super().__init__(directory,backend='postgresql',**options)


PLUGINS = {'sqlite':SQLitePlugin,'postgresql':PostgreSQLPlugin}
_guard = threading.RLock()
_opened = False


def register_database_plugin(identifier, plugin):
    """Bootstrap-only extension for reviewed, installed code; never an API hook."""
    with _guard:
        if _opened or identifier in PLUGINS:
            raise ValueError('Database plugins are fixed before the first database is opened')
        descriptor = getattr(plugin,'descriptor',None)
        required = ('unit_of_work','health','info','schema_info','backup','restore_snapshot','close')
        if (not isinstance(identifier,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',identifier)
                or not isinstance(descriptor,DatabaseDescriptor) or descriptor.identifier!=identifier
                or descriptor.api_version!=1 or descriptor.schema_version!=SCHEMA_VERSION
                or not all(callable(getattr(plugin,name,None)) for name in required)):
            raise ValueError('Database plugin does not implement the required versioned contract')
        PLUGINS[identifier] = plugin


def open_database(directory, *, backend='sqlite', **options):
    global _opened
    with _guard:
        plugin = PLUGINS.get(backend)
        if plugin is None or plugin.descriptor.api_version != 1:
            raise ValueError('Selected database plugin is missing or incompatible')
        _opened = True
    return plugin(directory,**options)
