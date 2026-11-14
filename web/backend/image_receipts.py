"""Strict, credential-free projection of the durable Agent image protocol."""
from __future__ import annotations

from datetime import datetime
import re

from .observability import redact

_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_STATES = {"accepted": "pending", "running": "pending", "failed": "failed", "interrupted": "unknown"}


class ImageReceiptError(ValueError):
    pass


def image_operation_unresolved(value: dict) -> bool:
    state = value.get("operation_status", value.get("status"))
    if state == "completed":
        return False
    return (state in {"accepted", "running"} or value.get("resources_may_exist") is True
            or value.get("status") == "unknown"
            or state not in {"failed", "interrupted"} and value.get("resources_may_exist") is not False)


def public_image_operation(value: dict) -> dict:
    """Old/private fields are never exported by the task history endpoint."""
    names = {"id", "operation_id", "node_id", "action", "reference", "operation_status", "status",
             "created_at", "updated_at", "resources_may_exist", "received_at", "started_at", "finished_at",
             "replayed", "receipt_unavailable"}
    result = {name: item for name, item in value.items() if name in names}
    for name in ("message", "error_code"):
        if isinstance(value.get(name), str): result[name] = redact(value[name][:2048])
    raw = value.get("result")
    if isinstance(raw, dict):
        try:
            projected = project_image_receipt({**value, "service_id": ""}, value)
            result["result"] = projected["result"]
        except (ImageReceiptError, KeyError, TypeError):
            result["result_unverified"] = True
    return result


def _text(value, maximum, name):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ImageReceiptError("Invalid image receipt " + name)
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ImageReceiptError("Invalid image receipt Unicode") from exc
    return value


def project_image_receipt(value: dict, intent: dict) -> dict:
    """A status flag alone never acknowledges an image identity or side effect."""
    if (not isinstance(value, dict) or value.get("operation_id") != intent["id"]
            or value.get("node_id") != intent["node_id"] or value.get("action") != intent["action"]
            or value.get("service_id") != ""):
        raise ImageReceiptError("Image receipt identity differs from the persisted intent")
    action = intent["action"]
    if action not in {"image_pull", "image_remove"}:
        raise ImageReceiptError("Unsupported image receipt action")
    state = value.get("operation_status")
    if not isinstance(state, str) or state not in {*_STATES, "completed"}:
        raise ImageReceiptError("Invalid image receipt operation state")
    resources = value.get("resources_may_exist")
    if type(resources) is not bool:
        raise ImageReceiptError("Image receipt resource uncertainty must be explicit")
    status = value.get("status")
    expected_status = ("ready" if action == "image_pull" else "removed") if state == "completed" else _STATES[state]
    if status != expected_status or state == "completed" and resources:
        raise ImageReceiptError("Inconsistent image receipt completion state")
    result = {"operation_status": state, "status": status, "resources_may_exist": resources}
    for name in ("received_at", "started_at", "finished_at"):
        if name not in value: continue
        stamp = value[name]
        if stamp is not None:
            stamp = _text(stamp, 64, name)
            try:
                if datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("missing timezone")
            except ValueError as exc:
                raise ImageReceiptError("Invalid image receipt timestamp") from exc
        result[name] = stamp
    if "message" in value:
        result["message"] = redact(_text(value["message"], 2048, "message"))
    code = value.get("error_code")
    if code is not None and (not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", code)):
        raise ImageReceiptError("Invalid image receipt error code")
    if "error_code" in value: result["error_code"] = code
    if "replayed" in value:
        if type(value["replayed"]) is not bool:
            raise ImageReceiptError("Invalid image receipt replay state")
        result["replayed"] = value["replayed"]
    public_intent = value.get("intent")
    if public_intent is not None:
        name = "image_reference" if action == "image_pull" else "image_id"
        if not isinstance(public_intent, dict) or public_intent.get(name) != intent["reference"]:
            raise ImageReceiptError("Image receipt reference differs from the persisted intent")
    raw = value.get("result")
    if raw is not None and not isinstance(raw, dict):
        raise ImageReceiptError("Invalid image receipt result")
    projected = None
    if isinstance(raw, dict):
        projected = {}
        if "status" in raw:
            if raw["status"] not in {"ready", "removed"}:
                raise ImageReceiptError("Invalid image result state")
            projected["status"] = raw["status"]
        if "image_id" in raw:
            identifier = raw["image_id"]
            if not isinstance(identifier, str) or not _IMAGE.fullmatch(identifier):
                raise ImageReceiptError("Image result has no immutable image identity")
            projected["image_id"] = identifier
        if "image_reference" in raw:
            try:
                from .image_contract import validate_image_reference
                reference = validate_image_reference(raw["image_reference"])
            except ValueError as exc:
                raise ImageReceiptError("Image result reference is invalid") from exc
            if reference != intent["reference"]:
                raise ImageReceiptError("Image result reference differs from the requested pull")
            projected["image_reference"] = reference
        if "repo_digests" in raw:
            digests = raw["repo_digests"]
            if (not isinstance(digests, list) or len(digests) > 64
                    or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,439}@sha256:[0-9a-f]{64}", item)
                           or not _IMAGE.fullmatch(item.rsplit("@", 1)[-1]) for item in digests)):
                raise ImageReceiptError("Image result digests exceed their safe contract")
            projected["repo_digests"] = list(digests)
        if "size_bytes" in raw:
            size = raw["size_bytes"]
            if size is not None and (type(size) is not int or not 0 <= size <= 2**63 - 1):
                raise ImageReceiptError("Image result size is invalid")
            projected["size_bytes"] = size
        if "message" in raw:
            projected["message"] = redact(_text(raw["message"], 2048, "result message"))
    if state == "completed":
        if not projected or projected.get("status") != expected_status or not projected.get("image_id"):
            raise ImageReceiptError("Image completion is missing its verified immutable result")
        if action == "image_remove" and projected["image_id"] != intent["reference"]:
            raise ImageReceiptError("Image removal acknowledged another image identity")
        if action == "image_pull" and projected.get("image_reference") != intent["reference"]:
            raise ImageReceiptError("Image pull did not acknowledge the requested reference")
    result["result"] = projected
    return result
