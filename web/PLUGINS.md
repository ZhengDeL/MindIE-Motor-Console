# 插件管理与运行边界

MindIE-Motor Console 通过内置插件组合业务能力，通过社区源码包扩展受保护 API、页面和后台服务。本文说明部署管理员如何选择插件及判断生效状态；插件作者使用[开发指南](PLUGIN_DEVELOPMENT.md)。

使用社区包前，在已登录会话读取 `/api/plugin-sdk`，确认目标安装报告 `community-import-v1`、`http-routes-v1` 和所需页面能力。平台版本、插件 API 与具体能力分别检查；发布版本与源码获取见[文档入口](docs/README.md)。

MindIE-Motor 负责原生推理与部署，平台负责管理连接和操作编排。插件不替换 Coordinator、Controller、NodeManager 或引擎算法，也不自动更换固定的原生源码基线。

## 当前实现

| 层 | 实现与实际边界 |
| --- | --- |
| 宿主 | 登录、会话、权限、审计写入、资源所有权、资源准入、存储事务及恢复入口始终保留。 |
| 插件 SDK | 不可变 Manifest、API 版本、依赖图、页面/API 归属、受信工厂、版本化接口提供/消费、退出清理。 |
| 实际工厂 | 品牌、节点与 Agent 推送、模板、镜像、网关，以及服务的布局、入口和操作恢复路由已通过工厂装配。 |
| 核心协调 | 服务主流程、部分系统及账号路由由宿主协调；注册表控制启用状态。注册条目不代表每个模块均可独立发行。 |
| 前端 | 使用服务端插件清单选择菜单、命令面板、页面、轮询与嵌套入口。系统设置保留为恢复入口。 |
| 数据库 | 沿用事务 Repository / UoW 驱动；SQLite、PostgreSQL 独立选择。显示可用驱动不代表已连接两个数据库。 |
| 运行时 | 控制面在连接 Agent 前检查运行时插件；Agent 独立检查自身配置。Docker 停用时不创建 Docker 执行组件，宿主 Exporter 采集入口仍可用。 |

关闭插件不删除模型、数据库、工作目录或已部署资源。关闭运行时插件会暂停该控制面执行相应管理操作；重新启用后才能继续管理。关闭操作审计页面不会关闭审计写入，关闭账号管理页面不会关闭身份校验。CCAE 插件表示控制台配置入口，不能撤销已经写入原生模型配置的 Reporter 行为。

## 查看状态

在“系统设置 → 插件管理”查看已装配能力及社区包。几个状态含义不同：

| 状态 | 含义与下一步 |
| --- | --- |
| 已导入 | 制品已通过包格式检查并暂存，还没有被选为启动代码。 |
| 下次启动已选择 | 精确摘要和配置已保存；重新启动对应控制面后才生效。 |
| 当前已加载 | 当前进程已装配该版本；其API/外部服务仍需单独检查。 |
| 宿主停用或版本不兼容 | 仅重新启动不足以启用；先核对启动配置、版本和依赖。 |

选择变化后 `pending_restart` 表示当前进程与下次启动目标不一致。取消选择后，当前插件仍可保持加载，直到控制面重启；不据此判断模型已停止。

## 部署配置

直接运行 Python / Uvicorn 使用 `MOTOR_DISABLED_PLUGINS`，值为逗号分隔的插件 ID。Compose 将不同进程的选择分开：

| Compose 外部变量 | 注入的组件 |
| --- | --- |
| `MOTOR_CONTROL_DISABLED_PLUGINS` | 控制面 |
| `MOTOR_AGENT_DISABLED_PLUGINS` | 当前 Agent |
| `MOTOR_DEMO_DISABLED_PLUGINS` | 演示实例 |

空值启用可装配的内置插件；社区包还必须先导入并明确选择。示例：

```dotenv
MOTOR_CONTROL_DISABLED_PLUGINS=grafana,branding
MOTOR_AGENT_DISABLED_PLUGINS=motor-kubernetes
```

修改部署配置后只重建对应平台组件。以下命令从 `web/` 目录执行，沿用已有部署的 Compose 文件、项目名与环境文件；文件名根据部署方式替换：

```bash
docker compose --env-file .env -f compose.production.yaml up -d --no-deps control
```

上例应用控制面启动配置，不重建模型容器。首次安装先完成[生产部署说明](PRODUCTION.md)中的目录、账号、密钥与网络准备。Kubernetes-only Agent 若关闭 Docker，也需关闭依赖它的镜像管理声明：

```dotenv
MOTOR_AGENT_DISABLED_PLUGINS=motor-docker,images
```

未知插件、重复 ID、缺失或循环依赖、启用插件依赖已停用插件、关闭当前数据库驱动都会拒绝启动；不会静默切换数据库或自动级联关闭其他模块。Web 的“系统设置 → 插件管理”展示实际启动清单、依赖、版本及当前存储驱动，不提供未实现的热切换。

插件前端要求 `/api/plugins` 返回 `schemaVersion: 1`。与不提供该接口的旧后端混用时，页面显示清单错误并保留设置和重试入口。API 与 Web 应按同一兼容组合交付；清单摘要只描述装配内容，不代表插件签名、外部连接健康或模型可用性。

## 扩展合同

宿主按完整依赖图装配选中的工厂。工厂通过 Context 获取允许的服务，以 `provide(name, version, value)` 发布接口，依赖插件使用 `consume()` 消费精确版本。工厂不得覆盖其他插件路由或宿主身份端点。后台线程、客户端及任务组件使用 `on_shutdown()` 注册清理；所有清单在启动后封存。

同一 `ExitStack` 负责装配失败与正常退出的资源释放；某个清理失败时仍尝试其余清理。新插件不应在模块导入阶段隐式启动工作线程。接口与示例见 [插件 SDK](backend/plugin_host/README.md)。

插件是受信代码，并非进程沙箱。真实控制面管理员可导入离线 `.qhplugin` 源码包；导入只校验与暂存，显式信任并选择后由下次启动加载。选择旧摘要并重启可回退插件代码，不自动回滚插件自己的数据迁移。演示环境和执行 Agent 不加载上传的控制面代码。当前没有在线插件市场、任意包自动执行、前端远程脚本加载或运行中热卸载；wheel/复杂前端包的发行仍需独立合同。

导入格式、公开 SDK、完整示例、配置及恢复流程见[社区开发指南](PLUGIN_DEVELOPMENT.md)，可贡献能力及需要独立接口设计的模块见[插件扩展面](PLUGIN_EXTENSION_POINTS.md)。

## 原生 Motor 部署合同

Docker 运行时调用固定原生启动入口，保留资源账本、最终准入、操作 ID 和 Agent 回执。当前 Kubernetes 运行时使用 UID/resourceVersion 所有权保护的执行路径；它与原生云原生 Job 是不同执行实现，不能混用操作回执或假定已经自动接管。

固定源码包含 `examples/cloud_native_deploy` 的部署、扩缩与清理 Job。新增 Job 提供者必须实现不可变输入、操作摘要、Job/Namespace UID、终态回执和结果未知时对账。原生 cleanup 对部分资源使用 `--all`，只能在核验为平台独占的命名空间使用；共享命名空间不能调用该清理路径。Job 完成、模型就绪和实际推理分别验证。

## 使用检查

安装后分别检查插件清单、选择修订号、当前已加载状态与实际 API。新增页面检查角色权限、数据来源、刷新/失败、明暗主题及窄屏；运行时扩展还需要真实 Agent、目标集群和模型验证。包校验、浏览器显示或容器 Running 均不能代替完整业务检查。

源码来源和第三方许可见 [NOTICE](../NOTICE.md)，贡献与发布检查见 [CONTRIBUTING](../CONTRIBUTING.md)。
