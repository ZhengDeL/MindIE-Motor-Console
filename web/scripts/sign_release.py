"""Offline publisher tool. Signing private keys never belong on the control plane."""
import argparse
import base64
import json
from pathlib import Path
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.releases import canonical_manifest, validate_release_manifest


def sign(manifest_file, private_key_file, output=None):
    manifest = Path(manifest_file)
    if not manifest.is_file() or manifest.stat().st_size > 65536:
        raise ValueError("Release manifest is missing or too large")
    with manifest.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Release manifest is missing or too large")
    value = validate_release_manifest(json.loads(raw.decode("utf-8")))
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
