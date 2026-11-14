"""Prepare an empty production deployment without starting or replacing services.

Requires Python 3.11+ and cryptography. Existing data/config/secrets are never
overwritten; migration from an existing installation is a separate operation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.releases import _IMAGE
from backend.agent_rollout_models import validate_install_root
from scripts.product_paths import require_product_path
from scripts.tls_material import validate_server_identity


def prepare(project_dir, data_dir, image, origin, public_key, *, port=19080, agent_rollout_root="/var/lib/motor-console-agents",
            web_image=None, web_tls_dir=None, log_dir=None):
    if any(any(ord(c) < 32 for c in str(value)) for value in (project_dir, data_dir, image, origin, public_key)):
        raise ValueError("Deployment settings must not contain control characters")
    project = require_product_path(project_dir, "Project directory")
    data = require_product_path(data_dir, "Control data directory")
    public_key = require_product_path(public_key, "Publisher public key")
    rollout_root = validate_install_root(agent_rollout_root)
    if data.is_relative_to(project) or project.is_relative_to(data):
        raise ValueError("Keep Control data separate from the source checkout")
    env = project / "web/.env.production"
    if not (project / "web/compose.production.yaml").is_file(): raise ValueError("Production Compose definition is missing")
    if not _IMAGE.fullmatch(image): raise ValueError("Production image must be pinned by SHA256 digest")
    if web_image is not None and (not isinstance(web_image, str) or not _IMAGE.fullmatch(web_image)):
        raise ValueError("Independent Web image must use an immutable registry digest or local image ID")
    if bool(web_image) != bool(web_tls_dir):
        raise ValueError("Independent Web deployment requires both --web-image and --web-tls-dir")
    tls_files = None
    if web_image:
        tls_source = Path(web_tls_dir).resolve()
        if not (project / "web/compose.modular.yaml").is_file(): raise ValueError("Modular Compose definition is missing")
        tls_files = [tls_source / "server.crt", tls_source / "server.key"]
        if any(not p.is_file() or p.is_symlink() or p.stat().st_size > 65536 for p in tls_files):
            raise ValueError("TLS directory must contain bounded regular server.crt and server.key files")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or any(character.isspace() for character in origin)):
        raise ValueError("Use the exact public HTTPS origin without a path")
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Invalid HTTPS origin port")
    except ValueError as exc:
        raise ValueError("Invalid HTTPS origin port") from exc
    if tls_files:
        validate_server_identity(tls_files[0], tls_files[1], parsed.hostname)
    if not 1 <= int(port) <= 65535: raise ValueError("Invalid control port")
    if env.exists(): raise ValueError("Production environment already exists; nothing was overwritten")
    if data.exists() and any(data.iterdir()): raise ValueError("New production setup requires an empty data directory; do not import another product database or credentials")
    key_bytes = Path(public_key).read_bytes()
    if not isinstance(serialization.load_pem_public_key(key_bytes), Ed25519PublicKey): raise ValueError("Use the trusted publisher's Ed25519 public key")
    owner = os.geteuid() if hasattr(os, "geteuid") else 10001
    group = os.getegid() if hasattr(os, "getegid") else 10001
    owner, group = (10001, 10001) if owner == 0 else (owner, group)
    settings = {"MOTOR_CONSOLE_IMAGE": image, "MOTOR_CONTROL_DATA_DIR": str(data),
                "MOTOR_CONTROL_UID": owner, "MOTOR_CONTROL_GID": group,
                "MOTOR_CONTROL_BIND_IP": "127.0.0.1", "MOTOR_CONTROL_PORT": port,
                "MOTOR_TRUSTED_ORIGINS": origin,
                "MOTOR_RELEASE_PUBLIC_KEY_HOST_FILE": str(data / "config/release.pub"),
                "MOTOR_AGENT_ROLLOUT_ROOT": str(rollout_root)}
    logs = require_product_path(log_dir or data / "logs", "Log directory")
    settings.update(MOTOR_LOG_HOST_DIR=str(logs), MOTOR_LOG_SEGMENT_BYTES=10485760, MOTOR_LOG_ROTATIONS=7)
    if web_image:
        settings.update(MOTOR_COMPOSE_FILE="compose.modular.yaml", MOTOR_WEB_IMAGE=web_image,
                        MOTOR_WEB_PORT=parsed.port or 443, MOTOR_WEB_BIND_IP="0.0.0.0",
                        MOTOR_WEB_CONFIG_FILE=str(data / "config/web/nginx.conf"),
                        MOTOR_WEB_TLS_DIR=str(data / "config/web/tls"))
    if any("'" in str(value) for value in settings.values()):
        raise ValueError("Deployment settings must not contain single quotes")
    created = []
    def mkdir(path):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
        if os.name == "posix" and os.geteuid() == 0: os.chown(path, owner, group)
    def write(path, content, mode=0o600):
        with path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        path.chmod(mode)
        if os.name == "posix" and os.geteuid() == 0 and path.is_relative_to(data): os.chown(path, owner, group)
        created.append(path)
    mkdir(data)
    for child in ("secrets", "config", "releases", "backups", "logs", "profiles"): mkdir(data / child)
    if logs != data / "logs": mkdir(logs)
    write(data / "secrets/encryption.key", secrets.token_urlsafe(48).encode() + b"\n")
    write(data / "secrets/bootstrap.token", secrets.token_urlsafe(48).encode() + b"\n")
    write(data / "config/release.pub", key_bytes, 0o644)
    if web_image:
        mkdir(data / "config/web")
        mkdir(data / "config/web/tls")
        # Web runs as 101, with only the dedicated configuration group shared.
        # It receives this TLS directory, never the control's secrets directory.
        (data / "config/web/tls").chmod(0o750)
        for path in tls_files:
            write(data / "config/web/tls" / path.name, path.read_bytes(), 0o640 if path.name.endswith(".key") else 0o644)
        template = project / "web/deploy/nginx/web.tls.conf.example"
        write(data / "config/web/nginx.conf", template.read_bytes(), 0o644)
    write(env, ("# Generated local deployment settings; keep private.\n" + "\n".join(f"{k}='{v}'" for k, v in settings.items()) + "\n").encode())
    return {"prepared": True, "services_started": False, "environment_file": str(env), "data_directory": str(data),
            "bootstrap_token_file": str(data / "secrets/bootstrap.token"),
            "next_command": "docker compose --env-file web/.env.production -f web/" + ("compose.modular.yaml up -d control web" if web_image else "compose.production.yaml up -d control"),
            "public_origin": origin, "requires_https_entrypoint": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--release-public-key", required=True)
    parser.add_argument("--port", type=int, default=19080)
    parser.add_argument("--agent-rollout-root", default="/var/lib/motor-console-agents")
    parser.add_argument("--web-image", help="Independent immutable Web image; requires a TLS directory")
    parser.add_argument("--web-tls-dir", help="Directory containing the reviewed server.crt and server.key")
    parser.add_argument("--log-dir", help="Independent absolute host log directory")
    args = parser.parse_args()
    try:
        result = prepare(args.project_dir, args.data_dir, args.image, args.origin, args.release_public_key,
                         port=args.port, agent_rollout_root=args.agent_rollout_root,
                         web_image=args.web_image, web_tls_dir=args.web_tls_dir, log_dir=args.log_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        print(json.dumps({"prepared": False, "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__": raise SystemExit(main())
