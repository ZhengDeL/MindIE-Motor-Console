"""Single-worker execution Agent with optional deployment-owned direct TLS."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import ssl

from .agent_credentials import read_private_file


def transport_security() -> dict:
    """Describe the explicit listener policy, never infer mTLS from a CA path."""
    configured = os.getenv("MOTOR_AGENT_REQUIRE_CLIENT_CERT", "false").lower()
    if configured not in {"true", "false"}:
        raise ValueError("MOTOR_AGENT_REQUIRE_CLIENT_CERT must be true or false")
    certificate = os.getenv("MOTOR_AGENT_SERVER_CERT_FILE", "")
    key = os.getenv("MOTOR_AGENT_SERVER_KEY_FILE", "")
    if bool(certificate) != bool(key):
        raise ValueError("Agent server certificate and private key must be configured together")
    require_client = configured == "true"
    if require_client and not certificate:
        raise ValueError("Agent client-certificate verification requires a TLS listener")
    return {"tls_enabled": bool(certificate), "client_certificate_required": require_client}


def tls_options() -> dict:
    policy = transport_security()
    certificate = os.getenv("MOTOR_AGENT_SERVER_CERT_FILE", "")
    key = os.getenv("MOTOR_AGENT_SERVER_KEY_FILE", "")
    ca = os.getenv("MOTOR_AGENT_SERVER_CA_FILE", "")
    client_ca = os.getenv("MOTOR_AGENT_CLIENT_CA_FILE", "") or ca
    if not certificate and not key:
        if ca:
            raise ValueError("Agent server CA requires a server certificate and key")
        return {}
    if not certificate or not key:
        raise ValueError("Agent direct TLS requires both server certificate and private key")
    if policy["client_certificate_required"] and not client_ca:
        raise ValueError("Agent client-certificate verification requires an explicit trusted client CA")
    for value in (certificate, key, ca, client_ca if policy["client_certificate_required"] else ""):
        if value and (not Path(value).is_absolute() or "\x00" in value):
            raise ValueError("Agent server TLS material must use absolute private deployment paths")
    read_private_file(Path(key), maximum=128 * 1024)
    # Validate the pair before the socket is opened; an invalid key must never
    # silently turn a configured HTTPS listener into plain HTTP.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.keylog_filename = None
    context.load_cert_chain(certificate, key, password=lambda: "")
    if ca:
        ssl.create_default_context(cafile=ca)
    result = {"ssl_certfile": certificate, "ssl_keyfile": key,
              "ssl_version": ssl.PROTOCOL_TLS_SERVER}
    if policy["client_certificate_required"]:
        trust = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=client_ca)
        if not trust.get_ca_certs():
            raise ValueError("Agent client CA bundle contains no certificate authority")
        result.update(ssl_ca_certs=client_ca, ssl_cert_reqs=ssl.CERT_REQUIRED)
    return result


def main() -> None:
    import uvicorn
    host = os.getenv("MOTOR_AGENT_BIND_IP", "127.0.0.1")
    ipaddress.ip_address(host)
    port = int(os.getenv("MOTOR_AGENT_PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("Agent port must be between 1 and 65535")
    configuration = uvicorn.Config("backend.app:app", host=host, port=port, workers=1,
                                   access_log=False, limit_concurrency=64, **tls_options())
    configuration.load()
    if configuration.ssl:
        configuration.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
        configuration.ssl.keylog_filename = None
    uvicorn.Server(configuration).run()


if __name__ == "__main__":
    main()
