"""Explicit backend configuration for API-independent host maintenance tools."""
from pathlib import Path
import re
from ..observability import register_secret


def read_environment(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size>262144:
        raise ValueError('Production environment file is missing or exceeds 256 KiB')
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)',line)
        if not match or match[1] in result:
            raise ValueError('Production environment requires unique literal KEY=value assignments')
        key,value = match.groups()
        if value.startswith(("'",'"')):
            end = value.rfind(value[0])
            if end==0 or value[end+1:].strip() and not value[end+1:].lstrip().startswith('#'):
                raise ValueError('Unsupported quoted production environment value')
            value = value[1:end]
        else:
            value = value.split(' #',1)[0].rstrip()
        result[key] = value
        if re.search(r'secret|token|password|database_url',key,re.I):
            register_secret(value)
    return result


def database_options(environment, data_dir):
    backend = environment.get('MOTOR_DATABASE_BACKEND','sqlite').strip().lower()
    raw = environment.get('MOTOR_DATABASE_URL','').strip()
    configured = environment.get('MOTOR_DATABASE_URL_HOST_FILE') or environment.get('MOTOR_DATABASE_URL_FILE')
    if raw and configured:
        raise ValueError('Choose an inline database URL or a URL file, not both')
    if configured:
        prefix = '/run/motor-secrets/'
        path = Path(configured)
        if str(configured).startswith(prefix):
            name = str(configured)[len(prefix):]
            if '/' in name or name in {'','.','..'}:
                raise ValueError('Database URL secret must be a simple file')
            path = Path(data_dir)/'secrets'/name
        if not path.is_file() or path.is_symlink() or path.stat().st_size>65536:
            raise ValueError('Database URL secret file is unavailable or invalid')
        raw = path.read_text(encoding='utf-8').strip()
    if raw:
        register_secret(raw)
    return {'backend':backend,'database_url':raw or None}
