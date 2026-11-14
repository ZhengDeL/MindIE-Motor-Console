"""Reviewed plugin composition for the bundled console distribution.

Manifests identify existing implementations and their public extension points.
They do not claim that an external service is connected or that every legacy
handler has already moved into its own package.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import chain
import os
import re
from typing import Iterable

from ..version import CONSOLE_VERSION
from .contracts import PluginConfigurationError, PluginManifest, RouteDeclaration as Route
from .registry import PluginRegistry


def builtin_manifests(*, storage_backend: str = "sqlite") -> tuple[PluginManifest, ...]:
    if not isinstance(storage_backend, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,55}", storage_backend):
        raise PluginConfigurationError("Storage backend must name an installed plugin")
    definitions = (
        PluginManifest("overview", "集群概览", "汇总服务和节点状态；不将未采集或过期资源计为零。",
                       dependencies=("services", "nodes"), pages=("overview",), capabilities=("overview.read",), routes=(Route("/api/overview"),)),
        PluginManifest("nodes", "节点管理", "登记执行 Agent、查看资源、探测连接和推送受信安装包。",
                       pages=("nodes",), capabilities=("nodes.manage", "nodes.inventory", "nodes.rollout"),
                       routes=(Route("/api/nodes"), Route("/api/agent-rollouts"))),
        PluginManifest("services", "推理服务", "管理原生 Motor 配置、部署任务、版本修订及资源归属。",
                       dependencies=("nodes",), pages=("services",), capabilities=("services.manage", "services.inference"),
                       routes=(Route("/api/services"), Route("/api/jobs"), Route("/api/resources/placement-plan"))),
        PluginManifest("templates", "模板管理", "读取固定原生模板，保存和复用完整自定义配置。",
                       dependencies=("services",), pages=("templates",), capabilities=("templates.manage",),
                       routes=(Route("/api/templates"),)),
        PluginManifest("features", "功能配置", "显示固定 Motor 能力合同和离线配置指导。",
                       dependencies=("services",), pages=("features",), capabilities=("features.guides",)),
        PluginManifest("images", "节点镜像", "管理 Docker 节点镜像；保留操作回执和资源准入校验。",
                       dependencies=("nodes", "motor-docker"), pages=("images",), capabilities=("images.manage",),
                       routes=(Route("/api/images"), Route("/api/integrations/images"))),
        PluginManifest("gateways", "网关接入", "连接已有 OpenAI 兼容网关；不代替网关安装和路由管理。",
                       dependencies=("services",), pages=("gateways",), capabilities=("gateways.connect", "gateways.discovery"),
                       routes=(Route("/api/gateways"), Route("/api/services/{identifier}/aigw-models"))),
        PluginManifest("runtime-logs", "运行日志", "读取原生服务日志并保留日志来源、角色与请求关联。",
                       dependencies=("services",), pages=("logs",), capabilities=("runtime-logs.read",),
                       routes=(Route("/api/services/{identifier}/logs"),)),
        PluginManifest("system-logs", "系统日志", "查询控制台结构化运行日志；关闭页面不会停止宿主日志留存。",
                       pages=("systemlogs",), capabilities=("system-logs.read",), routes=(Route("/api/system/logs"),)),
        PluginManifest("audit", "操作审计", "查看操作审计记录；宿主始终执行授权和审计留存。",
                       pages=("audit",), capabilities=("audit.read",), routes=(Route("/api/audit"),)),
        PluginManifest("accounts", "账号管理", "管理本地账号和角色；登录、会话及权限校验属于稳定宿主。",
                       pages=("accounts",), capabilities=("accounts.manage",), routes=(Route("/api/auth/users"),)),
        PluginManifest("maintenance", "系统维护", "核对发布计划、备份及升级回执；不从网页直接升级宿主。",
                       pages=("maintenance",), capabilities=("maintenance.backup", "maintenance.upgrade-plan"),
                       routes=(Route("/api/system/releases"), Route("/api/system/upgrades"), Route("/api/system/backups"))),
        PluginManifest("branding", "平台品牌", "配置控制台名称、副标题和图标；保留默认品牌作为离线回退。",
                       kind="appearance", capabilities=("branding.configure",), routes=(Route("/api/branding"),)),
        PluginManifest("grafana", "Grafana 观测", "接入独立 Grafana 并生成离线观测配置；健康检查与业务验收分别呈现。",
                       kind="integration", capabilities=("grafana.connect", "grafana.provision"),
                       routes=(Route("/api/extensions/grafana"),)),
        PluginManifest("ccae", "CCAE 原生接入", "使用原生 Reporter 配置；外部注册、Kafka 和控制效果需要真实环境验证。",
                       kind="integration", capabilities=("ccae.native-configuration",)),
        PluginManifest("motor-docker", "Motor Docker 运行时", "通过节点 Agent 执行固定原生 Docker 启动入口，保持资源账本与操作回执。",
                       kind="runtime", capabilities=("motor-docker.execute",), runtimes=("docker",),
                       routes=tuple(Route("/agent/" + name) for name in (
                           "operations", "images", "status", "preflight", "deploy", "stop", "restart", "remove", "logs"))),
        PluginManifest("motor-kubernetes", "Motor Kubernetes 运行时", "对接客户 Kubernetes 集群并保留原生部署配置与资源所有权检查。",
                       kind="runtime", capabilities=("motor-kubernetes.execute",), runtimes=("kubernetes",),
                       routes=(Route("/agent/kubernetes"),)),
        PluginManifest("storage-sqlite", "SQLite 存储", "单实例关系存储驱动，实现事务、条件更新、原子审计及备份恢复合同。",
                       kind="storage", capabilities=("storage-sqlite.persistence",)),
        PluginManifest("storage-postgresql", "PostgreSQL 存储", "可替换的关系存储驱动；数据库凭据由部署侧提供，不暴露到浏览器。",
                       kind="storage", capabilities=("storage-postgresql.persistence",)),
    )
    return tuple(replace(item, version=CONSOLE_VERSION, required=item.id == "storage-" + storage_backend)
                 for item in definitions)


def build_registry(*, disabled: str | Iterable[str] | None = None, storage_backend: str = "sqlite",
                   extra_manifests: Iterable[PluginManifest] = ()) -> PluginRegistry:
    """Validate the complete bootstrap configuration before mounting plugins.

    Additional declarations come from reviewed host bootstrap code, never from
    an HTTP request, an arbitrary package entry point or a database document.
    A custom storage backend also needs its existing registered storage driver;
    declaring a manifest does not install a driver or validate a connection.
    """
    selected = "storage-" + storage_backend if isinstance(storage_backend, str) else ""
    manifests = (replace(item, required=item.required or item.id == selected)
                 if isinstance(item, PluginManifest) else item
                 for item in chain(builtin_manifests(storage_backend=storage_backend), extra_manifests))
    return PluginRegistry(manifests,
                          disabled=os.getenv("MOTOR_DISABLED_PLUGINS", "") if disabled is None else disabled,
                          active_storage_plugin=selected)
