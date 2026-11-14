"""Offline publisher tool. Signing private keys never belong on the control plane."""
import argparse
import base64
import json
from pathlib import Path
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.releases import canonical_manifest
from backend.version import PRODUCT_ID


def sign(manifest_file, private_key_file, output=None):
    manifest = Path(manifest_file)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("product_id") != PRODUCT_ID or value.get("release_schema") != 1:
        raise ValueError("Only a product-bound Motor Console release_schema=1 manifest may be signed")
    key = serialization.load_pem_private_key(Path(private_key_file).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey): raise ValueError("An Ed25519 publisher key is required")
    destination = Path(output) if output else manifest.with_suffix(".sig")
    if destination.exists(): raise ValueError("Signature output already exists; review the previous artifact")
    signature = base64.b64encode(key.sign(canonical_manifest(value))) + b"\n"
    with destination.open("xb") as stream: stream.write(signature)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    print(str(sign(args.manifest, args.private_key, args.output)))
