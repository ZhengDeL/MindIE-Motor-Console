"""Small public Agent metadata contract; unknown/debug/secret fields are discarded."""
from __future__ import annotations

from datetime import datetime
import re

from .version import MIN_AGENT_VERSION, PRODUCT_ID


AGENT_META_BYTES = 64 * 1024
AGENT_RESPONSE_BYTES = 16 * 1024 * 1024


class AgentMetadataError(ValueError):
    pass


def project_agent_metadata(value) -> dict:
    """Accept only this product's public Agent contract, never foreign metadata."""
    def text(item, maximum=128, pattern=None):
        if (not isinstance(item, str) or not item or len(item) > maximum
                or any(ord(c) < 32 or ord(c) == 127 for c in item)
                or pattern is not None and not re.fullmatch(pattern, item)):
            raise AgentMetadataError("Agent metadata contains an invalid public string")
        return item

    def integer(item):
        if type(item) is not int or not 1 <= item <= 65535:
            raise AgentMetadataError("Agent metadata protocol must be an integer")
        return item

    def boolean(item):
        if type(item) is not bool:
            raise AgentMetadataError("Agent metadata contains an invalid boolean")
        return item

    if not isinstance(value, dict):
        raise AgentMetadataError("Agent metadata must be a JSON object")
    if value.get("product_id") != PRODUCT_ID:
        raise AgentMetadataError("Agent belongs to another product or does not declare its product identity")
    version = value.get("agent_version")
    if (not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            or tuple(map(int, version.split("."))) < MIN_AGENT_VERSION):
        raise AgentMetadataError("Agent version is unsupported by this Qinghuan platform build")
    result = {"product_id": PRODUCT_ID, "agent_protocol": integer(value.get("agent_protocol"))}
    if "transport_security" in value:
        security = value["transport_security"]
        if not isinstance(security, dict):
            raise AgentMetadataError("Agent transport security metadata is invalid")
        tls_enabled = boolean(security.get("tls_enabled"))
        client_required = boolean(security.get("client_certificate_required"))
        if client_required and not tls_enabled:
            raise AgentMetadataError("Agent client certificate policy requires a TLS listener")
        result["transport_security"] = {"tls_enabled": tls_enabled, "client_certificate_required": client_required}
    if "read_only" in value or "execution_mode" in value:
        readonly = boolean(value.get("read_only"))
        mode = value.get("execution_mode")
        if mode != ("read_only" if readonly else "managed"):
            raise AgentMetadataError("Agent execution mode is inconsistent")
        result.update(read_only=readonly, execution_mode=mode)
    if "agent_version" in value:
        result["agent_version"] = text(value["agent_version"], 64, r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._+-]+)?")
    if "supported_control_protocols" in value:
        protocols = value["supported_control_protocols"]
        if not isinstance(protocols, list) or len(protocols) > 32:
            raise AgentMetadataError("Agent supported protocols exceed their bounds")
        result["supported_control_protocols"] = list(dict.fromkeys(integer(v) for v in protocols))
    if "supported_runtimes" in value:
        runtimes = value["supported_runtimes"]
        if (not isinstance(runtimes, list) or len(runtimes) > 8
                or any(not isinstance(v, str) or v not in {"docker", "kubernetes"} for v in runtimes)):
            raise AgentMetadataError("Agent supported runtimes are invalid")
        result["supported_runtimes"] = list(dict.fromkeys(runtimes))
    if "operation_protocol" in value:
        if type(value["operation_protocol"]) is not int or value["operation_protocol"] != 1:
            raise AgentMetadataError("Agent operation protocol is unsupported")
        result["operation_protocol"] = 1
    if "execution_capabilities" in value:
        capabilities = value["execution_capabilities"]
        if not isinstance(capabilities, dict):
            raise AgentMetadataError("Agent execution capabilities are invalid")
        result["execution_capabilities"] = {
            key: boolean(capabilities[key]) for key in
            ("operation_receipts", "resource_ledger", "resource_admission", "mutations", "image_operations") if key in capabilities
        }
        if "read_only" in result and "mutations" in result["execution_capabilities"]:
            if result["execution_capabilities"]["mutations"] == result["read_only"]:
                raise AgentMetadataError("Agent mutation capability conflicts with its execution mode")
    if "motor_profile" in value:
        profile = value["motor_profile"]
        if not isinstance(profile, dict):
            raise AgentMetadataError("Agent Motor profile is invalid")
        projected = {}
        for key, maximum, pattern in (
            ("profile_id", 96, r"[a-z0-9][a-z0-9._-]*"),
            ("commit", 40, r"[0-9a-f]{40}"),
            ("driver", 96, r"[A-Za-z0-9][A-Za-z0-9._-]*"),
            ("manifest_sha256", 64, r"[0-9a-f]{64}"),
        ):
            if key in profile:
                projected[key] = text(profile[key], maximum, pattern)
        if "agent_protocol" in profile:
            projected["agent_protocol"] = integer(profile["agent_protocol"])
        if "bundled" in profile:
            projected["bundled"] = boolean(profile["bundled"])
        result["motor_profile"] = projected
    if "native_contract" in value:
        contract = value["native_contract"]
        if (not isinstance(contract, dict) or not isinstance(contract.get("boundaries"), list)
                or len(contract["boundaries"]) > 32 or any(not isinstance(item, dict) for item in contract["boundaries"])):
            raise AgentMetadataError("Native compatibility boundaries are malformed")
        status = text(contract.get("status"), 64)
        if status not in {"known_boundaries", "separate_profile_required"}:
            raise AgentMetadataError("Native compatibility status is unsupported")
        result["native_contract"] = {
            "commit": text(contract.get("commit"), 40, r"[0-9a-f]{40}"),
            "adapter_modifies_upstream": boolean(contract.get("adapter_modifies_upstream")),
            "source_integrity": text(contract.get("source_integrity"), 64),
            "status": status, "runtime_acceptance": text(contract.get("runtime_acceptance"), 64),
            "boundaries": [{"code": text(item.get("code"), 64), "status": text(item.get("status"), 64),
                            "detail": text(item.get("detail"), 2048)} for item in contract["boundaries"]]
        }
    if "credential_policy" in value:
        policy = value["credential_policy"]
        if not isinstance(policy, dict):
            raise AgentMetadataError("Agent credential metadata is invalid")
        def entry(item):
            if not isinstance(item, dict):
                raise AgentMetadataError("Agent credential metadata entry is invalid")
            expiry = item.get("expires_at")
            if expiry is not None:
                text(expiry, 64)
                try:
                    if datetime.fromisoformat(expiry.replace("Z", "+00:00")).tzinfo is None:
                        raise ValueError
                except (ValueError, OverflowError):
                    raise AgentMetadataError("Agent credential expiry metadata is invalid") from None
            return {"id": text(item.get("id"), 64, r"[A-Za-z0-9][A-Za-z0-9_.-]*"),
                    "active": boolean(item.get("active")), "expires_at": expiry}
        projected = {"legacy": boolean(policy.get("legacy")), "disabled": boolean(policy.get("disabled")),
                     "current": entry(policy.get("current")),
                     "previous": entry(policy["previous"]) if policy.get("previous") is not None else None}
        if "schema" in policy:
            if type(policy["schema"]) is not int or policy["schema"] != 1:
                raise AgentMetadataError("Agent credential metadata schema is unsupported")
            projected["schema"] = 1
        result["credential_policy"] = projected
    return result
