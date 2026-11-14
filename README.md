# 擎寰平台 · Motor Console

擎寰平台是面向 **MindIE-Motor** 的开源部署与运维控制台，当前正式版本为 **0.11.0**。它为原生模型配置、Docker/Kubernetes 部署、节点资源、服务日志、监控接入和平台维护提供统一的 Web 操作入口。

本项目独立维护，与原生 MindIE-Motor 分别发布和升级。平台作为独立管理组件运行。原生 Motor 继续负责请求路由、实例管理、P/D 协同及引擎执行；正常业务推理直接访问模型服务。平台不会因为更新界面或修改显示名称而更新原生源码、模型权重或正在运行的推理镜像。

## 可以做什么

| 能力 | 范围 |
| --- | --- |
| 模型配置 | 固定原生模板、自定义模板、配置修订、完整原生 JSON 导入与导出。 |
| 部署与生命周期 | Docker 多节点布局与执行、Kubernetes 原生清单适配、预检、启停、重启、任务及资源归属核对。 |
| 节点与设备 | Agent 接入、连接配置、资源明细、镜像管理；区分设备声明、硬件观测、账本和 Kubernetes 资源份额。 |
| 日志与观测 | 服务日志、平台日志、操作审计、Node/NPU Exporter、Prometheus/Grafana 接入及离线配置。 |
| 管理与维护 | 本地账号和角色、OIDC、品牌及外观设置、数据库插件、备份恢复和受信升级计划。 |
| 社区扩展 | 离线插件包、启动时装配、受保护 API、声明式页面和贡献示例；插件需经管理员审查和信任。 |

Docker 的布局建议与最终准入由平台和执行 Agent 协同完成；它不等同于 Kubernetes 的集群调度。Grafana 连接、已有推理网关接入和 CCAE 原生配置也分别有各自的安装、权限及业务验证前提。

## 版本与源码范围

| 获取方式 | 适用范围 |
| --- | --- |
| 固定标签 `v0.11.0` | 可复现的正式源码。使用对应的[发布说明](web/docs/RELEASE_0.11.0.md)和[部署手册](web/docs/DEPLOYMENT_0.11.0.md)。 |
| `main` | 当前正式主线；部署仍固定已审核提交或发行标签，记录实际镜像摘要。 |

`main` 保存已批准的正式代码。源码标签、已构建镜像和服务器实际版本是不同对象；拉取代码不会自动升级已有部署。插件使用前核对目标实例 `/api/plugin-sdk` 的实际能力。

当前固定原生适配基线为 `37d615f232a36b8dba8630ffc3a01a20d289c65e`。平台通过受控适配器使用其配置、模板和 deployer 入口；不同运行时并非每个动作都直接执行同一个 `deploy.py`。原生版本、模型镜像及外部依赖的具体兼容范围见[原生集成说明](web/docs/NATIVE_INTEGRATION.md)与[能力矩阵](web/docs/NATIVE_CAPABILITY_MATRIX.md)。

## 开始使用

**部署用户**：先阅读[文档导航](web/docs/README.md)，选择运行时、数据库和网络入口，再按[安装入口](web/DEPLOYMENT.md)准备独立目录、密钥、镜像及 Agent。

获取固定发布源码：

```bash
git clone --recurse-submodules --branch v0.11.0 \
  https://github.com/ZhengDeL/MindIE-Motor-Console.git motor-console
cd motor-console
git submodule update --init --recursive
```

GitHub 自动源码压缩包不展开原生子模块。自行构建镜像时应记录源提交、目标架构与镜像摘要；仓库中的示例镜像地址和摘要占位符须由部署方替换。

**应用开发者**：使用所需开发分支或审核后的提交，按[本地开发指南](web/README.md)运行。该指南使用已取得的源码，不要求把开发能力误装到旧标签中。

**插件作者**：阅读[插件开发指南](web/PLUGIN_DEVELOPMENT.md)，从[完整示例](web/plugins/examples/community-status/README.md)开始。当前包导入只做校验与暂存，管理员明确选择后在下次启动加载；插件不是进程沙箱，不能用于绕过宿主权限。

## 部署结构

```mermaid
flowchart LR
  Browser[浏览器] --> Web[Web 静态服务]
  Web --> API[控制面 API]
  API --> Storage[SQLite 或 PostgreSQL]
  API --> DA[Docker 执行 Agent]
  API --> KA[Kubernetes 执行 Agent]
  DA --> Docker[宿主 Docker Engine]
  KA --> KAPI[Kubernetes API Server]
  Docker --> Motor[原生 Motor 模型服务]
  KAPI --> Motor
  Client[推理客户端] --> Motor
```

推荐使用独立 Web/API 的 `web/compose.modular.yaml`。Web 不挂载数据库、节点凭据或 Docker socket；默认 SQLite 适合轻量单实例，PostgreSQL 是可选存储实现，不能据此推定控制台具备多实例高可用。

每个独立 Docker 执行宿主通常部署一个 Agent；一个 Kubernetes 集群通常配置一个执行连接，由集群调度 Pod，不为此在每个工作节点重复安装管理 Agent。具体权限和副本按管理边界配置。

## 文档与贡献

- [完整文档导航](web/docs/README.md)：安装、操作、监控、维护、插件及版本说明。
- [系统架构](web/ARCHITECTURE.md)：模块、管理链路、数据库和原生 Motor 的职责。
- [插件扩展面](web/PLUGIN_EXTENSION_POINTS.md)：已实现合同与后续可迁移模块。
- [贡献指南](CONTRIBUTING.md)、[安全报告](SECURITY.md)、[许可及第三方来源](NOTICE.md)。

缺失或过期的采样不会补成零；采样值也不代表设备已预留或模型业务已就绪。部署、外部系统、性能与长期运行需要针对目标环境分别验证。项目不把页面打开、HTTP 200 或容器 Running 当成完整业务验收。

本项目采用 [Apache License 2.0](LICENSE)。固定原生子模块及其他第三方组件保留各自许可；技术产品标识为 `motor-console`，显示品牌可由管理员配置。
