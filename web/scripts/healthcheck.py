"""Process/storage/credential readiness; never represents NPU/model acceptance."""
import json
import os
from pathlib import Path
import ssl
import sys
from urllib.request import build_opener, HTTPSHandler, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.version import PRODUCT_ID

mode = os.getenv("MOTOR_MODE", "control")
if mode == "agent":
    host = os.getenv("MOTOR_AGENT_BIND_IP", "127.0.0.1")
    port = os.getenv("MOTOR_AGENT_PORT", "19082")
elif mode == "control":
    host = os.getenv("MOTOR_LISTEN_HOST") or "127.0.0.1"
    port = os.getenv("MOTOR_LISTEN_PORT") or "8000"
else:
    host, port = "127.0.0.1", "8000"
host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
if ":" in host:
    host = f"[{host}]"
scheme = "https" if mode == "agent" and os.getenv("MOTOR_AGENT_SERVER_CERT_FILE") else "http"
context = None
if scheme == "https":
    context = ssl.create_default_context(cafile=os.getenv("MOTOR_AGENT_SERVER_CA_FILE") or None)
    context.keylog_filename = None
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    certificate = os.getenv("MOTOR_AGENT_HEALTH_CLIENT_CERT_FILE", "")
    key = os.getenv("MOTOR_AGENT_HEALTH_CLIENT_KEY_FILE", "")
    if bool(certificate) != bool(key):
        raise SystemExit("Health client certificate and key must be configured together")
    if certificate:
        context.load_cert_chain(certificate, key, password=lambda: "")
    elif os.getenv("MOTOR_AGENT_REQUIRE_CLIENT_CERT", "false").lower() == "true":
        raise SystemExit("mTLS readiness requires a dedicated health client identity")
opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
with opener.open(f"{scheme}://{host}:{port}/ready", timeout=4) as response:
    if response.status != 200:
        raise SystemExit(1)
    raw = response.read(65537)
    if len(raw) > 65536:
        raise SystemExit(1)
    body = json.loads(raw)
    if not isinstance(body, dict) or body.get("status") != "ready" or body.get("product_id") != PRODUCT_ID:
        raise SystemExit(1)
