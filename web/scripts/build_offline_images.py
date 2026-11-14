#!/usr/bin/env python3
"""Prepare a reviewable offline image context; never contacts Docker or a node.

Official base receipts must be obtained and verified separately. This tool
copies only product source, the pinned Motor tree, built Web assets and locked
wheels. Existing application images may supply explicitly selected binaries;
their application, environment and filesystem are never inherited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


class BuildInputError(ValueError):
    pass


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BuildInputError('Build inputs must be regular files: '+path.name)
    return path


def copy_file(source, target, *, mode=0o644):
    source = regular(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def copy_tree(source, target, *, reviewed_native=False, omitted=None):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise BuildInputError('Build source directories must not be symbolic links')
    native_modes = {}
    if reviewed_native:
        for record in git(source, 'ls-files', '--stage', '-z').split('\0'):
            if not record:
                continue
            metadata, name = record.split('\t', 1)
            mode, _object_id, stage = metadata.split(' ')
            if stage != '0':
                raise BuildInputError('Native source contains an unsupported index mode or unresolved merge')
            if mode == '120000':
                # Do not follow upstream documentation links or ship Windows
                # symlink stubs. The mandatory runtime manifest below still
                # verifies every required regular file, so a required link
                # cannot silently disappear from a runnable source profile.
                if omitted is not None:
                    omitted.append({'path':name, 'git_mode':mode, 'reason':'tracked-symlink-not-followed'})
                continue
            if mode not in {'100644', '100755'}:
                raise BuildInputError('Native source contains an unsupported index mode')
            native_modes[name] = 0o755 if mode == '100755' else 0o644
        entries = (source/name for name in native_modes)
    else:
        entries = source.rglob('*')
    for entry in sorted(entries):
        relative = entry.relative_to(source)
        excluded = {'.git', '__pycache__', 'node_modules', '.pytest_cache'}
        if not reviewed_native:
            excluded.add('tests')
        if any(part in excluded for part in relative.parts):
            continue
        if entry.is_symlink():
            raise BuildInputError('A build input contains an unreviewed symbolic link')
        if entry.is_file():
            official_example = reviewed_native and entry.name == '.env.example'
            if (entry.suffix in {'.pyc', '.sqlite', '.sqlite3', '.db', '.log'}
                    or entry.name.startswith('.env') and not official_example):
                raise BuildInputError('A source directory contains operational data: '+entry.name)
            copy_file(entry, target/relative, mode=native_modes.get(relative.as_posix(), 0o644))
    return native_modes


def official_receipt(path, repository):
    value = json.loads(regular(path).read_text(encoding='utf-8'))
    if (value.get('official_repository') != 'docker.io/library/'+repository
            or value.get('resolved_tag') != {'python':'3.12-slim-bookworm','nginx':'stable-alpine'}[repository]
            or value.get('architecture') != 'linux/arm64'
            or value.get('all_compressed_digests_verified') is not True
            or value.get('all_rootfs_diff_ids_verified') is not True):
        raise BuildInputError('Official base provenance or platform verification is missing')
    for name in ('manifest_digest', 'config_image_id'):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', str(value.get(name, ''))):
            raise BuildInputError('Official base has an invalid immutable identity')
    return {name:value[name] for name in ('official_repository', 'resolved_tag', 'manifest_digest',
                                         'config_image_id', 'architecture')}


def git(project, *arguments):
    result = subprocess.run(['git', '-C', str(project), *arguments], check=True,
                            capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def prepare(arguments):
    project = Path(arguments.project_dir).resolve()
    destination = Path(arguments.destination).resolve()
    if destination.exists() or destination == project or destination.is_relative_to(project/'web'):
        raise BuildInputError('Use a new independent build-context directory')
    python = official_receipt(arguments.python_receipt, 'python')
    nginx = official_receipt(arguments.nginx_receipt, 'nginx')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', arguments.dependency_image):
        raise BuildInputError('The reviewed binary dependency source must be an immutable image ID')
    if not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', arguments.version):
        raise BuildInputError('A stable product release version is required')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', arguments.sqlite_version):
        raise BuildInputError('The exact verified SQLite runtime version is required')
    upstream = project/'mindie-motor/upstream'
    pin = git(project, 'rev-parse', 'HEAD:mindie-motor/upstream')
    if git(upstream, 'rev-parse', 'HEAD') != pin or git(upstream, 'status', '--porcelain'):
        raise BuildInputError('Native Motor source differs from the recorded clean submodule pin')
    revision = git(project, 'rev-parse', 'HEAD')
    source_dirty = bool(git(project, 'status', '--porcelain'))
    lock = regular(project/'web/requirements.lock')
    permitted_hashes = set(re.findall(r'--hash=sha256:([0-9a-f]{64})', lock.read_text(encoding='utf-8')))
    wheels = sorted(Path(arguments.wheelhouse).glob('*.whl'))
    if not wheels:
        raise BuildInputError('The offline wheelhouse is empty')
    for wheel in wheels:
        if digest(regular(wheel)) not in permitted_hashes or not ('aarch64' in wheel.name or wheel.name.endswith('-any.whl')):
            raise BuildInputError('An offline wheel is not a locked ARM64/universal artifact: '+wheel.name)
    dist = Path(arguments.frontend_dist)
    regular(dist/'index.html')
    if not (dist/'assets').is_dir():
        raise BuildInputError('Build the reviewed Web distribution before preparing images')
    destination.mkdir(parents=True, mode=0o700)
    try:
        copy_file(lock, destination/'requirements.lock')
        for wheel in wheels:
            copy_file(wheel, destination/'wheelhouse'/wheel.name)
        copy_tree(project/'web/backend', destination/'backend')
        copy_tree(project/'web/scripts', destination/'scripts')
        copy_tree(dist, destination/'frontend/dist')
        native_omitted = []
        native_modes = copy_tree(upstream, destination/'upstream', reviewed_native=True, omitted=native_omitted)
        native_manifest = json.loads((destination/'backend/motor_source_manifest.json').read_text(encoding='utf-8'))
        if native_manifest.get('commit') != pin:
            raise BuildInputError('Bundled Motor integrity manifest differs from the source pin')
        for relative, expected in native_manifest['files'].items():
            path = Path(relative)
            if path.is_absolute() or '..' in path.parts or digest(regular(destination/'upstream'/path)) != expected:
                raise BuildInputError('Copied native Motor integrity differs: '+relative)
        for name in ('LICENSE', 'NOTICE.md'):
            copy_file(project/name, destination/'licenses'/name)
        for name in ('compose.docker-agent.yaml', 'compose.kubernetes-agent.yaml',
                     'compose.docker-kubernetes-agent.yaml', 'compose.agent-kubectl-client.yaml'):
            copy_file(project/'web'/name, destination/'rollout-assets'/name)
        copy_file(project/'web/deploy/nginx/web.conf', destination/'web.conf')
        aliases = {name:'motor-offline-'+name+':sha256-'+source['config_image_id'].split(':')[1]
                   for name,source in [('python',python),('nginx',nginx)]}
        aliases['dependencies'] = 'motor-offline-dependencies:sha256-'+arguments.dependency_image.split(':')[1]
        kubectl = 'COPY --from=binary-dependencies /usr/local/bin/kubectl /usr/local/bin/kubectl\n' if arguments.include_kubectl else ''
        dockerfile = f'''FROM {aliases['dependencies']} AS binary-dependencies
FROM {aliases['python']} AS runtime
# Build-only argument: Ascend hosts may use the ascend default runtime. The
# documented void mode disables device/driver mounting; it is not persisted
# into the final application environment and deployments explicitly use runc.
ARG ASCEND_VISIBLE_DEVICES=void
LABEL io.motor.product="motor-console" org.opencontainers.image.version="{arguments.version}" \\
 org.opencontainers.image.revision="{revision}" io.motor.native.commit="{pin}" \\
 io.motor.base.manifest="{python['manifest_digest']}" io.motor.source.dirty="{str(source_dirty).lower()}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LD_LIBRARY_PATH=/opt/sqlite/lib \\
 MOTOR_MODE=control MOTOR_DATA_DIR=/var/lib/motor-console MOTOR_UPSTREAM_DIR=/opt/motor/upstream \\
 MOTOR_STATIC_DIR=/app/frontend/dist MOTOR_HOST_WORKSPACE_DIR=/opt/motor-console/workspace \\
 MOTOR_HOST_ROOT=/host MOTOR_AGENT_BIND_IP=127.0.0.1 MOTOR_AGENT_PORT=19082
COPY --from=binary-dependencies /opt/sqlite/lib/ /opt/sqlite/lib/
COPY --from=binary-dependencies /usr/bin/tini /usr/bin/tini
{kubectl}RUN python -c "import sqlite3,sys; assert sys.version_info[:2] == (3,12), sys.version; assert sqlite3.sqlite_version == '{arguments.sqlite_version}', sqlite3.sqlite_version"
WORKDIR /app
COPY licenses/ /usr/share/doc/motor-console/
COPY build-manifest.json /usr/share/doc/motor-console/build-manifest.json
COPY requirements.lock /app/requirements.lock
COPY wheelhouse/ /wheelhouse/
RUN pip install --no-index --find-links=/wheelhouse --no-cache-dir --require-hashes -r /app/requirements.lock \\
 && pip check && rm -r /wheelhouse
COPY upstream/ /opt/motor/upstream/
RUN chmod -R a-w /opt/motor/upstream
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY rollout-assets/ /app/rollout-assets/
RUN chmod 0755 /app/scripts/container-entrypoint.sh
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python /app/scripts/healthcheck.py
ENTRYPOINT ["/usr/bin/tini","--","/app/scripts/container-entrypoint.sh"]

FROM runtime AS api
LABEL io.motor.role="api"
ENV MOTOR_STATIC_ENABLED=false

FROM {aliases['nginx']} AS web
ARG ASCEND_VISIBLE_DEVICES=void
LABEL io.motor.product="motor-console" io.motor.role="web" org.opencontainers.image.version="{arguments.version}" \\
 org.opencontainers.image.revision="{revision}" io.motor.native.commit="{pin}" \\
 io.motor.base.manifest="{nginx['manifest_digest']}" io.motor.source.dirty="{str(source_dirty).lower()}"
COPY build-manifest.json /usr/share/doc/motor-console/build-manifest.json
COPY frontend/dist/ /usr/share/nginx/html/
COPY web.conf /etc/nginx/conf.d/default.conf
# Match the 0.5-CPU/64-PID console budget, not the host accelerator CPU count.
RUN sed -i -E 's@^[[:space:]]*pid[[:space:]]+[^;]+;@pid /tmp/nginx.pid;@;s@^[[:space:]]*worker_processes[[:space:]]+[^;]+;@worker_processes 1;@;/^[[:space:]]*user[[:space:]]/d' /etc/nginx/nginx.conf
USER 101:101
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/health
ENTRYPOINT ["nginx","-g","daemon off;"]

FROM runtime AS bundled
LABEL io.motor.role="bundled"
COPY frontend/dist/ /app/frontend/dist/
'''
        (destination/'Dockerfile.offline').write_text(dockerfile, encoding='utf-8', newline='\n')
        files = []
        for path in sorted(destination.rglob('*')):
            if path.is_dir():
                path.chmod(0o755)
                continue
            if path.is_file():
                relative = path.relative_to(destination).as_posix()
                mode = native_modes.get(relative[len('upstream/'):], 0o644) if relative.startswith('upstream/') else 0o644
                path.chmod(mode)
                # Windows does not retain POSIX execute bits. The manifest is
                # authoritative for transport/archive extraction on Linux.
                files.append({'path':relative, 'sha256':digest(path), 'bytes':path.stat().st_size, 'mode':mode})
        context_sha = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        manifest = {'product':'motor-console', 'version':arguments.version, 'source_revision':revision,
                    'source_tree_dirty':source_dirty,
                    'native_motor_commit':pin, 'target':'linux/arm64', 'context_sha256':context_sha,
                    'native_integrity_verified_files':len(native_manifest['files']),
                    'native_omitted_entries':native_omitted,
                    'official_bases':{'python':python, 'nginx':nginx},
                    'binary_dependency_image':arguments.dependency_image, 'sqlite_version':arguments.sqlite_version,
                    'kubectl_included':arguments.include_kubectl, 'required_local_aliases':aliases,
                    'permission_policy':{'context_root':0o700, 'directories':0o755, 'platform_files':0o644,
                                         'native_files':'git-index-100644-or-100755', 'manifest_file':0o644},
                    'requirements_sha256':digest(lock), 'files':files,
                    'acceptance':'Context prepared only; image build, runtime and native workload acceptance remain separate'}
        manifest_path = destination/'build-manifest.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        manifest_path.chmod(0o644)
        return {'prepared':True, 'context_sha256':context_sha, 'files':len(files), 'wheels':len(wheels)}
    except BaseException:
        # Retain the partial private context for diagnosis; never recursively
        # delete a caller-computed path or overwrite an existing directory.
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--wheelhouse', required=True)
    parser.add_argument('--frontend-dist', required=True)
    parser.add_argument('--python-receipt', required=True)
    parser.add_argument('--nginx-receipt', required=True)
    parser.add_argument('--dependency-image', required=True)
    parser.add_argument('--sqlite-version', required=True)
    parser.add_argument('--version', default='0.11.1')
    parser.add_argument('--include-kubectl', action='store_true')
    arguments = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(arguments), ensure_ascii=False))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'prepared':False, 'error':str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
