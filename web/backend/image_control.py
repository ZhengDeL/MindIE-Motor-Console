"""Durable image intents; credentials are transient execution material."""
from __future__ import annotations

import hashlib
import json
import re
import threading

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .fleet import FleetError
from .image_receipts import (ImageReceiptError, image_operation_unresolved, project_image_receipt,
                             public_image_operation)
from .node_configuration import execution_node
from .observability import register_secret
from .store import now, uid


class RegistryAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=4096)


class ImageIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1, max_length=100)
    operation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,128}$")


class PullImage(ImageIntent):
    image: str = Field(min_length=1, max_length=256)
    registry_auth: RegistryAuth | None = None

    @field_validator("image")
    @classmethod
    def reference(cls, value):
        from .image_contract import validate_image_reference
        return validate_image_reference(value)


class RemoveImage(ImageIntent):
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


def create_image_router(store, fleet, lock: threading.RLock):
    router = APIRouter(prefix="/api/images", tags=["node-images"])

    def node(identifier):
        value = store.get("nodes", identifier)
        if value is None: raise HTTPException(404, "执行节点不存在")
        if "docker" not in (value.get("agent_metadata") or {}).get("supported_runtimes", []):
            raise HTTPException(409, "该节点未声明 Docker 镜像管理能力")
        return execution_node(value)

    def submit(action, data, request):
        target = node(data.node_id)
        identifier = data.operation_id or uid("image_")
        request_id = request.headers.get("Idempotency-Key")
        if request_id and request_id != identifier:
            raise HTTPException(409, "请求幂等标识与 operation_id 不一致")
        reference = data.image if action == "pull" else data.image_id
        intent = {"action": "image_" + action, "node_id": data.node_id, "reference": reference}
        digest = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
        with lock:
            with store.unit_of_work(write=True) as unit:
                previous = unit.resources.get("image_operations", identifier)
                if previous:
                    if previous.get("intent_digest") != digest:
                        raise HTTPException(409, "该操作 ID 已用于另一项镜像任务")
                    return public_image_operation(previous)
                # Cross-administrator submissions still refer to the same host
                # side effect, even when they generate different request IDs.
                offset = 0
                while True:
                    page = unit.resources.page("image_operations", limit=100, offset=offset,
                        filters={"node_id": data.node_id, "action": intent["action"], "reference": reference})
                    existing = next((item for item in page["items"] if image_operation_unresolved(item)), None)
                    if existing:
                        raise HTTPException(409, {"message": "该节点同一镜像仍有未决任务，请继续核对原操作回执",
                                                  "existing_operation_id": existing["id"]})
                    if not page["has_more"]: break
                    offset += 100
                    if offset > 10000:
                        raise HTTPException(409, "同一镜像任务历史过长，无法在有界事务内确认无未决操作；请先归档已完成历史")
                value = {"id": identifier, "operation_id": identifier, **intent, "intent_digest": digest,
                         "operation_status": "accepted", "status": "pending", "created_at": now(),
                         "updated_at": now(), "resources_may_exist": True}
                unit.resources.put("image_operations", value)
                store.audit("受理镜像任务", target["name"], "accepted", connection=unit,
                            metadata={"operation_id": identifier, "action": intent["action"]})
        payload = {"operation_id": identifier, "node_id": data.node_id}
        if action == "pull":
            payload["image"] = reference
            if data.registry_auth:
                password = data.registry_auth.password.get_secret_value()
                register_secret(password)
                payload["registry_auth"] = {"username": data.registry_auth.username, "password": password}
        else:
            payload["image_id"] = reference
        try:
            receipt = fleet.call(target, "POST", "/agent/images/" + action, payload, timeout=20)
        except FleetError as error:
            with lock:
                latest = store.get("image_operations", identifier)
                if latest is None:
                    raise HTTPException(409, "镜像任务记录已变化；请按操作标识核对 Agent 回执") from error
                # A concurrent receipt read may already have confirmed this
                # intent while the original POST was waiting for its response.
                # Transport failure is weaker evidence than that receipt.
                if latest.get("operation_status") != "accepted" or latest.get("received_at"):
                    return latest
                updated = {**latest}
                updated.update(operation_status="interrupted" if error.outcome_unknown else "failed",
                             status="unknown" if error.outcome_unknown else "failed", resources_may_exist=error.outcome_unknown,
                             message="提交结果未确认，请按原操作 ID 查询回执" if error.outcome_unknown else "Agent 未接受镜像任务，请检查权限与节点状态",
                             updated_at=now())
                if not store.compare_and_mutate_audited("image_operations", identifier, latest, updated,
                        action="镜像提交结果", target=target["name"], detail=updated["operation_status"],
                        metadata={"operation_id": identifier}):
                    raise HTTPException(409, "镜像任务状态已更新，请读取最新回执") from error
            return updated
        return apply_receipt(value, receipt, target["name"])

    def apply_receipt(current, receipt, target_name):
        try:
            changes = project_image_receipt(receipt, current)
        except ImageReceiptError as exc:
            raise HTTPException(502, "镜像任务回执未通过身份、状态或资源结果校验；保留未决任务，请按原操作标识核对") from exc
        with lock:
            latest = store.get("image_operations", current["id"])
            if latest is None: raise HTTPException(409, "镜像任务记录已变化")
            # Never let a late running response overwrite a verified terminal receipt.
            if latest["operation_status"] in {"completed", "failed"} and changes["operation_status"] in {"accepted", "running", "interrupted"}:
                return latest
            if latest["operation_status"] == "running" and changes["operation_status"] == "accepted":
                return latest
            if any(latest.get(key) != value for key, value in changes.items()):
                updated = {**latest, **changes, "updated_at": now()}
                if not store.compare_and_mutate_audited("image_operations", current["id"], latest, updated,
                    action="镜像任务回执", target=target_name, detail=str(receipt.get("operation_status", "unknown")),
                    metadata={"operation_id": current["id"]}):
                    raise HTTPException(409, "镜像任务状态已更新，请重新读取回执")
                return store.get("image_operations", current["id"])
            return latest

    @router.post("/pull", status_code=202)
    def pull(body: PullImage, request: Request): return public_image_operation(submit("pull", body, request))

    @router.post("/remove", status_code=202)
    def remove(body: RemoveImage, request: Request): return public_image_operation(submit("remove", body, request))

    @router.get("/operations")
    def operations(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=10000),
                   node_id: str | None = Query(None, min_length=1, max_length=100)):
        page = store.page("image_operations", limit=limit, offset=offset,
                          filters={"node_id": node_id} if node_id is not None else None)
        return {**page, "items": [public_image_operation(value) for value in page["items"]]}

    @router.get("/operations/{identifier}")
    def operation(identifier: str, node_id: str | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identifier): raise HTTPException(422, "无效操作 ID")
        value = store.get("image_operations", identifier)
        if value is None: raise HTTPException(404, "镜像任务不存在")
        if node_id is not None and value["node_id"] != node_id: raise HTTPException(409, "任务不属于该节点")
        target = node(value["node_id"])
        try:
            receipt = fleet.call(target, "GET", "/agent/images/operations/" + identifier, timeout=8)
        except FleetError:
            return public_image_operation({**value, "receipt_unavailable": True})
        return public_image_operation(apply_receipt(value, receipt, target["name"]))

    return router
