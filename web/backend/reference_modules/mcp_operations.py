"""Explicit MCP HTTP extension: durable, bound plans and idempotent execution.

Plans express review and preauthorization, not an assertion that an LLM supplied
human approval. Execution still uses the selected task coordinator and its audit.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.deployment_view import configuration_digest
from backend.node_configuration import execution_node
from backend.ports.services import ServiceCommandV1
from backend.provider_host.defaults import AuditedStore
from backend.reference_modules.mcp_access import expiry, require_scope, require_service_target
from backend.reference_modules.services_models import ScaleInput

CONTRACT = "ext.mcp.operations"
VERSION = "1.0.0"
PLAN_KIND = "mcp_operation_plans"
_BASE = "/api/plugins/mcp"


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    service_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    action: Literal["deploy", "rebuild", "stop", "restart", "scale", "undeploy"]
    counts: dict[str, int] | None = None

    @model_validator(mode="after")
    def validate_counts(self):
        if self.action == "scale":
            if self.counts is None:
                raise ValueError("Scale requires counts")
            ScaleInput(counts=self.counts)
        elif self.counts is not None:
            raise ValueError("Only scale accepts counts")
        return self


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_id: str = Field(pattern=r"^mcpplan_[0-9a-f]{32}$")
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_name: str = Field(min_length=1, max_length=100)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _actor(request):
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, "Authentication required")
    credential = getattr(request.state, "mcp_credential", None)
    if principal.kind == "machine" and not credential:
        raise HTTPException(403, "MCP operations require a dedicated service identity or browser session")
    return principal.user_id, credential


def _targets(workflow, service, action):
    targets = copy.deepcopy(workflow.execution_placements(service, action))
    return targets[:1] if service["runtime"] == "kubernetes" else targets


def _snapshot(service, targets, store):
    identities = {}
    for target in targets:
        identifier = target["node_id"]
        node = store.get("nodes", identifier)
        if node is None:
            raise HTTPException(409, {"code": "plan_node_missing"})
        node = execution_node(node)
        stable = {key: node.get(key) for key in ("id", "address", "agent_url", "nic_name", "configured_nic_name",
            "configuration_revision", "credential_revision", "host_identity", "daemon_id")}
        # Credentials never enter the public plan. Their encrypted binding and
        # CA also invalidate a plan if a registration was changed without a
        # revision bump. Telemetry and last_seen deliberately do not participate.
        stable["connection_binding"] = _digest({key: node.get(key) for key in ("_token", "_agent_ca_pem")})
        metadata = node.get("agent_metadata") or {}
        stable["agent_binding"] = {key: metadata.get(key) for key in ("product_id", "agent_protocol", "motor_profile", "provider_generation")}
        identities[identifier] = stable
    return {"configuration_digest": configuration_digest(service),
        "config_revision": service.get("config_revision", 1), "name": service["name"],
        "runtime": service["runtime"], "targets": targets,
        "deployed_digest": service.get("deployed_digest"),
        "deployed_motor_profile": service.get("deployed_motor_profile"), "node_bindings": identities}


def factory(ctx):
    inputs = ctx.require("api_inputs")
    if inputs is None or ctx.settings.get("mode") != "control":
        raise ValueError("MCP operations require the reference control application capabilities")
    lock = inputs.workflow_inputs.mutation_lock
    workflow = ctx.consume("services.workflow", workflow_inputs=inputs.workflow_inputs)
    coordinator = ctx.consume("tasks.coordinator", coordinator_inputs=inputs.coordinator_inputs)
    store = AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink"))
    generation = ctx.generation
    try:
        ttl = int(os.getenv("MOTOR_MCP_PLAN_TTL_SECONDS", "300"))
    except ValueError as error:
        raise ValueError("MOTOR_MCP_PLAN_TTL_SECONDS must be an integer") from error
    if not 30 <= ttl <= 3600:
        raise ValueError("MOTOR_MCP_PLAN_TTL_SECONDS must be 30..3600")
    router = APIRouter()

    def service_for(entry, identifier, *, targets=()):
        service = workflow.get(identifier)
        if service.get("deleted"):
            raise HTTPException(404, "Service no longer exists")
        if entry:
            require_service_target(entry, service, placements=targets)
        return service

    def load_plan(identifier, request):
        actor, entry = _actor(request)
        record = store.get(PLAN_KIND, identifier)
        if record is None:
            raise HTTPException(404, "Plan does not exist")
        if (record.get("binding", {}).get("actor") != actor
                or record["binding"].get("credential_id") != (entry["id"] if entry else None)):
            raise HTTPException(403, "Plan belongs to another identity")
        if not hmac.compare_digest(_digest(record["binding"]), record.get("plan_digest", "")):
            raise HTTPException(503, {"code": "plan_integrity_unavailable"})
        service_for(entry, record["binding"]["service_id"], targets=record["binding"]["snapshot"]["targets"])
        return record, entry

    def public_plan(record):
        binding = record["binding"]
        result = {key: binding[key] for key in ("service_id", "action", "expires_at", "plan")}
        result.update(plan_id=record["id"], plan_digest=record["plan_digest"],
            provider_generation=binding["provider_generation"], status=record["status"],
            configuration_digest=binding["snapshot"]["configuration_digest"],
            confirmation_name=binding["snapshot"]["name"])
        if record.get("job_id"):
            result["job_id"] = record["job_id"]
        return result

    @router.get(_BASE + "/identity")
    def identity(request: Request):
        _actor_value, entry = _actor(request)
        if not entry:
            raise HTTPException(403, "A dedicated service credential is required")
        return {key: entry[key] for key in ("id", "subject", "role", "scopes", "expires_at", "service_ids", "node_ids") if key in entry}

    @router.post(_BASE + "/operations/plan")
    def plan(data: PlanInput, request: Request):
        actor, entry = _actor(request)
        if entry:
            require_scope(entry, "deployment:plan")
        with lock:
            service = copy.deepcopy(service_for(entry, data.service_id))
            targets = _targets(workflow, service, data.action)
            if entry:
                require_service_target(entry, service, placements=targets)
            before = _snapshot(service, targets, store)
        # Native preflight may contact multiple Agents. It must not hold the
        # application mutation lock while waiting on network responses.
        if data.action in {"deploy", "rebuild"}:
            preview = workflow.plan(service, replacing=data.action == "rebuild" or bool(service.get("needs_rebuild")))
        elif data.action == "scale":
            workflow.require_scalable(service)
            preview = workflow.plan_scale(service, data.counts)
        else:
            if not service.get("resources_may_exist") or not targets:
                raise HTTPException(409, "No managed execution targets exist")
            if data.action == "undeploy" and service["runtime"] != "kubernetes":
                raise HTTPException(409, "Undeploy only supports Kubernetes managed resources")
            preview = {"valid": True, "targets": targets, "action": data.action,
                "preflight_performed": False,
                "warnings": ["This is a configuration snapshot; runtime readiness and human approval are not asserted."]}
        with lock:
            latest = service_for(entry, data.service_id)
            if _snapshot(latest, _targets(workflow, latest, data.action), store) != before:
                raise HTTPException(409, {"code": "plan_configuration_changed"})
            captured = datetime.now(timezone.utc)
            binding = {"actor": actor, "credential_id": entry["id"] if entry else None,
                "service_id": data.service_id, "action": data.action, "counts": data.counts,
                "provider_generation": generation, "created_at": captured.isoformat(),
                "expires_at": (captured + timedelta(seconds=ttl)).isoformat(),
                "snapshot": before, "plan": preview}
            record = {"id": "mcpplan_" + uuid4().hex, "binding": binding,
                "plan_digest": _digest(binding), "status": "planned", "job_id": None}
            store.mutate_audited(PLAN_KIND, record["id"], record, action="mcp.plan.created",
                target=service["name"], metadata={"plan_id": record["id"], "action": data.action,
                    "configuration_digest": before["configuration_digest"], "credential_id": binding["credential_id"]})
            return public_plan(record)

    @router.get(_BASE + "/operations/plans/{identifier}")
    def get_plan(identifier: str, request: Request):
        with lock:
            record, entry = load_plan(identifier, request)
            if entry:
                require_scope(entry, "deployment:plan")
            return public_plan(record)

    @router.post(_BASE + "/operations/apply")
    def apply(data: ApplyInput, request: Request):
        with lock:
            record, entry = load_plan(data.plan_id, request)
            if entry:
                require_scope(entry, "deployment:apply")
            binding = record["binding"]
            if not hmac.compare_digest(data.plan_digest, record["plan_digest"]):
                raise HTTPException(409, {"code": "plan_digest_mismatch"})
            if data.confirmation_name != binding["snapshot"]["name"]:
                raise HTTPException(409, {"code": "plan_confirmation_mismatch"})
            # Returning an already accepted receipt never submits new effects.
            if record.get("job_id"):
                return {"job_id": record["job_id"], "replayed": True}
            service = service_for(entry, binding["service_id"])
            # The stable coordinator key is also checked before snapshot/TTL:
            # a crash between durable job commit and plan receipt commit must
            # be recoverable even after that job changes service state.
            key = "mcp-plan:" + record["id"]
            command_hash = hashlib.sha256((binding["actor"] + ":" + key).encode()).hexdigest()
            accepted = store.get("idempotency", command_hash)
            if accepted:
                job = coordinator.get(accepted["job_id"])
                if job.get("service_id") != binding["service_id"] or job.get("action") != binding["action"]:
                    raise HTTPException(503, {"code": "plan_receipt_mismatch"})
                record.update(job_id=job["id"], status="accepted")
                store.mutate_audited(PLAN_KIND, record["id"], record, action="mcp.plan.receipt_recovered",
                    target=service["name"], metadata={"job_id": job["id"], "plan_id": record["id"]})
                return {"job_id": job["id"], "replayed": True}
            if datetime.now(timezone.utc) >= expiry(binding["expires_at"]):
                raise HTTPException(409, {"code": "plan_expired"})
            if generation != binding["provider_generation"]:
                raise HTTPException(409, {"code": "plan_provider_generation_changed"})
            targets = _targets(workflow, service, binding["action"])
            if _snapshot(service, targets, store) != binding["snapshot"]:
                raise HTTPException(409, {"code": "plan_configuration_changed"})
            if binding["plan"].get("valid") is not True:
                raise HTTPException(409, {"code": "plan_not_valid"})
            preview = binding["plan"]
            approval = {"reviewed_configuration_digest": binding["snapshot"]["configuration_digest"],
                "extra_manifests_approved_sha256": preview.get("extra_manifest_sha256") or ""}
            store.audit("mcp.plan.apply_admitted", service["name"], "accepted",
                metadata={"plan_id": record["id"], "action": binding["action"], "plan_digest": record["plan_digest"]})
            result = coordinator.submit(binding["service_id"], binding["action"], ServiceCommandV1(
                approval=approval if binding["action"] in {"deploy", "rebuild"} else None,
                counts=binding["counts"], confirmation_name=data.confirmation_name if binding["action"] == "undeploy" else None,
                idempotency_key=key))
            record.update(job_id=result["job_id"], status="accepted")
            try:
                store.mutate_audited(PLAN_KIND, record["id"], record, action="mcp.plan.accepted",
                    target=service["name"], metadata={"job_id": result["job_id"], "plan_id": record["id"]})
            except Exception as error:
                inputs.workflow_inputs.mark_unhealthy()
                raise HTTPException(503, {"code": "plan_receipt_unavailable", "outcome_unknown": True,
                    "plan_id": record["id"], "job_id": result["job_id"]}) from error
            return {"job_id": result["job_id"], "replayed": bool(result.get("replayed", False))}

    return router


def http_module():
    """Data-only declaration used by the offline assembly preparation command."""
    routes = [
        ("/identity", ["GET"], ["operator", "viewer", "admin"]),
        ("/operations/plan", ["POST"], ["operator", "admin"]),
        ("/operations/plans/{identifier}", ["GET"], ["operator", "viewer", "admin"]),
        ("/operations/apply", ["POST"], ["operator", "admin"]),
    ]
    return {"namespace": "mcp", "contract": CONTRACT, "routes": [
        {"path": _BASE + path, "methods": methods, "roles": roles, "account_only": False}
        for path, methods, roles in routes]}
