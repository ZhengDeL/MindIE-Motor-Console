"""Node registration and verified credential changes, independent of deployments."""
from __future__ import annotations

import ipaddress
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .fleet import FleetError, public_node, validate_agent_url
from .observability import register_secret
from .store import now, uid
from .version import AGENT_PROTOCOL_VERSION
from .agent_metadata_projection import AGENT_META_BYTES, AgentMetadataError, project_agent_metadata
from .agent_trust import MAX_AGENT_CA_BYTES, agent_ca_summary, validate_agent_ca
from .resource_inventory import InventoryError, MAX_INVENTORY_BYTES, inventory_view, project_inventory
from .node_inventory_sampling import NodeInventorySampler, _duplicate_identity, ObservationBusy
from .node_configuration import edit_policy, execution_identity, invalidate_observation, nic_configuration, node_references


class NodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    address: str
    agent_url: str
    agent_token: str = Field(default="", max_length=2000)
    agent_ca_pem: str = Field(default="", max_length=MAX_AGENT_CA_BYTES)
    labels: list[str] = Field(default_factory=list, max_length=32)
    nic_name: str = Field(default="", max_length=15)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("节点名称不能为空或含控制字符")
        return value.strip()

    @field_validator("address")
    @classmethod
    def address_is_ipv4(cls, value: str):
        address = ipaddress.IPv4Address(value)
        if address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError("请选择节点实际 IPv4 地址")
        return str(address)

    @field_validator("nic_name")
    @classmethod
    def valid_nic(cls, value: str):
        if value and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", value):
            raise ValueError("通信网卡须为最多 15 位的 Linux 网卡名称；留空使用 Agent 上报值")
        return value

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, value: list[str]):
        if any(not item.strip() or len(item) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in item) for item in value):
            raise ValueError("标签须为非空文本，每项最多 64 字符")
        return list(dict.fromkeys(item.strip() for item in value))

    @field_validator("agent_url")
    @classmethod
    def url_is_agent(cls, value: str):
        return validate_agent_url(value)

    @field_validator("agent_token")
    @classmethod
    def token_is_literal(cls, value: str):
        if value and (value.strip() != value or any(not 33 <= ord(c) <= 126 for c in value)):
            raise ValueError("Agent Token 必须是无空白的可打印 ASCII 字符")
        return value

    @field_validator("agent_ca_pem")
    @classmethod
    def ca_is_public(cls, value: str):
        return validate_agent_ca(value) if value else ""


class NodeMetadataInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = None
    nic_name: str | None = Field(default=None, max_length=15)
    labels: list[str] | None = Field(default=None, max_length=32)

    @field_validator("name", "address", "nic_name", "labels")
    @classmethod
    def configured_values(cls, value, info):
        if value is None:
            raise ValueError("配置字段不能为 null；不修改时请省略")
        validator = {"name": NodeInput.clean_name, "address": NodeInput.address_is_ipv4,
                     "nic_name": NodeInput.valid_nic, "labels": NodeInput.valid_labels}[info.field_name]
        return validator(value)


class NodeCredentialsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_token: str = Field(min_length=24, max_length=2000)
    expected_revision: int = Field(ge=0)
    agent_url: str | None = Field(default=None, max_length=2048)
    agent_ca_pem: str | None = Field(default=None, max_length=MAX_AGENT_CA_BYTES)

    @field_validator("agent_ca_pem")
    @classmethod
    def public_ca(cls, value: str | None):
        return validate_agent_ca(value) if value else value

    @field_validator("agent_url")
    @classmethod
    def secure_agent_url(cls, value: str | None):
        return validate_agent_url(value) if value is not None else None

    @field_validator("agent_token")
    @classmethod
    def literal_token(cls, value: str):
        if value.strip() != value or any(not 33 <= ord(c) <= 126 for c in value):
            raise ValueError("Agent Token 必须是无空白的可打印 ASCII 字符")
        return value


def node_view(node: dict) -> dict:
    configured, reported, effective = nic_configuration(node)
    result = {**inventory_view(public_node({**node, "nic_name": effective})),
              "configuration_revision": node.get("configuration_revision", 0),
              "configured_nic_name": configured, "reported_nic_name": reported,
              "credential_configured": bool(node.get("_token")), "credential_revision": node.get("credential_revision", 0),
              "tls_trust": agent_ca_summary(node.get("_agent_ca_pem"))}
    if "agent_metadata" in result:
        try:
            result["agent_metadata"] = project_agent_metadata(result["agent_metadata"])
        except AgentMetadataError:
            # Old saved metadata is not trusted merely because it was persisted.
            result.pop("agent_metadata")
    return result


def create_node_router(store, fleet, mutation_lock, *, mode: str, inventory_sampler=None) -> APIRouter:
    router = APIRouter(prefix="/api/nodes", tags=["nodes"])
    sampler = inventory_sampler if inventory_sampler is not None else NodeInventorySampler(store, fleet, mutation_lock, mode=mode)

    def find(identifier):
        node = store.get("nodes", identifier)
        if node is None:
            raise HTTPException(404, "节点不存在")
        return node

    def detail(node):
        duplicates = _duplicate_identity(node, store.list("nodes"))
        if duplicates:
            node = {**node, "identity_conflict": {"code": "duplicate_execution_host", "other_node_ids": duplicates,
                     "message": "同一宿主或 Docker daemon 已重复接入；资源未重复计入"}}
        elif node.get("identity_conflict"):
            node = {key: value for key, value in node.items() if key != "identity_conflict"}
        references = node_references(store, node)
        return {**node_view(node), "references": references, "edit_policy": edit_policy(references)}

    @router.get("/{identifier}")
    def get_node(identifier: str):
        return detail(find(identifier))

    @router.patch("/{identifier}")
    def update_node(identifier: str, data: NodeMetadataInput):
        fields = data.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if not fields:
            raise HTTPException(422, "请至少提供一个要修改的登记字段")
        with mutation_lock:
            latest = find(identifier)
            if latest.get("configuration_revision", 0) != data.expected_revision:
                raise HTTPException(409, "节点配置已变化，请刷新详情后确认并重新保存")
            configured, reported, _ = nic_configuration(latest)
            changed = {key: value for key, value in fields.items()
                       if value != (configured if key == "nic_name" else latest.get(key))}
            if not changed:
                return detail(latest)
            references = node_references(store, latest)
            network_changed = bool({"address", "nic_name"} & changed.keys())
            if network_changed and references["network_protected"]:
                raise HTTPException(409, edit_policy(references)["address"]["reason"])
            if "address" in changed and any(node.get("id") != identifier and node.get("address") == changed["address"] for node in store.list("nodes")):
                raise HTTPException(409, "该部署 IP 已由其他节点登记，请保留独立节点身份")
            value = {**latest, **{key: item for key, item in changed.items() if key != "nic_name"},
                     "configuration_revision": data.expected_revision + 1, "configuration_updated_at": now(),
                     "configured_nic_name": changed.get("nic_name", configured), "reported_nic_name": reported}
            value["nic_name"] = value["configured_nic_name"] or reported
            if network_changed:
                value = invalidate_observation(value, "部署 IP 或通信网卡登记已更新，等待重新采集")
            if not store.compare_and_mutate_audited("nodes", identifier, latest, value,
                    action="更新节点配置", target=value["name"], detail="仅更新平台登记信息，未重启 Agent 或模型服务",
                    metadata={"configuration_revision": value["configuration_revision"], "fields": sorted(changed)}):
                raise HTTPException(409, "节点记录已变化，请刷新后重试")
        return detail(value)

    @router.get("")
    def get_nodes():
        nodes = store.list("nodes")
        result = []
        for node in nodes:
            duplicates = _duplicate_identity(node, nodes)
            if duplicates:
                node = {**node, "identity_conflict": {"code": "duplicate_execution_host", "other_node_ids": duplicates,
                         "message": "同一宿主或 Docker daemon 已重复接入；资源未重复计入"}}
            elif node.get("identity_conflict"):
                node = {key: value for key, value in node.items() if key != "identity_conflict"}
            result.append(node_view(node))
        return {"items": result}

    @router.get("/{identifier}/kubernetes/nodes")
    def get_cluster_nodes(identifier: str, request: Request):
        node = find(identifier)
        if request.query_params:
            raise HTTPException(422, "集群节点查询不接受 URL、kubecontext 或其它查询参数")
        if mode == "demo":
            raise HTTPException(409, "演示环境不连接真实 Kubernetes 集群，请在控制面接入执行 Agent 后读取")
        result = fleet.cluster_nodes(node)
        return {key: result[key] for key in ("items", "collected_at", "cluster_version")}

    @router.post("", status_code=201)
    def create_node(data: NodeInput):
        with mutation_lock:
            if any(n["address"] == data.address or n["agent_url"] == data.agent_url for n in store.list("nodes")):
                raise HTTPException(409, "节点地址或 Agent 已接入")
            if mode != "demo" and len(data.agent_token) < 24:
                raise HTTPException(422, "Agent Token 至少 24 字符")
            register_secret(data.agent_token)
            if data.agent_ca_pem and not data.agent_url.startswith("https://"):
                raise HTTPException(422, "节点专用 CA 仅用于 HTTPS Agent")
            value = {**data.model_dump(exclude={"agent_token", "agent_ca_pem"}), "id": uid("node_"),
                     "status": "unknown", "architecture": "", "os": "", "docker_version": "",
                     "npu_total": None, "npu_used": None, "cpu_percent": None, "memory_percent": None,
                     "last_seen": "", "error": "", "credential_revision": 0,
                     "configuration_revision": 0, "configured_nic_name": data.nic_name, "reported_nic_name": "",
                     "_token": fleet.secrets.encrypt(data.agent_token), "_agent_ca_pem": data.agent_ca_pem}
            store.mutate_audited("nodes", value["id"], value, action="接入节点", target=value["name"])
        return node_view(value)

    @router.patch("/{identifier}/credentials")
    def update_credentials(identifier: str, data: NodeCredentialsInput):
        original = find(identifier)
        if original.get("credential_revision", 0) != data.expected_revision:
            raise HTTPException(409, "凭据版本已变化，请刷新节点后重试")
        destination = data.agent_url if data.agent_url is not None else original["agent_url"]
        node_ca = original.get("_agent_ca_pem", "") if data.agent_ca_pem is None else data.agent_ca_pem
        if node_ca and not destination.startswith("https://"):
            raise HTTPException(422, "节点专用 CA 仅用于 HTTPS Agent")
        if any(n["id"] != identifier and n.get("agent_url") == destination for n in store.list("nodes")):
            raise HTTPException(409, "目标 Agent 地址已由其他节点接入")
        register_secret(data.agent_token)
        encrypted = fleet.secrets.encrypt(data.agent_token)
        metadata: dict[str, Any] | None = None
        verified_identity = None
        destination_changed = destination != original["agent_url"]
        if destination_changed:
            references = node_references(store, original)
            if references["endpoint_protected"]:
                raise HTTPException(409, edit_policy(references)["agent_url"]["reason"])
        candidate = {**original, "agent_url": destination, "_token": encrypted, "_agent_ca_pem": node_ca}
        if mode != "demo":
            # A fixed authenticated GET verifies the new secret before replacing
            # the saved connection. Node identity and placements stay fixed;
            # an explicitly supplied endpoint can upgrade HTTP to HTTPS.
            try:
                metadata = project_agent_metadata(fleet.call(
                    candidate, "GET", "/agent/meta",
                    timeout=15, max_response_bytes=AGENT_META_BYTES))
            except (FleetError, AgentMetadataError):
                raise HTTPException(409, "新管理连接未通过 TLS、凭据或 Agent 元数据校验，原连接保持不变") from None
            if metadata["agent_protocol"] != AGENT_PROTOCOL_VERSION:
                raise HTTPException(409, "新凭据未通过 Agent 协议校验，原凭据保持不变")
            if destination_changed:
                baseline = execution_identity(original)
                docker_expected = original.get("docker_available") is True or bool(original.get("docker_version") or baseline.get("daemon_id"))
                try:
                    # Most nodes already have a stable, strictly projected host
                    # identity. Legacy registrations may establish it from the
                    # existing verified connection before checking a new URL.
                    if not baseline.get("host_identity") or docker_expected and not baseline.get("daemon_id"):
                        old_info = project_inventory(fleet.call(original, "GET", "/agent/inventory", timeout=10,
                                                                max_response_bytes=MAX_INVENTORY_BYTES))
                        baseline = execution_identity(old_info)
                        docker_expected = docker_expected or old_info.get("docker_available") is True or bool(old_info.get("docker_version"))
                    new_info = project_inventory(fleet.call(candidate, "GET", "/agent/inventory", timeout=10,
                                                            max_response_bytes=MAX_INVENTORY_BYTES))
                    if (new_info.get("inventory_schema") != 2 or not baseline.get("host_identity")
                            or new_info.get("host_identity") != baseline["host_identity"]
                            or inventory_view({**new_info, "status": "online"}).get("resource_freshness") != "fresh"
                            or original.get("kubernetes_available") is True and new_info.get("kubernetes_available") is not True
                            or docker_expected and (not baseline.get("daemon_id")
                                or new_info.get("daemon_id") != baseline["daemon_id"]
                                or new_info.get("docker_available") is False)):
                        raise InventoryError("Execution host identity differs")
                    verified_identity = baseline
                except (FleetError, InventoryError):
                    raise HTTPException(409, "新 Agent URL 无法确认为原执行宿主（Docker 还须同一 daemon）；未改变服务运维指向。请核对地址、宿主身份及原连接状态") from None
        with mutation_lock:
            latest = find(identifier)
            if (latest.get("credential_revision", 0) != data.expected_revision
                    or latest.get("_token") != original.get("_token")
                    or latest.get("_agent_ca_pem", "") != original.get("_agent_ca_pem", "")
                    or latest.get("agent_url") != original.get("agent_url")):
                raise HTTPException(409, "节点凭据或地址已变化，请重新校验")
            if any(n["id"] != identifier and n.get("agent_url") == destination for n in store.list("nodes")):
                raise HTTPException(409, "目标 Agent 地址已由其他节点接入")
            if destination_changed:
                references = node_references(store, latest)
                if references["endpoint_protected"]:
                    raise HTTPException(409, edit_policy(references)["agent_url"]["reason"])
            if verified_identity:
                latest_identity = execution_identity(latest)
                if any(latest_identity.get(field) and latest_identity[field] != verified_identity.get(field)
                       for field in ("host_identity", "daemon_id")):
                    raise HTTPException(409, "执行宿主观测已变化，请重新校验管理入口")
            value = {**latest, "_token": encrypted, "agent_url": destination, "_agent_ca_pem": node_ca,
                     "credential_revision": data.expected_revision + 1, "credentials_updated_at": now()}
            value = invalidate_observation(value, "管理连接凭据已更新，等待重新采集")
            if metadata is not None:
                value["agent_metadata"] = metadata
                value.pop("compatibility_error", None)
            if not store.compare_and_mutate_audited("nodes", identifier, latest, value,
                    action="更新节点凭据", target=value["name"],
                    detail="新凭据经 Agent 校验；保留节点 ID 和服务引用" if metadata else "演示环境凭据更新；未访问 Agent",
                    metadata={"credential_revision": value["credential_revision"], "agent_verified": metadata is not None,
                              "endpoint_changed": destination_changed, "execution_identity_verified": verified_identity is not None}):
                raise HTTPException(409, "节点记录已变化，请刷新后重试")
        return {**detail(value), "credential_verified": metadata is not None}

    @router.post("/{identifier}/probe")
    def probe_node(identifier: str):
        find(identifier)
        if mode == "demo":
            with mutation_lock:
                value = {**find(identifier), "status": "online", "last_seen": now(), "error": ""}
                store.mutate_audited("nodes", identifier, value, action="探测节点", target=value["name"],
                                     detail="演示环境数据；未访问真实 Agent")
        else:
            try:
                value = sampler.collect(identifier, manual=True)
            except KeyError:
                raise HTTPException(404, "节点不存在") from None
            except ObservationBusy as error:
                raise HTTPException(429, str(error), headers={"Retry-After": "2"}) from None
            except InventoryError as error:
                raise HTTPException(409, str(error)) from None
        return node_view(value)

    @router.delete("/{identifier}")
    def delete_node(identifier: str):
        with mutation_lock:
            node = find(identifier)
            references = node_references(store, node)
            if (references["service_count"] or references["job_count"] or references["resource_claim_count"]
                    or references["resource_ledger_incomplete"]):
                raise HTTPException(409, "该节点仍被服务、执行任务或未释放资源引用，请先完成清理与对账")
            store.mutate_audited("nodes", identifier, None, action="移除节点", target=node["name"],
                                 detail="仅移除接入记录，不卸载节点或删除模型")
        return {"ok": True}

    return router
