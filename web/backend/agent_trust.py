"""Bounded public CA material scoped to one registered Agent, never a file path."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re

from cryptography import x509
from cryptography.hazmat.primitives import serialization


MAX_AGENT_CA_BYTES = 64 * 1024
_CERTIFICATE = re.compile(r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----")


def validate_agent_ca(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_AGENT_CA_BYTES
            or "PRIVATE KEY" in value or "\x00" in value):
        raise ValueError("Agent CA must contain bounded public PEM certificates only")
    blocks = _CERTIFICATE.findall(value)
    if not 1 <= len(blocks) <= 4 or _CERTIFICATE.sub("", value).strip():
        raise ValueError("Agent CA must contain one to four public PEM certificates")
    normalized = []
    moment = datetime.now(timezone.utc)
    for block in blocks:
        try:
            certificate = x509.load_pem_x509_certificate(block.encode("ascii"))
            if not certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise ValueError
            if not certificate.not_valid_before_utc <= moment < certificate.not_valid_after_utc:
                raise ValueError
            try:
                if not certificate.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign:
                    raise ValueError
            except x509.ExtensionNotFound:
                pass
            normalized.append(certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"))
        except (ValueError, UnicodeError, x509.ExtensionNotFound):
            raise ValueError("Agent CA certificate must be valid, current and authorized to sign certificates") from None
    return "".join(normalized)


def agent_ca_summary(value: str | None) -> dict:
    if not value:
        return {"kind": "deployment_trust"}
    try:
        pem = validate_agent_ca(value)
        certificates = x509.load_pem_x509_certificates(pem.encode("ascii"))
        return {"kind": "node_ca", "sha256": hashlib.sha256(pem.encode("ascii")).hexdigest(),
                "ca_expires_at": min(c.not_valid_after_utc for c in certificates).isoformat(),
                "certificates": len(certificates)}
    except (ValueError, TypeError, UnicodeError):
        return {"kind": "invalid_node_ca"}
