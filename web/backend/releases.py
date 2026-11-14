"""Canonical, signed platform release manifests shared by API and host CLI."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import re
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .version import PRODUCT_ID

_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_RELEASE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}\Z")
_IMAGE = re.compile(r"(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})\Z")


def version_tuple(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError("A stable major.minor.patch release is required")
    return tuple(map(int, value.split(".")))


def canonical_manifest(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def read_release(directory: Path, release_id: str, key_file: str | Path | None) -> dict:
    if not _RELEASE.fullmatch(release_id): raise ValueError("Invalid release ID")
    root = directory.resolve()
    path = (root / (release_id + ".json")).resolve()
    signature_path = (root / (release_id + ".sig")).resolve()
    if not path.is_relative_to(root) or not signature_path.is_relative_to(root): raise ValueError("Release path escapes its configured directory")
    if not key_file: raise ValueError("No trusted release public key is configured")
    if not path.is_file() or path.stat().st_size > 65536: raise ValueError("Release manifest is missing or too large")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict): raise ValueError("Invalid release manifest object")
    try:
        signature = base64.b64decode(signature_path.read_text(encoding="ascii").strip(), validate=True)
        public_key = serialization.load_pem_public_key(Path(key_file).read_bytes())
        if not isinstance(public_key, Ed25519PublicKey): raise ValueError("An Ed25519 release key is required")
        public_key.verify(signature, canonical_manifest(document))
    except (OSError, InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("Release signature verification failed") from exc
    if type(document.get("release_schema")) is not int or document["release_schema"] != 1 or document.get("id") != release_id:
        raise ValueError("Unsupported release schema or mismatched identity")
    if document.get("product_id") != PRODUCT_ID:
        raise ValueError("Release manifest belongs to another product or has no Motor Console identity")
    version_tuple(document.get("version"))
    if not isinstance(document.get("image"), str) or not _IMAGE.fullmatch(document["image"]):
        raise ValueError("Release image must be pinned by SHA256 digest")
    if "web_image" in document and (not isinstance(document["web_image"], str) or not _IMAGE.fullmatch(document["web_image"])):
        raise ValueError("Independent Web release image must be pinned by SHA256 digest")
    for field in ("agent_protocol", "db_schema_min", "db_schema_max"):
        if type(document.get(field)) is not int or document[field] < 0: raise ValueError("Invalid release compatibility range")
    if document["db_schema_min"] > document["db_schema_max"]: raise ValueError("Invalid database compatibility range")
    return {**document, "manifest_sha256": hashlib.sha256(canonical_manifest(document)).hexdigest()}
