# 插件扩展与替换接口

0.12.0 提供独立的进程宿主、Python 提供者和浏览器 SDK。参考插件与第三方插件通过同一契约装配，不要求第三方冒用内置 ID 或修改调用方源码。已发布标签的能力以该标签文档为准。

## 选择合适的 SDK

| 需求 | 入口与交付格式 | 装配方式 |
| --- | --- | --- |
| 替换 HTTP 传输或 Python 适配进程 | [Node SDK](../platform/README.md)，本地受信 ESM 制品目录 | 精确契约绑定、整目录摘要、启动与停止生命周期。 |
| 替换存储、安全、业务、运行时或 API | [Python Provider SDK](backend/provider_host/README.md)，`.mcprovider` 或已审查镜像内的模块 | 数据库打开前读取独立装配文件，按契约和版本解析工厂。 |
| 替换完整界面、页面、组件、按钮或图标 | [UI SDK](frontend/sdk/README.md)，预构建 `.mcui` | 导入、审查、明确信任、选择，API 重启后发布新的浏览器启动图；不要求重建宿主。 |
| 添加独立业务 API 与简单状态页 | [社区 SDK](PLUGIN_DEVELOPMENT.md)，`.qhplugin` | 保留既有增量扩展合同，不能用此格式替代另外三个 SDK。 |

不同格式分别有自己的版本与校验器。包导入不执行代码，也不表示已启用；运行状态必须从实际进程读取。操作步骤见[提供者导入与部署](docs/PROVIDER_PLUGINS.md)。

## Python 公开合同

当前目录包含 51 个公开合同，以及一个不开放替换的 ASGI 根装配机制。实际方法、输入、可选性和依赖以[可执行目录](backend/provider_host/catalogue.py)和[独立公共 SDK](../packages/python-sdk/README.md)为准；名称相同不等于已实现完整接口。

| 领域 | 公开契约与调用边界 |
| --- | --- |
| 存储与秘密 | `storage.store`、`security.secrets`；在持久状态打开前选择，不限于 SQLite。数据库实现必须满足 Repository/UoW、条件写、审计原子性及备份恢复合同。 |
| 身份与访问控制 | `security.identity`、`security.session`、`security.policy`、`security.agent`；身份、撤销会话、角色、CSRF 与 Agent 身份分别装配。 |
| 审计、日志与任务 | `audit.sink`、`logs.*`、`services.workflow`、`tasks.coordinator`、`tasks.scheduler`、`tasks.recovery`；关键写入受强制审计与授权检查，任务记录所用代次和执行绑定。 |
| 连接与观测 | `agent.connection`、`telemetry.*`；独立的 Agent 传输、宿主指标来源、节点/服务观测和指标查询。 |
| 资源与执行 | `resources.*`、`motor.native`、`runtime.*`；库存、账本、最终准入、布局建议、原生版本画像及 Docker/K8s 执行器分别选择。 |
| 业务与集成 | `templates.catalog`、`features.catalog`、`integration.grafana`、品牌和维护合同；网关及 Reporter 配置由相应 API、能力与原生适配提供。外部产品仍有独立安装和验收前提。 |
| HTTP 与扩展 | `api.*`、`plugins.packages`、`demo.seed`；API 模块只能注册自己契约归属的路由，默认社区加载器及演示初始化也从绑定解析。 |

`/api/plugin-sdk` 的访问策略由运行模式和权限决定；其中 `provider_sdk` 描述实际合同。`/api/plugins` 的 `assembly` 描述正在运行的绑定与代次，不公开本地代码路径、凭据和完整私有配置。

`console.application` 是宿主强制检查和最终路由装配机制，不是业务插件，也不能被第三方声明或作为依赖。制品校验、路由归属、强制调用所选授权/审计、代次检查及诊断仍由宿主执行；具体实现可替换，缺失实现不得变成免认证。

## 完整 UI 替换

默认 UI 清单声明 137 个插槽，见[实际清单](frontend/public/ui-default-manifest.json)。插槽是替换粒度，不是安装包数量；一个包可提供整套界面，也可只实现一个页面或一组图标。

| 层次 | 实际替换点 |
| --- | --- |
| 应用与视觉 | `ui.shell`、`ui.appearance`、`ui.theme`、`ui.i18n`、`ui.notifications`；完整外壳、登录流程、设计系统与语言。 |
| 页面与工作流 | `page.*`、`tab.service.*`、`layout.*`；列表、详情页签、部署向导和工作区布局。 |
| 交互组件 | `component.*`；具体表格、表单、弹窗、筛选、分页与状态组件。 |
| 基础渲染与动作 | `ui.element`、`ui.button`、`ui.actions`；原生元素、按钮渲染和稳定动作 ID。 |
| 图形 | `ui.icons`；语义图标映射，无需逐页修改 import。 |

全部取消默认包的选择后，不加载默认 App、外观模块或业务样式。最小启动器只保留启动/失败诊断。运行中的页面固定一个装配代次；旧页面的写请求在后端被拒绝并要求刷新，不能混用不同代的动作与后端。

完整 UI 使用宿主 React 单例与公开 SDK。预构建包入口与资源先校验摘要，不自动从 CDN 安装依赖。它是受信浏览器代码，不是 iframe 或进程沙箱；第三方代码必须经过来源、许可和行为审查。源码示例见[独立操作界面](frontend/sdk/examples/operations-ui/README.md)。

## 生命周期、状态与恢复

- 启动前检查版本、摘要、缺失/重复提供者与循环依赖；选择错误或工厂失败直接拒绝，不悄悄加载另一默认实现。
- 选择文件独立于业务数据库。保存选择采用条件写并保留旧字节备份；当前进程不热换存储、身份、执行器或 UI。
- 更换关键提供者前，检查持久操作回执与受管资源；结果未知时先对账，不能自动重放或认领另一实现的模型。
- 提供者登记连接、线程、订阅和任务清理。插件不得依赖进程退出隐式撤销已发往 Docker/K8s 的副作用；有界停止须由实现和宿主共同满足。
- 离线检查与恢复不执行待恢复插件。损坏的选择 JSON 可按原始字节摘要恢复，并保留损坏文件；它不回滚业务数据库。

自定义存储的迁移/备份、第三方安全提供者、真实 NPU 执行和外部系统均须各自验收。公开接口可替换不等于任意实现天然兼容，也不等于自动迁移、热卸载或多实例高可用。

## 新增业务和页面

Python schema v2 支持命名空间契约、逐产品依赖与独立 HTTP 贡献；UI 支持完整页面、导航、深链接和自定义服务。无需向根应用添加业务分支。三个独立 SDK、空目录脚手架、检查和打包步骤见[插件框架与开发者入口](docs/PLUGIN_FRAMEWORK.md)。

## 贡献新实现

从[开发入口](PLUGIN_DEVELOPMENT.md)选择 SDK，在[插件提案](../.github/ISSUE_TEMPLATE/plugin-proposal.yml)中说明契约、输入/输出、生命周期、权限、数据归属和失败语义。包版本与契约版本分别管理；升级现有合同须说明兼容及迁移，不直接扩写保留 ID。

提交代码时包括清晰的 README、LICENSE 和实际调用示例。测试、浏览器验收及部署证据保留本地；贡献流程和安全报告分别见[贡献指南](../CONTRIBUTING.md)与[安全策略](../SECURITY.md)。
