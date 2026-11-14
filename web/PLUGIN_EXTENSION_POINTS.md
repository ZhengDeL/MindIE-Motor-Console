# 插件扩展能力与接口演进

社区包通过公开的 Context 合同贡献功能，不需要修改宿主私有方法。原生 Motor 的 Coordinator、Controller、NodeManager 与引擎仍保持自己的生命周期；平台插件不接管推理流量或改写原生调度算法。

## 现有社区接口

目标安装通过 `/api/plugin-sdk` 报告所需能力；具体包的宿主与依赖兼容范围也必须满足，不从新宿主版本推定所有旧包均可加载。

| 扩展面 | 实际入口 | 已实现范围 |
| --- | --- | --- |
| 独立业务 API | Manifest `routes` + `context.mount_router()` | 自己的 `/api/plugins/<id>` 命名空间，HTTP 路由，宿主认证、最低角色和写操作 CSRF。 |
| 新页面与导航 | Manifest `navigation` | 纯文本 `status-table-v1`，侧栏、快捷搜索、直达地址、角色过滤、分页、刷新与失败反馈。 |
| 插件之间的服务 | `provide()` / `consume()` | 精确接口版本、直接声明依赖、装配前依赖/版本校验、禁止覆盖其他提供者。 |
| 后台任务与连接 | 同步工厂 + `on_shutdown()` | 明确登记连接/线程清理，装配失败与退出均释放；无 UI 的包可声明空 `routes`。任务有界停止由作者负责。 |
| 包内静态数据 | `require_service("resources")` | 只读包内已校验资源；可以带配置示例、指南或指标定义，不执行安装钩子。 |
| 私有插件配置 | `require_service("configuration")` | 下次启动选择时提供的 JSON 副本；目录只显示配置键和摘要，配置更新需重启。它不是自动加密的密钥保险库。 |
| 公共平台元信息 | `require_service("public_info")` | 产品名、版本、运行模式、宿主 API；不提供原生 SSH、Docker socket、数据库对象或任意文件读取。 |

包是经过操作员信任的 Python 源码，不是沙箱。网络和文件副作用必须在 README 中说明。GET 接口应保持只读语义，不能借角色声明隐式执行写操作；数据访问与副作用仍由维护者审查。没有选择的包、免登录演示及执行 Agent 都不会加载上传的控制面代码。

## 需要独立接口设计的模块

下表是迁移路线，不表示导入任意社区包后已经可以替换这些内部提供者。

| 候选 | 当前实现位置 | 适合的插件合同 | 必须留在宿主的约束 |
| --- | --- | --- | --- |
| Motor 版本适配 | `compatibility.py`、`motor_adapter.py` | 版本画像、原生入口、配置/清单呈现、能力探测 | 固定源码身份、未知 JSON 字段、明确拒绝不支持操作、真实版本验收。 |
| Docker / 原生 K8s Job 执行器 | `docker_runtime.py`、`kubernetes_runtime.py` | 计划、执行、状态、日志与持久操作回执 | 所有权、UID、资源账本、最终准入、结果未知时先对账；不简单替换为裸 apply。 |
| 模型/硬件模板提供者 | `catalog.py`、`template_library.py` | 列表、完整配置、版本与来源标识 | 保留完整原生 JSON、自定义模板修订及权限，不能把模板可见当部署可用。 |
| 镜像仓库与镜像校验 | `integrations.py`、`image_contract.py` | 镜像目录、只读探测、版本兼容报告 | 凭据保护、镜像摘要、架构/原生包核对；不在未审批时拉起占卡容器。 |
| 资源观测来源 | `node_exporter.py`、`resource_telemetry.py` | Node/NPU Exporter 映射、指标单位、时效、健康与进程观测 | 保留未知/陈旧状态，不能替代资源预留账本，不额外探卡或重复实现宿主 CPU 采集。 |
| 布局建议策略 | `placement_recommendations.py` | 基于受信快照返回候选与排除理由 | 建议不预占设备，部署前仍执行宿主与 Agent 最终资源准入。 |
| 指标、Grafana 与告警 | `service_metrics.py`、`extensions.py` | 指标查询、可验证看板资源、通知适配器 | 来源/采样时效、凭据及目标授权、失败隔离；不开放匿名管理员。 |
| 日志解析与输出 | `log_records.py`、`observability.py` | 日志来源/格式适配、受控外部输出 | 脱敏、访问范围、限流与保留窗口；关闭插件不关闭宿主必须的运行/审计记录。 |
| 网关与 CCAE | `gateway_registry.py`、`native_contracts.py` | 网关发现/连接检查、原生 Reporter 配置指导 | 网关连接不等于安装/路由创建；控制能力及外部系统必须单独实测。 |
| 身份源 | `oidc.py`、`auth.py` | OIDC/LDAP 等身份提供者接口 | 本地恢复管理员、会话撤销、RBAC、CSRF、审计始终由核心执行。 |
| 数据库/备份驱动 | `ports/storage.py`、`storage/registry.py` | 早期存储 bootstrap、Repository/UoW、CAS、备份恢复 | 必须在打开数据库前选择、校验迁移和恢复；当前运行期社区包不能直接换数据库。 |
| 前端主题、国际化及复杂页面 | `appearance.tsx`、页面组件 | 单独的构建期 UI SDK、资源/翻译合同 | 对比度、无障碍、草稿和权限；当前导入包不提供远程 JS、任意 HTML 或 CSS 执行。 |

新增提供者应先说明具体调用方、输入/输出、生命周期、权限、失败状态及升级兼容性，再通过[插件提案](../.github/ISSUE_TEMPLATE/plugin-proposal.yml)讨论接口。接口版本与提供者包版本分开管理；只有声明而没有实际装配和调用方的模块，不应列为已支持能力。

## 贡献与发行检查

贡献流程见[开发指南](PLUGIN_DEVELOPMENT.md)、[完整示例](plugins/examples/community-status/README.md)和[安全策略](../SECURITY.md)。使用 `/api/plugin-sdk` 确认目标宿主的 `features`，在提案和 PR 中说明所需合同。

发行前检查目标 Linux 文件系统的进程锁、原子发布与恢复、依赖 SBOM 和漏洞扫描、许可证及安全报告通道。包摘要只标识内容；插件来源和作者身份仍需单独审核。
