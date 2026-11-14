# 擎寰平台部署入口

正式部署基线为 **v0.11.0**。新安装和已有安装升级从 [0.11.0 部署与升级](docs/DEPLOYMENT_0.11.0.md)及[生产运行手册](PRODUCTION.md)开始。源码、镜像摘要、SDK 能力和签名升级清单必须匹配；不能只修改页面版本号。

0.11.0 首次将社区插件包导入纳入正式发行。部署后在已登录会话通过 `/api/plugin-sdk` 的 `features` 确认 `community-import-v1`、`http-routes-v1` 和 `status-table-v1`。上传只做校验与暂存；管理员明确信任并选择后，下次启动才加载。插件与宿主同进程执行，不是安全沙箱，详见[插件宿主说明](backend/plugin_host/README.md)。

```text
浏览器 → HTTPS Web → Control API（单实例）
                       ├→ 数据库插件：SQLite / PostgreSQL
                       ├→ Docker Agent → 原生 Motor 模型容器
                       └→ Kubernetes 执行 Agent → 原生集群资源

Node / NPU Exporter → Prometheus → Grafana
```

Web、API、数据库、Agent 和观测模块分别维护。每台独立 Docker 执行宿主通常一个 Agent；每个 Kubernetes 集群通常一个执行 Agent，工作节点调度由 Kubernetes 完成。Agent 不承载模型推理流量。

推荐 `compose.modular.yaml` 分离 Web 与 API，SQLite 或 PostgreSQL 由数据库插件提供；`compose.production.yaml` 是 bundled 兼容拓扑，两者不要混用。先按目标架构构建和验证 API/Web 镜像，再配置不可变摘要、独立持久目录与 HTTPS。升级需要一致备份、当前运行拓扑、真实镜像和可回滚计划；GitHub 源码版本不会自动更新已部署服务器。

- [0.11.0 部署范围与升级](docs/DEPLOYMENT_0.11.0.md)
- [独立观测模块](observability/README.md)
- [Agent 推送](AGENT_ROLLOUT.md)与[运维](AGENT_OPERATIONS.md)
- [数据库与扩展边界](STORAGE.md)
- [资源观测、时效与部署准入](RESOURCE_MANAGEMENT.md)
- [数据备份和恢复](BACKUP_RECOVERY.md)
