"""Prepare a new local production Agent; never start containers or contact a cluster.

Run on the intended Linux Docker host, using reviewed immutable images and
native TLS material or an existing trusted HTTPS proxy beyond loopback. Existing data,
credentials and environment files are never replaced. Requires PyYAML and
cryptography from the pinned platform tool environment.
"""
from __future__ import annotations

import argparse
import base64
from decimal import Decimal
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import ssl
import stat
import sys
from urllib.parse import urlsplit

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.product_paths import require_product_path


_IMAGE = re.compile(r"(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})\Z")
_CREDENTIALS_CONTAINER = "/run/motor-agent-credentials"


def _require_linux_host():
    if not sys.platform.startswith("linux"):
        raise ValueError("Run prepare_agent.py locally on the intended Linux Docker host")


def _path(value, label: str) -> Path:
    raw = str(value)
    if not raw or any(ord(c) < 32 for c in raw) or any(c in raw for c in "'$`"):
        raise ValueError(label + " must be an absolute path without control characters, quotes or substitutions")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(label + " must be an absolute local path")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(label + " must not traverse a symbolic link")
    return require_product_path(path, label)


def _private_file(value, label: str) -> Path:
    path = _path(value, label)
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(label + " must be an existing regular file of at most 2 MiB")
    if os.name == "posix":
        info = path.stat()
        if (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid not in {0, os.geteuid()}
                or info.st_nlink != 1):
            raise ValueError(label + " must be a private, singly linked file owned by root or the current user (chmod 600)")
        _trusted_parents(path.parent, label)
    return path


def _trusted_parents(path: Path, label: str):
    if os.name != "posix":
        return
    for parent in (path, *path.parents):
        if not parent.exists():
            continue
        info = parent.stat()
        sticky = bool(info.st_mode & stat.S_ISVTX)
        if info.st_uid not in {0, os.geteuid()} or (info.st_mode & 0o022 and not sticky):
            raise ValueError(label + " must not traverse an untrusted writable parent directory")


def _resource_settings(cpus, memory, pids, concurrency) -> dict:
    cpu = str(cpus)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,3})?", cpu) or not Decimal("0.25") <= Decimal(cpu) <= 32:
        raise ValueError("Agent CPUs must be a number between 0.25 and 32")
    memory = str(memory).lower()
    matched = re.fullmatch(r"([1-9][0-9]*)([kmg])", memory)
    if not matched:
        raise ValueError("Agent memory must use an integer k, m or g unit, for example 1g")
    size = int(matched[1]) * {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[matched[2]]
    if not 512 * 1024 ** 2 <= size <= 64 * 1024 ** 3:
        raise ValueError("Agent memory must be between 512m and 64g")
    if type(pids) is not int or not 64 <= pids <= 4096:
        raise ValueError("Agent PID limit must be an integer between 64 and 4096")
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise ValueError("Native concurrency must be an integer between 1 and 4")
    return {"MOTOR_AGENT_CPUS": cpu, "MOTOR_AGENT_MEMORY_LIMIT": memory,
            "MOTOR_AGENT_PIDS_LIMIT": pids, "MOTOR_AGENT_NATIVE_CONCURRENCY": concurrency}


def _public_endpoint(bind_ip: str, port: int, public_url: str | None,
                     https_proxy_confirmed: bool, agent_ca_file, *, native_tls=False) -> tuple[str, str | None]:
    try:
        address = ipaddress.IPv4Address(bind_ip)
    except ipaddress.AddressValueError as exc:
        raise ValueError("Agent bind IP must be an IPv4 address") from exc
    if address.is_multicast or address.is_link_local:
        raise ValueError("Agent bind IP must be loopback, a management address or 0.0.0.0")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Agent port must be between 1 and 65535")
    if not public_url:
        if not address.is_loopback:
            raise ValueError("A non-loopback Agent requires an explicit HTTPS public URL and native TLS material or a confirmed trusted proxy")
        public_url = f"{'https' if native_tls else 'http'}://{address}:{port}"
    if any(ord(c) < 33 for c in public_url):
        raise ValueError("Agent public URL must not contain whitespace or control characters")
    parsed = urlsplit(public_url)
    try:
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        valid_port = False
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or not valid_port
            or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Agent public URL must be an HTTP(S) origin without credentials or a path")
    try:
        public_address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        public_address = None
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9.])?", parsed.hostname):
            raise ValueError("Agent public URL hostname is invalid") from None
    if public_address and (public_address.is_unspecified or public_address.is_multicast or public_address.is_link_local):
        raise ValueError("Agent public URL must use a reachable management address or hostname")
    if parsed.scheme == "http":
        if native_tls:
            raise ValueError("Native TLS material requires an HTTPS public URL")
        local_url = public_address is not None and (
            public_address.version == 4 and public_address.is_loopback
            or public_address.version == 6 and str(public_address) == "::1")
        if not address.is_loopback or not local_url:
            raise ValueError("Plain HTTP is permitted only for a loopback Agent and loopback URL")
        if agent_ca_file:
            raise ValueError("An Agent CA file requires an HTTPS public URL")
    elif not (https_proxy_confirmed or native_tls):
        raise ValueError("Use --tls-dir for native Agent TLS, or confirm an existing trusted proxy with --https-proxy-confirmed")
    ca_path = None
    if agent_ca_file:
        ca = _path(agent_ca_file, "Agent public CA file")
        if not ca.is_file() or ca.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Agent public CA file must be an existing PEM bundle of at most 2 MiB")
        try:
            ssl.create_default_context(cafile=str(ca))
        except (OSError, ssl.SSLError) as exc:
            raise ValueError("Agent public CA file is not a valid PEM trust bundle") from exc
        ca_path = str(ca)
    return public_url, ca_path


def _tls_source(path, label, *, secret=False):
    source = _private_file(path, label) if secret else _path(path, label)
    if not source.is_file() or source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(label + " must be a bounded regular PEM file")
    if os.name == "posix":
        info = source.stat()
        if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
            raise ValueError(label + " must be owned by root/current user and not writable by other accounts")
        _trusted_parents(source.parent, label)
    return source.read_bytes()


def _certificate_chain(raw, label):
    from cryptography import x509
    from datetime import datetime, timezone
    try:
        values = x509.load_pem_x509_certificates(raw)
        if not 1 <= len(values) <= 16:
            raise ValueError("invalid chain length")
        moment = datetime.now(timezone.utc)
        if any(not cert.not_valid_before_utc <= moment < cert.not_valid_after_utc for cert in values):
            raise ValueError("certificate is outside its validity period")
        return values
    except (ValueError, TypeError) as exc:
        raise ValueError(label + " must contain current, valid PEM certificates") from exc


def _tls_identity(directory, label, *, health=False):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID
    source = _path(directory, label + " directory")
    # Existing deployment PKI bundles use server.* filenames for every leaf;
    # their actual EKU, not that filename, determines the identity's purpose.
    stem = "client" if health and (source / "client.crt").exists() else "server"
    certificate = _tls_source(source / (stem + ".crt"), label + " certificate")
    key = _tls_source(source / (stem + ".key"), label + " private key", secret=True)
    chain = _certificate_chain(certificate, label)
    try:
        private = serialization.load_pem_private_key(key, password=None)
        form = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if chain[0].public_key().public_bytes(*form) != private.public_key().public_bytes(*form):
            raise ValueError("certificate and private key do not match")
        usage = chain[0].extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        needed = ExtendedKeyUsageOID.CLIENT_AUTH if health else ExtendedKeyUsageOID.SERVER_AUTH
        if needed not in usage or health and ExtendedKeyUsageOID.SERVER_AUTH in usage:
            raise ValueError("certificate has the wrong dedicated TLS purpose")
        try:
            if chain[0].extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise ValueError("a CA certificate cannot be a TLS leaf identity")
        except x509.ExtensionNotFound:
            pass
    except (ValueError, TypeError, x509.ExtensionNotFound) as exc:
        raise ValueError(label + " requires a matching unencrypted key and the correct explicit TLS extended key usage") from exc
    return certificate, key, chain


def _san_matches(certificate, host):
    from cryptography import x509
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    try:
        return ipaddress.ip_address(host) in names.get_values_for_type(x509.IPAddress)
    except ValueError:
        host = host.encode("idna").decode().lower()
        return any(host == value.lower() or value.startswith("*.") and host.endswith(value[1:].lower())
                   and host.count(".") == value.count(".") for value in names.get_values_for_type(x509.DNSName))


def _verify_tls_chain(chain, authorities, label):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa, ec, dsa, ed25519, ed448, padding
    trusted = {cert.fingerprint(hashes.SHA256()) for cert in authorities}
    current, seen, intermediates = chain[0], set(), 0
    for _ in range(16):
        fingerprint = current.fingerprint(hashes.SHA256())
        if fingerprint in trusted:
            return
        if fingerprint in seen:
            break
        seen.add(fingerprint)
        verified = None
        for issuer in [*chain[1:], *authorities]:
            if current.issuer != issuer.subject:
                continue
            try:
                constraints = issuer.extensions.get_extension_for_class(x509.BasicConstraints).value
                if not constraints.ca or constraints.path_length is not None and intermediates > constraints.path_length:
                    continue
                try:
                    if not issuer.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign:
                        continue
                except x509.ExtensionNotFound:
                    pass
                key = issuer.public_key()
                if isinstance(key, rsa.RSAPublicKey):
                    key.verify(current.signature, current.tbs_certificate_bytes,
                               current.signature_algorithm_parameters or padding.PKCS1v15(), current.signature_hash_algorithm)
                elif isinstance(key, ec.EllipticCurvePublicKey):
                    key.verify(current.signature, current.tbs_certificate_bytes, ec.ECDSA(current.signature_hash_algorithm))
                elif isinstance(key, dsa.DSAPublicKey):
                    key.verify(current.signature, current.tbs_certificate_bytes, current.signature_hash_algorithm)
                elif isinstance(key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
                    key.verify(current.signature, current.tbs_certificate_bytes)
                else:
                    continue
                verified = issuer
                break
            except Exception:
                continue
        if verified is None:
            break
        current, intermediates = verified, intermediates + 1
    raise ValueError(label + " certificate chain does not verify against its configured CA")


def _native_tls(tls_dir, client_ca_file, health_client_dir, public_url, bind_ip, server_ca):
    """Validate immutable input bytes before creating any installation files."""
    if not tls_dir:
        if client_ca_file or health_client_dir:
            raise ValueError("Client CA and health-client material require --tls-dir")
        return {}, {}, None
    cert_raw, key_raw, chain = _tls_identity(tls_dir, "Agent server")
    public_host = urlsplit(public_url).hostname
    health_host = "127.0.0.1" if bind_ip == "0.0.0.0" else bind_ip
    if not _san_matches(chain[0], public_host) or not _san_matches(chain[0], health_host):
        raise ValueError("Agent server certificate SAN must cover both the public host and the actual local health-check host")
    files = {"agent-server/server.crt": cert_raw, "agent-server/server.key": key_raw}
    settings = {"MOTOR_AGENT_SERVER_CERT_FILE": "/run/motor-client/agent-server/server.crt",
                "MOTOR_AGENT_SERVER_KEY_FILE": "/run/motor-client/agent-server/server.key",
                "MOTOR_AGENT_REQUIRE_CLIENT_CERT": "false"}
    if server_ca:
        ca_raw = _tls_source(server_ca, "Agent public CA")
        _verify_tls_chain(chain, _certificate_chain(ca_raw, "Agent public CA"), "Agent server")
        files["agent-server/ca.crt"] = ca_raw
        settings["MOTOR_AGENT_SERVER_CA_FILE"] = "/run/motor-client/agent-server/ca.crt"
    if bool(client_ca_file) != bool(health_client_dir):
        raise ValueError("Native mTLS requires both --client-ca-file and an independent --health-client-dir")
    if client_ca_file:
        client_ca_raw = _tls_source(client_ca_file, "Agent client CA")
        authorities = _certificate_chain(client_ca_raw, "Agent client CA")
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        try:
            if any(not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca for cert in authorities):
                raise ValueError("Agent client CA bundle must contain only certificate authorities")
        except x509.ExtensionNotFound as exc:
            raise ValueError("Agent client CA bundle must contain explicit CA basic constraints") from exc
        health_crt, health_key, health_chain = _tls_identity(health_client_dir, "Agent health client", health=True)
        if health_chain[0].fingerprint(hashes.SHA256()) == chain[0].fingerprint(hashes.SHA256()):
            raise ValueError("Agent health client identity must be independent of the server identity")
        _verify_tls_chain(health_chain, authorities, "Agent health client")
        files.update({"agent-server/client-ca.crt": client_ca_raw, "agent-health/client.crt": health_crt,
                      "agent-health/client.key": health_key})
        settings.update(MOTOR_AGENT_REQUIRE_CLIENT_CERT="true",
                        MOTOR_AGENT_CLIENT_CA_FILE="/run/motor-client/agent-server/client-ca.crt",
                        MOTOR_AGENT_HEALTH_CLIENT_CERT_FILE="/run/motor-client/agent-health/client.crt",
                        MOTOR_AGENT_HEALTH_CLIENT_KEY_FILE="/run/motor-client/agent-health/client.key")
    from cryptography.hazmat.primitives import hashes
    return settings, files, {"server_certificate_sha256": chain[0].fingerprint(hashes.SHA256()).hex(),
                             "client_certificate_required": bool(client_ca_file)}


def _embedded(value, label: str) -> bytes:
    try:
        if not isinstance(value, str):
            raise ValueError
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(label + " must be valid embedded base64 data") from exc


def _kubeconfig(value) -> tuple[Path, bytes]:
    """Reject credentials that depend on unmounted files or executable plugins."""
    source = _private_file(value, "Kubeconfig")
    raw = source.read_bytes()
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError("Kubeconfig is not valid YAML") from exc
    if not isinstance(document, dict) or document.get("apiVersion") != "v1" or document.get("kind") != "Config":
        raise ValueError("Kubeconfig must be a v1 Config")
    entries = {}
    for field in ("contexts", "clusters", "users"):
        items = document.get(field)
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ValueError("Use a minified, flattened kubeconfig with one context, cluster and user")
        entries[field] = items[0]
    context_entry = entries["contexts"]
    context = context_entry.get("context")
    if (not isinstance(context, dict) or not context_entry.get("name")
            or document.get("current-context") != context_entry["name"]
            or context.get("cluster") != entries["clusters"].get("name")
            or context.get("user") != entries["users"].get("name")):
        raise ValueError("Kubeconfig current context must reference its named cluster and user")
    cluster = entries["clusters"].get("cluster")
    user = entries["users"].get("user")
    if not isinstance(cluster, dict) or not isinstance(user, dict):
        raise ValueError("Kubeconfig cluster and user settings are required")
    server = cluster.get("server")
    try:
        parsed = urlsplit(server) if isinstance(server, str) else None
        valid_server = (parsed and parsed.scheme == "https" and parsed.hostname
                        and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment
                        and (parsed.port is None or 1 <= parsed.port <= 65535))
    except ValueError:
        valid_server = False
    if not valid_server or any(ord(c) < 33 for c in server):
        raise ValueError("Kubeconfig API server must use a valid HTTPS URL")
    if cluster.get("insecure-skip-tls-verify") is not None and cluster.get("insecure-skip-tls-verify") is not False:
        raise ValueError("Kubeconfig must verify the API server TLS certificate")
    if any(key in cluster for key in ("certificate-authority", "proxy-url")):
        raise ValueError("Kubeconfig must embed its CA and must not depend on an external proxy")
    if any(key in user for key in ("exec", "auth-provider", "tokenFile", "token-file", "client-certificate", "client-key", "username", "password")):
        raise ValueError("Kubeconfig must use static embedded credentials without plugins, passwords or external files")
    if "certificate-authority-data" in cluster:
        try:
            ssl.create_default_context(cadata=_embedded(cluster["certificate-authority-data"], "Kubeconfig CA").decode("ascii"))
        except (OSError, UnicodeError, ssl.SSLError) as exc:
            raise ValueError("Kubeconfig embedded CA must be a valid PEM trust bundle") from exc
    token = user.get("token")
    certificate = user.get("client-certificate-data")
    key = user.get("client-key-data")
    if token is not None:
        if not isinstance(token, str) or not token or any(ord(c) < 33 for c in token) or certificate or key:
            raise ValueError("Kubeconfig must have one non-empty static token or one embedded client certificate/key pair")
    elif certificate and key:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        try:
            cert = x509.load_pem_x509_certificate(_embedded(certificate, "Kubeconfig client certificate"))
            private = serialization.load_pem_private_key(_embedded(key, "Kubeconfig client key"), password=None)
            public = lambda item: item.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            if public(cert.public_key()) != public(private.public_key()):
                raise ValueError
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError("Kubeconfig needs a current matching, unencrypted embedded client certificate/key pair") from exc
    else:
        raise ValueError("Kubeconfig requires a static token or embedded client certificate/key pair")
    return source, raw


def _credential_ring(token_file=None) -> dict:
    if token_file:
        source = _private_file(token_file, "Existing Agent token file")
        try:
            token = source.read_text(encoding="utf-8").strip()
        except UnicodeError as exc:
            raise ValueError("Existing Agent token must be UTF-8 text") from exc
        if token.startswith("{"):
            raise ValueError("Use a private plain token export as --token-file; existing credential rings must not be copied into a new identity")
        if not 24 <= len(token) <= 2000 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError("Existing Agent token must contain 24..2000 visible ASCII characters without whitespace")
    else:
        token = secrets.token_hex(32)
    return {"schema": 1, "disabled": False,
            "current": {"id": "agt_" + secrets.token_hex(12), "token": token, "expires_at": None},
            "previous": None}


def prepare(project_dir, data_dir, env_file, image, runtime, *, bind_ip="127.0.0.1", port=None,
            workspace_dir=None, kubeconfig=None, token_file=None, public_url=None,
            https_proxy_confirmed=False, agent_ca_file=None, cpus="1", memory="1g", pids=256,
            native_concurrency=1, tls_dir=None, client_ca_file=None, health_client_dir=None) -> dict:
    _require_linux_host()
    if port is None:
        port = 19083 if runtime == "kubernetes" else 19082
    if runtime not in {"docker", "kubernetes"}:
        raise ValueError("Agent runtime must be docker or kubernetes")
    if not isinstance(image, str) or not _IMAGE.fullmatch(image):
        raise ValueError("Production Agent image must be pinned by a SHA256 digest")
    project = _path(project_dir, "Project directory")
    data = _path(data_dir, "Agent data directory")
    env = _path(env_file, "Agent environment file")
    compose = project / f"web/compose.{runtime}-agent.yaml"
    if not compose.is_file():
        raise ValueError("The matching production Agent Compose definition is missing")
    if data == Path(data.anchor) or data.is_relative_to(project) or env.is_relative_to(project):
        raise ValueError("Use private Agent data and environment paths outside the project checkout")
    if env.is_relative_to(data):
        raise ValueError("Keep the host environment file outside the Agent-mounted data directory")
    _trusted_parents(data.parent, "Agent data directory")
    _trusted_parents(env.parent, "Agent environment file")
    if env.exists():
        raise ValueError("Agent environment already exists; nothing was overwritten")
    if data.exists() and (not data.is_dir() or any(data.iterdir())):
        raise ValueError("New Agent setup requires an empty data directory; do not reuse another product database or credentials")
    if data.exists() and os.name == "posix" and data.stat().st_uid not in {0, os.geteuid()}:
        raise ValueError("Agent data directory must be owned by root or the current user")
    if tls_dir and https_proxy_confirmed:
        raise ValueError("Choose native --tls-dir or a confirmed external proxy; do not declare both TLS termination modes")
    if tls_dir and not agent_ca_file and (Path(tls_dir) / "ca.crt").is_file():
        agent_ca_file = Path(tls_dir) / "ca.crt"
    public, ca = _public_endpoint(bind_ip, port, public_url, https_proxy_confirmed, agent_ca_file,
                                  native_tls=bool(tls_dir))
    tls_settings, tls_files, tls_metadata = _native_tls(tls_dir, client_ca_file, health_client_dir,
                                                      public, bind_ip, ca)
    resources = _resource_settings(cpus, memory, pids, native_concurrency)
    credential = _credential_ring(token_file)
    workspace = None
    kube_raw = None
    if runtime == "docker":
        if kubeconfig:
            raise ValueError("The Docker-only production Agent does not mount kubeconfig; prepare a separate Kubernetes Agent")
        if not workspace_dir:
            raise ValueError("Docker Agent requires an explicit absolute host workspace directory")
        workspace = _path(workspace_dir, "Docker host workspace")
        if (workspace == Path(workspace.anchor) or workspace.is_relative_to(project)
                or workspace.is_relative_to(data) or data.is_relative_to(workspace)
                or (workspace.exists() and not workspace.is_dir())):
            raise ValueError("Use a dedicated Docker workspace separate from project and Agent data")
        _trusted_parents(workspace, "Docker host workspace")
    else:
        if workspace_dir:
            raise ValueError("Kubernetes-only Agent must not receive a Docker workspace")
        if not kubeconfig:
            raise ValueError("Kubernetes Agent requires an explicit private kubeconfig")
        _, kube_raw = _kubeconfig(kubeconfig)
    settings = {"MOTOR_CONSOLE_IMAGE": image, "MOTOR_AGENT_DATA_DIR": str(data),
                "MOTOR_AGENT_READ_ONLY": "true",
                "MOTOR_AGENT_CREDENTIALS_HOST_PATH": str(data / "credentials"),
                "MOTOR_AGENT_CREDENTIALS_CONTAINER_PATH": _CREDENTIALS_CONTAINER,
                "MOTOR_AGENT_TOKEN_FILE": _CREDENTIALS_CONTAINER + "/agent-token.json",
                "MOTOR_AGENT_BIND_IP": bind_ip, "MOTOR_AGENT_PORT": port, **resources}
    settings.update(tls_settings)
    if workspace:
        settings["MOTOR_HOST_WORKSPACE_DIR"] = str(workspace)
    if kube_raw is not None:
        settings["MOTOR_KUBECONFIG_FILE"] = str(data / "config/kubeconfig")
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    lock = data / ".prepare-agent.lock"
    locked = False

    def mkdir(path):
        if not path.exists():
            path.mkdir(parents=True, mode=0o700)
            created_dirs.append(path)
        if path == data or path.is_relative_to(data):
            path.chmod(0o700)

    def write(path, content):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        created_files.append(path)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        path.chmod(0o600)

    try:
        mkdir(data)
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(descriptor)
        locked = True
        if any(item != lock for item in data.iterdir()):
            raise ValueError("Agent data changed during preparation; no existing files were overwritten")
        for child in ("credentials", "profiles", "client-secrets", "config"):
            mkdir(data / child)
        write(data / "credentials/agent-token.json", (json.dumps(credential, indent=2) + "\n").encode())
        for relative, raw in tls_files.items():
            target = data / "client-secrets" / relative
            mkdir(target.parent)
            write(target, raw)
        if kube_raw is not None:
            write(data / "config/kubeconfig", kube_raw)
        if workspace:
            mkdir(workspace)
        mkdir(env.parent)
        content = "# Private local production Agent settings; do not commit.\n"
        content += "\n".join(f"{key}='{value}'" for key, value in settings.items()) + "\n"
        write(env, content.encode())
    except Exception:
        # Only our exclusively created files/empty directories are candidates.
        # Never recursively remove a supplied path or another install's files.
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        if locked:
            lock.unlink(missing_ok=True)
            locked = False
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass
        raise
    finally:
        if locked:
            lock.unlink(missing_ok=True)
    command = "docker compose --env-file " + shlex.quote(str(env)) + " -f " + shlex.quote(str(compose))
    return {"prepared": True, "services_started": False, "runtime": runtime,
            "environment_file": str(env), "data_directory": str(data), "public_url": public,
            "credential_file": str(data / "credentials/agent-token.json"),
            "credential_export_command": "PYTHONPATH=" + shlex.quote(str(project / "web"))
            + " python3 -m backend.agent_credentials export --file "
            + shlex.quote(str(data / "credentials/agent-token.json")) + " --output /secure/motor-console/agent-token-export.txt",
            "limits": resources, "validation": "local_configuration_only",
            "agent_ca_file_for_control": ca,
            "tls_mode": ("native-mtls" if client_ca_file else "native-tls") if tls_dir else "external-proxy" if https_proxy_confirmed else "loopback-http",
            "native_tls_material": tls_metadata,
            "tls_listener_started": False,
            "requires_https_entrypoint": public.startswith("https:"),
            "next_commands": [command + " config --quiet", command + " up -d agent", command + " ps agent"],
            "remaining_checks": ["Reviewed image availability and architecture", "Trusted TLS handshake from control to the public Agent URL",
                                 "Agent health, credential authentication and profile compatibility",
                                 "Runtime permissions, native rendering and separate Motor/NPU business acceptance"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--runtime", choices=("docker", "kubernetes"), required=True)
    parser.add_argument("--bind-ip", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--workspace-dir")
    parser.add_argument("--kubeconfig")
    parser.add_argument("--token-file")
    parser.add_argument("--public-url")
    parser.add_argument("--https-proxy-confirmed", action="store_true")
    parser.add_argument("--agent-ca-file")
    parser.add_argument("--tls-dir", help="Native Agent TLS directory containing server.crt/server.key; optional ca.crt")
    parser.add_argument("--client-ca-file", help="Trusted client CA for native mTLS; requires an independent health client identity")
    parser.add_argument("--health-client-dir", help="Independent CLIENT_AUTH-only client.crt/client.key (server.* bundle names also accepted)")
    parser.add_argument("--cpus", default="1")
    parser.add_argument("--memory", default="1g")
    parser.add_argument("--pids", type=int, default=256)
    parser.add_argument("--native-concurrency", type=int, default=1)
    args = parser.parse_args()
    try:
        result = prepare(**vars(args))
    except (OSError, ValueError) as exc:
        print(json.dumps({"prepared": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
