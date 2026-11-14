# MindIE-Motor Console 部署入口

新安装和组件更新从[安装指南](docs/INSTALLATION.md)及[生产运行手册](PRODUCTION.md)开始。安装前核对源码、镜像摘要、SDK 能力和签名升级清单。

插件通过公开 SDK 和精确装配配置接入；管理员导入、审查并选择后，在下次启动加载。Python、Node 与完整 UI 的格式和执行边界分别管理，详见[插件管理](PLUGINS.md)和[提供者部署](docs/PROVIDER_PLUGINS.md)。

```text
浏览器 → HTTPS Web → Node 插件入口 → Python worker（单实例）
                       ├→ 数据库插件：SQLite / PostgreSQL
                       ├→ Docker Agent → 原生 Motor 模型容器
                       └→ Kubernetes 执行 Agent → 原生集群资源

Node / NPU Exporter → Prometheus → Grafana
```

Web、API、数据库、Agent 和观测模块分别维护。每台独立 Docker 执行宿主通常一个 Agent；每个 Kubernetes 集群通常一个执行 Agent，工作节点调度由 Kubernetes 完成。Agent 不承载模型推理流量。

三组件插件入口使用 `compose.plugins.yaml`，已有独立 Web/API 使用 `compose.modular.yaml`；后者仍支持 Python 提供者与 UI 制品。`compose.production.yaml` 是 bundled 兼容拓扑，不能把多个拓扑同时指向同一个可写数据目录。先按目标架构构建和验证镜像，再配置不可变摘要、独立持久目录与 HTTPS。升级需要一致备份、当前运行拓扑、真实镜像和可回滚计划；GitHub 源码版本不会自动更新已部署服务器。

- [安装与升级](docs/INSTALLATION.md)
- [独立观测模块](observability/README.md)
- [Agent 推送](AGENT_ROLLOUT.md)与[运维](AGENT_OPERATIONS.md)
- [数据库与扩展边界](STORAGE.md)
- [资源观测、时效与部署准入](RESOURCE_MANAGEMENT.md)
- [数据备份和恢复](BACKUP_RECOVERY.md)
