"""Create a new Agent-only TLS identity; private key material stays on its host."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.agent_credentials import read_private_file
from backend.agent_security import validate_agent_url
from scripts.product_paths import require_product_path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _path(value: str, *, directory=False) -> Path:
    path = require_product_path(value, "Agent TLS material")
    if not path.is_absolute() or path.resolve() != path or ".." in path.parts:
        raise ValueError("Agent TLS paths must be absolute real paths without symbolic links")
    info = path.stat()
    if directory and not path.is_dir() or not directory and not path.is_file():
        raise ValueError("Agent TLS source path has an invalid type")
    if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o022):
        raise ValueError("Agent TLS paths must be owned by the installing account and not writable by other accounts")
    if os.name == "posix":
        for parent in path.parents:
            metadata = parent.lstat()
            if (metadata.st_uid not in {0, os.geteuid()} or not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_mode & 0o022 and not metadata.st_mode & stat.S_ISVTX):
                raise ValueError("Agent TLS path ancestors must be trusted real directories")
    return path


def configure(data_dir: str, env_file: str, advertised_url: str, *, validity_days=365) -> dict:
    if not sys.platform.startswith("linux"):
        raise ValueError("Configure Agent TLS on the intended Linux execution host")
    if type(validity_days) is not int or not 30 <= validity_days <= 397:
        raise ValueError("Agent certificate lifetime must be 30..397 days")
    data, environment = _path(data_dir, directory=True), _path(env_file)
    url = validate_agent_url(advertised_url)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.path or not parsed.hostname:
        raise ValueError("Managed Agent TLS requires an HTTPS origin")
    original = read_private_file(environment, maximum=65536)
    lines = original.decode("utf-8").splitlines()
    names = [line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")]
    if any(name.startswith("MOTOR_AGENT_SERVER_") for name in names):
        raise ValueError("Existing Agent TLS configuration requires an explicit maintenance rotation")
    clients = _path(str(data / "client-secrets"), directory=True)
    target = clients / "agent-server"
    target.mkdir(mode=0o700, exist_ok=False)
    created: list[Path] = []
    published = False
    temporary = environment.with_name(environment.name + ".tls-" + uuid4().hex)
    try:
        moment = datetime.now(timezone.utc)
        end = moment + timedelta(days=validity_days)
        issuer_key = ec.generate_private_key(ec.SECP256R1())
        server_key = ec.generate_private_key(ec.SECP256R1())
        issuer_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "擎寰平台 Agent CA")])
        issuer = (x509.CertificateBuilder().subject_name(issuer_name).issuer_name(issuer_name)
                  .public_key(issuer_key.public_key()).serial_number(x509.random_serial_number())
                  .not_valid_before(moment - timedelta(minutes=5)).not_valid_after(end)
                  .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                  .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(issuer_key.public_key()), critical=False)
                  .sign(issuer_key, hashes.SHA256()))
        hosts = {parsed.hostname, "127.0.0.1", "::1"}
        for line in lines:
            if line.startswith("MOTOR_AGENT_BIND_IP="):
                bind = line.split("=", 1)[1].strip().strip("'\"")
                address = ipaddress.ip_address(bind)
                if not address.is_unspecified:
                    hosts.add(str(address))
        alternatives = []
        for host in sorted(hosts):
            try:
                alternatives.append(x509.IPAddress(ipaddress.ip_address(host)))
            except ValueError:
                alternatives.append(x509.DNSName(host))
        server = (x509.CertificateBuilder()
                  .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "擎寰平台 Agent")]))
                  .issuer_name(issuer_name).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
                  .not_valid_before(moment - timedelta(minutes=5)).not_valid_after(end)
                  .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                  .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
                  .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                  .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
                  .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
                  .sign(issuer_key, hashes.SHA256()))
        materials = {
            "ca.crt": issuer.public_bytes(serialization.Encoding.PEM),
            "server.crt": server.public_bytes(serialization.Encoding.PEM),
            "server.key": server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()),
        }
        for name, raw in materials.items():
            path = target / name
            with path.open("xb") as output:
                created.append(path); path.chmod(0o600)
                output.write(raw); output.flush(); os.fsync(output.fileno())
        # No CA signing key is saved or returned. Replacement identities require
        # a new SSH-verified trust transition, never an unauthenticated renewal.
        issuer_key = server_key = None
        if environment.read_bytes() != original:
            raise ValueError("Agent environment changed during TLS preparation")
        additions = {
            "MOTOR_AGENT_SERVER_CERT_FILE": "/run/motor-client/agent-server/server.crt",
            "MOTOR_AGENT_SERVER_KEY_FILE": "/run/motor-client/agent-server/server.key",
            "MOTOR_AGENT_SERVER_CA_FILE": "/run/motor-client/agent-server/ca.crt",
        }
        with temporary.open("xb") as output:
            temporary.chmod(0o600)
            output.write(original.rstrip() + b"\n" + "".join(f"{key}={value}\n" for key, value in additions.items()).encode())
            output.flush(); os.fsync(output.fileno())
        if environment.read_bytes() != original:
            raise ValueError("Agent environment changed during TLS preparation")
        os.replace(temporary, environment)
        published = True
        descriptor = os.open(environment.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
        return {"configured": True, "agent_url": url,
                "ca_pem": materials["ca.crt"].decode("ascii"),
                "certificate_fingerprint_sha256": server.fingerprint(hashes.SHA256()).hex(),
                "not_after": server.not_valid_after_utc.isoformat(), "private_keys_disclosed": False}
    except BaseException:
        temporary.unlink(missing_ok=True)
        # Once the environment has been published, keep its referenced files
        # even if the directory fsync fails. The installer still refuses to
        # start, but the operator retains a complete identity for inspection.
        if not published:
            for path in reversed(created): path.unlink(missing_ok=True)
            target.rmdir()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--advertised-url", required=True)
    parser.add_argument("--validity-days", type=int, default=365)
    args = parser.parse_args()
    try:
        result = configure(**vars(args))
    except Exception:
        print(json.dumps({"configured": False, "error_code": "agent_tls_configuration_refused"}), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
