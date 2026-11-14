"""Control-to-Agent transport policy, independent of native Motor API TLS."""
from __future__ import annotations

import ipaddress
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import re
import ssl
import threading
from urllib.parse import urlsplit

import certifi


_context_lock = threading.Lock()
_context_cache: tuple[tuple, ssl.SSLContext] | None = None
_node_context_cache: OrderedDict[tuple, ssl.SSLContext] = OrderedDict()


class AgentSecurityError(ValueError):
    """A deployment-owned transport setting is invalid; messages contain no secrets."""


def _mtls_required() -> bool:
    value = os.getenv("MOTOR_AGENT_MTLS_REQUIRED", "false").lower()
    if value not in {"true", "false"}:
        raise AgentSecurityError("MOTOR_AGENT_MTLS_REQUIRED must be true or false")
    return value == "true"


def validate_agent_url(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value):
        raise AgentSecurityError("Agent URL is invalid")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise AgentSecurityError("Agent URL or port is invalid") from None
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise AgentSecurityError("Agent URL must be HTTP(S), without credentials, query or fragment")
    if (not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*/?", parsed.path)
            or any(part in {".", ".."} for part in parsed.path.split("/"))):
        raise AgentSecurityError("Agent URL path must contain literal path segments")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9.])?", host):
            raise AgentSecurityError("Agent hostname is invalid") from None
    if address and (address.is_link_local or address.is_multicast or address.is_unspecified):
        raise AgentSecurityError("Agent address must not be link-local, multicast or unspecified")
    if parsed.scheme == "http":
        if _mtls_required():
            raise AgentSecurityError("Agent mTLS policy requires an HTTPS endpoint")
        literal_loopback = address is not None and (
            address.version == 4 and address.is_loopback
            or address.version == 6 and str(address) == "::1")
        if os.getenv("MOTOR_ENV") == "production" and not literal_loopback:
            raise AgentSecurityError("Production remote Agents require HTTPS; HTTP permits only literal loopback IPs")
    return value.rstrip("/")


def _material_stamp(value: str | None) -> tuple:
    if not value:
        return ()
    try:
        info = Path(value).lstat()
        return (value, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                info.st_ctime_ns, info.st_mode, getattr(info, "st_uid", None))
    except OSError:
        raise AgentSecurityError("Agent TLS material is unavailable") from None


def agent_tls_context(node_ca_pem: str | None = None) -> ssl.SSLContext:
    """Load only deployment-owned Agent trust/client identity, always verifying peers.

    A bounded single-entry cache observes atomic file replacement on the next
    request while avoiding repeated CA parsing on ordinary inventory requests.
    Reverse proxies must verify the client certificate and protect their backend;
    sending a client certificate alone does not configure the Agent server.
    """
    ca = os.getenv("MOTOR_AGENT_CA_BUNDLE") or certifi.where()
    certificate = os.getenv("MOTOR_AGENT_CLIENT_CERT_FILE") or None
    key = os.getenv("MOTOR_AGENT_CLIENT_KEY_FILE") or None
    password_file = os.getenv("MOTOR_AGENT_CLIENT_KEY_PASSWORD_FILE") or None
    required = _mtls_required()
    if bool(certificate) != bool(key) or password_file and not key:
        raise AgentSecurityError("Agent client certificate and private key must be configured together")
    if required and not certificate:
        raise AgentSecurityError("Agent mTLS requires an independent client certificate and private key")
    identity_stamp = (required, *(_material_stamp(value) for value in (certificate, key, password_file)))
    if node_ca_pem is not None:
        if not isinstance(node_ca_pem, str) or not node_ca_pem:
            raise AgentSecurityError("Node Agent CA is invalid")
        from .agent_trust import MAX_AGENT_CA_BYTES, validate_agent_ca
        if len(node_ca_pem.encode("utf-8")) > MAX_AGENT_CA_BYTES:
            raise AgentSecurityError("Node Agent CA exceeds its size limit")
        stamp = (hashlib.sha256(node_ca_pem.encode("utf-8")).digest(), *identity_stamp)
        with _context_lock:
            cached = _node_context_cache.get(stamp)
            if cached is not None:
                _node_context_cache.move_to_end(stamp)
                return cached
            try:
                pem = validate_agent_ca(node_ca_pem)
            except ValueError:
                raise AgentSecurityError("Node Agent CA certificate is invalid") from None
            context = _build_context(None, certificate, key, password_file, ca_pem=pem)
            _node_context_cache[stamp] = context
            while len(_node_context_cache) > 32:
                _node_context_cache.popitem(last=False)
            return context
    stamp = (required, _material_stamp(ca), *identity_stamp[1:])
    global _context_cache
    with _context_lock:
        if _context_cache is not None and _context_cache[0] == stamp:
            return _context_cache[1]
        context = _build_context(ca, certificate, key, password_file)
        _context_cache = (stamp, context)
        return context


def _build_context(ca, certificate, key, password_file, *, ca_pem=None) -> ssl.SSLContext:
    try:
        context = ssl.create_default_context(cafile=ca, cadata=ca_pem)
        # Do not allow a process-level debugging variable to log Agent TLS keys.
        context.keylog_filename = None
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if certificate:
            from .agent_credentials import read_private_file
            # The key must be private even when the public certificate is shared.
            read_private_file(Path(key), maximum=128 * 1024)
            password = None
            if password_file:
                password = read_private_file(Path(password_file), maximum=16384).decode("utf-8").rstrip("\r\n")
                if not password or "\x00" in password:
                    raise ValueError("invalid password file")
            # A callback prevents OpenSSL from prompting on an encrypted key when
            # no password file was supplied (important for unattended services).
            context.load_cert_chain(certificate, key, password=lambda: password or "")
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise AgentSecurityError("Agent TLS must verify the server certificate and hostname")
        return context
    except (OSError, ValueError, UnicodeError) as exc:
        if isinstance(exc, AgentSecurityError):
            raise
        raise AgentSecurityError("Agent TLS trust or client identity could not be loaded") from None
