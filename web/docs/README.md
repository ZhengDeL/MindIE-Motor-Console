# 文档导航

本文档集面向部署管理员、平台使用者和贡献者。请使用与源码分支或固定发布标签相匹配的文档；开发功能不能仅凭相同的软件版本字符串推定已经进入历史发行版。

## 先选择版本与使用方式

- 本文档集对应正式发行版 `v0.11.1`，使用[该版本部署说明](DEPLOYMENT_0.11.1.md)及[发布说明](RELEASE_0.11.1.md)。
- 0.11.0 正式提供社区插件导入。使用插件前，登录目标控制面并确认 `/api/plugin-sdk` 返回 `community-import-v1` 等所需能力。
- 首次部署从[安装入口](../DEPLOYMENT.md)开始；仅查看界面或开发应用使用[本地开发指南](../README.md)。

在源码获取命令中，`SOURCE_REF` 表示要取得的发行标签。部署和插件贡献可从 `v0.11.1` 开始；对已取得的审核提交工作时，先核对其文档和能力，再初始化固定子模块，不自动更新到上游最新提交。取得源码不会自动构建镜像或更新服务器。

```bash
SOURCE_REF=v0.11.1
git clone --recurse-submodules --branch "$SOURCE_REF" \
  https://github.com/ZhengDeL/MindIE-Motor-Console.git motor-console
cd motor-console
git submodule update --init --recursive
```

版本、构建修订、Agent 协议、数据库 schema、原生 Motor 与模型镜像之间的关系见[版本与兼容规则](VERSIONING.md)。

## 系统架构与原生集成

| 说明 | 主要内容 |
| --- | --- |
| [项目介绍](../../README.md) | 产品定位、能力范围、部署结构与使用入口。 |
| [系统架构](../ARCHITECTURE.md) | 部署及流量图、模块/插件分层、插件生命周期与本项目的设计原则。 |
| [原生 Motor 集成](NATIVE_INTEGRATION.md) | Docker/Kubernetes 实际执行路径、deployer 与模型镜像、管理流量和推理流量。 |
| [原生能力矩阵](NATIVE_CAPABILITY_MATRIX.md) | 配置、运行时支持和外部依赖边界。 |
| [Motor 版本升级](MOTOR_UPGRADE.md) | 固定源码、画像、兼容检查及升级前验证。 |
| [界面设计依据](UI_REFERENCE.md) | 交互、组件、主题、可访问性及第三方来源。 |

## 安装与维护

| 说明 | 主要内容 |
| --- | --- |
| [安装入口](../DEPLOYMENT.md) | 运行模式、部署选择及手册顺序。 |
| [生产部署](../PRODUCTION.md) | 镜像、目录、密钥、HTTPS、权限、启动与检查。 |
| [0.11.1 安装与升级](DEPLOYMENT_0.11.1.md) | 正式源码构建、社区插件兼容检查和组件升级。 |
| [部署目录示例](../deploy/motor-console/README.md) | 环境文件、采集 unit 和受控目录。 |
| [HTTPS 入口](../INGRESS.md) | 独立入口、TLS 文件及网络前提。 |
| [存储](../STORAGE.md) | SQLite/PostgreSQL、事务与存储驱动。 |
| [备份与恢复](../BACKUP_RECOVERY.md) | 数据库、密钥、配置、Agent 账本及插件目录的独立备份。 |

## 节点、部署与模型服务

| 说明 | 主要内容 |
| --- | --- |
| [Agent 推送](../AGENT_ROLLOUT.md) | 受控安装、主机身份、凭据及操作范围。 |
| [Agent 运维](../AGENT_OPERATIONS.md) | 连接、执行权限、更新及故障排查。 |
| [资源观测与准入](../RESOURCE_MANAGEMENT.md) | Exporter、声明、账本、硬件观测和部署前检查。 |
| [Node Exporter](DOCKER_NODE_EXPORTER.md) | Docker 主机 CPU、内存、OS、磁盘和网络采集。 |
| [NPU Exporter 与采集](DOCKER_NPU_EXPORTER.md) | 设备映射、指标、进程、健康及兼容采集方式。 |
| [模板示例](../examples/templates/README.md) | 完整原生配置、自定义模板与预检前提。 |
| [离线能力指南](OFFLINE_CAPABILITIES.md) | 在界面查看配置示例、说明和限制。 |
| [Kubernetes 访问入口](KUBERNETES_ENDPOINTS.md) | Service/NodePort、端点、所有权与访问限制。 |
| [已有网关接入](GATEWAY_CONNECTIONS.md) | OpenAI 兼容网关、模型发现和服务绑定。 |

## 观测、界面与日志

- [独立运行观测](../observability/README.md)：Prometheus、Grafana、主机及逐设备看板。
- [服务指标](SERVICE_METRICS.md)：指标来源、窗口、时效及数据为空的排查。
- [服务日志](SERVICE_LOGS.md)：来源、角色、时间、过滤与读取预算。
- [平台品牌设置](BRANDING.md)：名称、副标题、图标、权限与保存冲突。

## 插件与贡献

- [插件管理](../PLUGINS.md)：导入、信任、下次启动选择、停用及回退。
- [插件开发](../PLUGIN_DEVELOPMENT.md)：Manifest、公开 Context、API/页面、限额与完整操作流程。
- [扩展面](../PLUGIN_EXTENSION_POINTS.md)：当前公开合同与后续模块化方向。
- [宿主合同](../backend/plugin_host/README.md)、[包格式与恢复](../backend/plugin_host/packages/README.md)。
- [完整示例](../plugins/examples/community-status/README.md)、[离线 Schema](../plugins/sdk/README.md)。
- [贡献指南](../../CONTRIBUTING.md)、[安全报告](../../SECURITY.md)、[许可来源](../../NOTICE.md)。

本仓库的首个公开发行版为 0.11.0；当前源码变化见[0.11.1 发布说明](RELEASE_0.11.1.md)，旧版事实保存在[0.11.0 发布说明](RELEASE_0.11.0.md)。内部研究笔记、部署日记、账号及测试产物不属于此文档集。
