"""Shared HTTPS server-identity checks for deployment preparation tools."""
from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID


MAX_TLS_FILE_BYTES = 65536


def validate_server_identity(cert_file: str | Path, key_file: str | Path, hostname: str) -> None:
    """Check the leaf identity, not CA trust or live HTTPS reachability.

    Callers own installation-path and filesystem-permission policy. This
    function reads bounded material without writing or rotating identities.
    """
    paths = (Path(cert_file), Path(key_file))
    if not isinstance(hostname, str) or not hostname or any(c.isspace() or ord(c) < 32 for c in hostname):
        raise ValueError("HTTPS origin must contain a valid hostname or IP address")
    materials = []
    for path in paths:
        if not path.is_file() or path.is_symlink() or not 1 <= path.stat().st_size <= MAX_TLS_FILE_BYTES:
            raise ValueError("Server TLS files must be bounded regular certificate and private-key files")
        with path.open("rb") as stream:
            raw = stream.read(MAX_TLS_FILE_BYTES + 1)
        if not 1 <= len(raw) <= MAX_TLS_FILE_BYTES:
            raise ValueError("Server TLS files must be bounded regular certificate and private-key files")
        materials.append(raw)
    try:
        certificate = x509.load_pem_x509_certificate(materials[0])
        private_key = serialization.load_pem_private_key(materials[1], password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError("Server TLS requires a valid PEM certificate and an unencrypted private key") from exc
    form = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if certificate.public_key().public_bytes(*form) != private_key.public_key().public_bytes(*form):
        raise ValueError("Server TLS certificate and private key do not match")
    instant = datetime.now(timezone.utc)
    if not certificate.not_valid_before_utc <= instant < certificate.not_valid_after_utc:
        raise ValueError("Server TLS certificate is outside its validity period")
    try:
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        constraints = None
    if constraints is not None and constraints.ca:
        raise ValueError("Use a leaf server certificate and its key, never a CA certificate or signing key")
    try:
        purpose = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        purpose = None
    if purpose is not None and not ({ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.ANY_EXTENDED_KEY_USAGE} & set(purpose)):
        raise ValueError("Server TLS certificate does not permit TLS server authentication")
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound as exc:
        raise ValueError("Server TLS certificate must contain a subjectAltName extension") from exc
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        name = hostname.encode("idna").decode("ascii").lower()
        matches = any(name == item.lower() or item.startswith("*.") and name.endswith(item[1:].lower())
                      and name.count(".") == item.count(".")
                      for item in names.get_values_for_type(x509.DNSName))
    else:
        matches = address in names.get_values_for_type(x509.IPAddress)
    if not matches:
        raise ValueError("Server TLS certificate SAN does not cover the public origin")
