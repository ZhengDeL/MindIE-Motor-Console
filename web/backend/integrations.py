"""Pinned inference-image metadata and bounded native AIGW discovery.

Registry metadata is evidence of an artifact, not model or NPU acceptance.
Motor's AIGW metadata does not install an independent gateway process.
"""
from __future__ import annotations

from typing import Any

from .compatibility import public_profile
from .store import now
from .settings import documentation_url
from .version import CONSOLE_VERSION

OFFICIAL_IMAGE = (
    "swr.cn-south-1.myhuaweicloud.com/ascendhub/mindie-motor:"
    "3.2.0-vllm_ascend0.30.0-a3-openeuler24.03-py3.12"
)
OFFICIAL_IMAGE_DIGEST = "sha256:65799d7131cb2080fbcb6301519ba5519c5a76bbbc5452d5684dfcc7197b9730"
MODEL_RESPONSE_BYTES = 2 * 1024 * 1024
MODEL_ITEMS_LIMIT = 200


def image_catalog() -> dict:
    docs_url = documentation_url()
    return {
        "version": CONSOLE_VERSION,
        "motor_profile": public_profile(),
        "documentation_url": docs_url,
        "items": [{
            "id": "motor-3.2.0-vllm-ascend-0.30.0-a3",
            "name": "MindIE Motor 3.2.0 · vLLM-Ascend 0.30.0 · A3",
            "image": OFFICIAL_IMAGE,
            "manifest_digest": OFFICIAL_IMAGE_DIGEST,
            "platforms": ["linux/arm64", "linux/amd64"],
            "hardware": "800I_A3", "motor_release": "3.2.0",
            "vllm_ascend": "0.30.0", "python": "3.12", "os": "openEuler 24.03",
            "source_url": docs_url,
            "metadata_checked_at": "2026-11-03",
            "verification": "registry_metadata_only", "compatibility": "unverified",
        }],
    }


def project_models(payload: Any) -> list[dict]:
    """Project public model-discovery fields, rejecting malformed/oversized input."""
    from .fleet import FleetError

    def valid_text(value: Any, *, nonempty: bool = False) -> bool:
        if (not isinstance(value, str) or len(value) > 256 or (nonempty and not value.strip())
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            return False
        try:
            value.encode("utf-8")
            return True
        except UnicodeEncodeError:
            return False

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise FleetError("模型发现接口没有返回有效的 data 列表")
    if len(payload["data"]) > MODEL_ITEMS_LIMIT:
        raise FleetError("模型发现结果超过 200 项，未显示不完整列表")
    result, identifiers = [], set()
    for value in payload["data"]:
        if not isinstance(value, dict):
            raise FleetError("模型发现结果包含无效条目")
        identifier = value.get("id")
        if not valid_text(identifier, nonempty=True) or identifier in identifiers:
            raise FleetError("模型发现结果的模型名无效或重复")
        identifiers.add(identifier)
        item = {"id": identifier}
        for key in ("object", "owned_by"):
            if key not in value:
                continue
            text = value[key]
            if text is not None and not valid_text(text):
                raise FleetError("模型发现结果包含无效文本字段")
            item[key] = text
        for key in ("p_max_seqlen", "d_max_seqlen", "p_instances_num", "d_instances_num", "created"):
            if key not in value:
                continue
            number = value[key]
            minimum = 1 if key.endswith("max_seqlen") else 0
            if number is not None and (type(number) is not int or not minimum <= number <= 2**53 - 1):
                raise FleetError("模型发现结果包含无效整数或超出浏览器精度")
            item[key] = number
        for key in ("slo_ttft", "slo_tpot"):
            if key not in value:
                continue
            number = value[key]
            if number is not None and (type(number) not in {int, float} or not 0 < number <= 2**53 - 1):
                raise FleetError("模型发现结果包含无效 SLO")
            item[key] = number
        result.append(item)
    return result


def discover_models(service: dict, nodes: dict) -> dict:
    from .motor_http import request_motor
    payload = request_motor(service, nodes, "infer", "/v1/models", timeout=10,
                            max_response_bytes=MODEL_RESPONSE_BYTES)
    return {"status": "available", "source": "motor_coordinator",
            "items": project_models(payload), "collected_at": now()}
