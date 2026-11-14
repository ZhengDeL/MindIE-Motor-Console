"""Strict service-scoped Motor HTTP, shared by control and execution agents.

The caller supplies stored service/node records, never a user-selected URL.
Fleet's address and API-key contracts are imported lazily to avoid cycles.
"""
from __future__ import annotations

import asyncio
import math
import os
import time
from pathlib import Path, PurePosixPath
import ssl
import zlib
from typing import Any
from urllib.parse import urlsplit

import httpx

from .network_budget import run_network


def resolve_file(value: str, purpose: str, *, host_root: str | Path | None = None) -> str:
    from .fleet import FleetError
    if not isinstance(value, str) or not value.strip():
        raise FleetError("Motor " + purpose + " file path is not configured")
    direct = Path(value)
    if direct.is_file():
        return str(direct)
    fragment = PurePosixPath(value)
    if fragment.is_absolute() and ".." not in fragment.parts:
        root = Path(host_root or os.getenv("MOTOR_HOST_ROOT", "/host")).resolve()
        candidate = root.joinpath(*fragment.parts[1:]).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return str(candidate)
    raise FleetError("Motor " + purpose + " file is unavailable to this execution environment")


def client_tls_context(tls: dict, *, host_root: str | Path | None = None) -> ssl.SSLContext | bool:
    """Read only deployment-owned client material, retaining peer verification.

    Native TLS JSON configures the Motor server. Its paths never authorize this
    client to read files on the control plane or execution Agent.
    """
    from .fleet import FleetError
    require_mtls = os.getenv("MOTOR_CLIENT_MTLS_REQUIRED", "false").lower()
    if require_mtls not in {"true", "false"}:
        raise FleetError("MOTOR_CLIENT_MTLS_REQUIRED must be true or false")
    if tls.get("enable_tls") is not True:
        if require_mtls == "true":
            raise FleetError("Motor deployment client policy requires TLS and trusted mTLS material")
        return True
    try:
        ca = os.getenv("MOTOR_CA_BUNDLE")
        resolved_ca = resolve_file(ca, "TLS CA", host_root=host_root) if ca else None
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=resolved_ca)
        context.keylog_filename = None
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        certificate = os.getenv("MOTOR_CLIENT_CERT_FILE")
        key = os.getenv("MOTOR_CLIENT_KEY_FILE")
        if require_mtls == "true" and not (certificate and key):
            raise FleetError("Motor mTLS requires deployment-owned MOTOR_CLIENT_CERT_FILE and MOTOR_CLIENT_KEY_FILE")
        if bool(certificate) != bool(key):
            raise FleetError("Motor TLS client certificate and private key must be configured together")
        if certificate:
            password_file = os.getenv("MOTOR_CLIENT_KEY_PASSWORD_FILE")
            password = ""
            if password_file:
                password = Path(resolve_file(password_file, "TLS client-key password", host_root=host_root)).read_text(encoding="utf-8").rstrip("\r\n")
            context.load_cert_chain(certfile=resolve_file(certificate, "TLS client certificate", host_root=host_root),
                                    keyfile=resolve_file(key, "TLS client private key", host_root=host_root),
                                    password=lambda: password)
        crl = os.getenv("MOTOR_CLIENT_CRL_FILE")
        if crl:
            context.load_verify_locations(cafile=resolve_file(crl, "TLS certificate revocation list", host_root=host_root))
            context.verify_flags |= ssl.VERIFY_CRL_CHECK_CHAIN
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise FleetError("Motor TLS client context must verify the server and hostname")
        return context
    except (OSError, ValueError) as exc:
        raise FleetError("Motor TLS material could not be loaded: " + type(exc).__name__) from exc


def management_headers(*, host_root: str | Path | None = None) -> dict[str, str]:
    """A stored service cannot choose local files to send as a client credential."""
    from .fleet import FleetError
    key_file = os.getenv("MOTOR_MANAGEMENT_KEY_FILE", "")
    if not key_file:
        raise FleetError("Motor management authentication requires deployment-owned MOTOR_MANAGEMENT_KEY_FILE")
    try:
        path = Path(resolve_file(key_file, "management API Key", host_root=host_root))
        if path.stat().st_size > 16384:
            raise FleetError("Motor management client credential exceeds the supported size")
        value = path.read_text(encoding="utf-8").strip()
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise FleetError("Motor management client credential is empty or contains control characters")
        from .observability import register_secret
        register_secret(value)
        return {"Content-Type": "application/json", "X-Motor-Management-Key": value}
    except (OSError, UnicodeError) as exc:
        raise FleetError("Motor management client credential is unavailable") from exc


def request_motor(service: dict, nodes: dict, plane: str, path: str, *, method: str = "GET",
                  body: dict | None = None, timeout: float = 8,
                  host_root: str | Path | None = None,
                  max_response_bytes: int | None = None) -> Any:
    """Request one configured Motor plane with no redirects, proxy env or TLS fallback."""
    from .fleet import FleetError, _motor_headers, endpoint
    from .deployment_view import runtime_service_view
    if service.get("id") and service.get("status") in {"draft", "stopped", "removing", "removed"}:
        raise FleetError("This service has no active native endpoint; cached history may be viewed without contacting a reused port")
    service = runtime_service_view(service)
    parsed = urlsplit(path)
    if plane not in {"infer", "mgmt", "obs"} or not path.startswith("/") or path.startswith("//") \
            or parsed.scheme or parsed.netloc or parsed.fragment or ".." in parsed.path.split("/"):
        raise FleetError("Motor request must use a fixed relative path on a configured service plane")
    coordinator = service["user_config"].get("motor_coordinator_config") or {}
    if not isinstance(coordinator, dict):
        raise FleetError("Motor Coordinator configuration must be an object")
    # Coordinator observability uses management TLS. Controller's independent
    # observability_tls_config must not be used for a Coordinator endpoint.
    tls_name = "infer_tls_config" if plane == "infer" else "mgmt_tls_config"
    deploy = service["user_config"].get("motor_deploy_config") or {}
    if not isinstance(deploy, dict) or not isinstance(deploy.get("tls_config") or {}, dict):
        raise FleetError("Motor deployment/TLS configuration must be an object")
    tls = (deploy.get("tls_config") or {}).get(tls_name, coordinator.get(tls_name) or {})
    if not isinstance(tls, dict):
        raise FleetError("Motor plane TLS configuration must be an object")
    base = endpoint(service, nodes, plane)
    if not base:
        raise FleetError("No accessible Coordinator endpoint is configured for this service")
    base = ("https" if tls.get("enable_tls") is True else "http") + base[base.index(":"):]
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise FleetError("Motor request time budget is invalid")
    if max_response_bytes is not None and (type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 2 * 1024 * 1024):
        raise FleetError("Motor response limit must be 1 byte to 2 MiB")
    limit = max_response_bytes if max_response_bytes is not None else 8 * 1024 * 1024
    deadline = time.monotonic() + timeout
    write_started = False

    def prepare():
        verify = client_tls_context(tls, host_root=host_root)
        if verify is True:
            # This plane is HTTP and redirects are forbidden. Supplying an
            # explicit strict context avoids loading an unused system CA store
            # in HTTPX's constructor; HTTPS planes load verified trust above.
            verify = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        key_config = coordinator.get("mgmt_api_key_config") or {}
        headers = management_headers(host_root=host_root) if plane != "infer" and key_config.get("enable_api_key") else _motor_headers(service, plane)
        return verify, {**headers, "Accept-Encoding": "identity"}

    def interpret(response):
        if not response.is_success:
            raise FleetError(f"Motor {plane} returned HTTP {response.status_code}",
                             status_code=response.status_code,
                             outcome_unknown=write_started and response.status_code >= 500)
        if "json" in response.headers.get("content-type", ""):
            result = response.json()
            if isinstance(result, dict) and (result.get("error") or result.get("code", 200) not in (0, 200)):
                raise FleetError("Motor returned an application-level error")
            return result
        return response.text

    async def perform():
        nonlocal write_started
        verify, headers = await asyncio.to_thread(prepare)
        async with httpx.AsyncClient(timeout=timeout, verify=verify, trust_env=False, follow_redirects=False,
                                     limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
            write_started = method.upper() not in {"GET", "HEAD", "OPTIONS"}
            async with client.stream(method, base + path, json=body, headers=headers) as streamed:
                encoding = streamed.headers.get("content-encoding", "").lower().strip()
                if encoding not in {"", "identity", "gzip"}:
                    raise FleetError("Motor response uses an unsupported content encoding", outcome_unknown=write_started)
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" and not streamed.is_stream_consumed else None
                content, raw_size = bytearray(), 0
                def add(chunk):
                    nonlocal raw_size
                    raw_size += len(chunk)
                    if raw_size > limit:
                        raise FleetError("Motor response exceeds its size limit", outcome_unknown=write_started)
                    decoded = decoder.decompress(chunk, limit - len(content) + 1) if decoder else chunk
                    if len(content) + len(decoded) > limit or decoder and decoder.unconsumed_tail:
                        raise FleetError("Motor decoded response exceeds its size limit", outcome_unknown=write_started)
                    content.extend(decoded)
                if streamed.is_stream_consumed:
                    add(streamed.content)
                else:
                    async for chunk in streamed.aiter_raw():
                        add(chunk)
                if decoder and (not decoder.eof or decoder.unused_data):
                    raise FleetError("Motor compressed response is incomplete or invalid", outcome_unknown=write_started)
                decoded_headers = {key: value for key, value in streamed.headers.items()
                                   if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}}
                response = httpx.Response(streamed.status_code, content=bytes(content), headers=decoded_headers)
        return await asyncio.to_thread(interpret, response)

    try:
        return run_network(perform, deadline)
    except TimeoutError as exc:
        raise FleetError("Motor service request exceeded its total time budget", outcome_unknown=write_started) from exc
    except (httpx.HTTPError, OSError, ValueError, RecursionError, zlib.error) as exc:
        raise FleetError("Motor service request failed: " + type(exc).__name__, outcome_unknown=write_started) from exc
