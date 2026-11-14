# Motor 原生版本升级审查

平台版本、Agent 协议、适配驱动与 Motor 原生版本分别管理。当前内置原生基线固定为
`37d615f232a36b8dba8630ffc3a01a20d289c65e`，适配驱动为 `motor-cli-v1`。
升级平台不会自动更新该 submodule，也不会迁移已创建服务绑定的 Motor profile。

源码 commit、镜像标签和包版本是不同身份：当前内置 pin 不是官方 `3.2.0` 标签的 `96846215cf4f05aaf44068e1784f25d64c18e845`。本文的步骤适用于审查独立候选，不代表任意相近标签已获兼容认证。执行链见 [原生集成](NATIVE_INTEGRATION.md)；平台社区插件与 Motor source profile 属于不同扩展合同，导入 `.qhplugin` 不会升级 Motor。

## 先理解当前接入边界

Docker 由平台与 Agent 确定执行布局，原生 Coordinator 执行请求调度。外部客户端直接访问模型入口；平台内 Kubernetes 推理调试可通过受控 Agent 接口访问服务。
平台验证操作者选定的节点、角色、设备与端口布局，Agent 在最终资源准入后执行。
`backend/motor_adapter.py` 使用固定版 `docker_deploy.py`、`lib.docker_utils`、
`lib.generator.render` 等原生 helper 生成容器参数和容器内 `--start` 命令；Docker SDK
只接受平台实现的参数。容器内仍由原生 deployer、Controller、Coordinator、NodeManager
和推理引擎运行模型。设备声明、HBM 利用率、宿主采样、Kubernetes 资源份额是不同口径。

Kubernetes 接入由 `backend/kubernetes_runtime.py` 在私有目录复制原生 deployer，调用
`deploy.py --dry-run --nostep --skip-check --config_dir …` 生成 YAML；另外调用原生 helper
准备环境与 ConfigMap 资产。命名空间、UID 归属、RBAC 和 PVC 保留由平台执行边界检查；当前按受控 create/replace 清单执行，尚未适配为原生云原生部署 Job。
扩缩容使用 `validate_only_instance_changed` 与 `update_infer_service_replicas_only` 等原生
helper。`--dry-run` 不覆盖原生 `--update_instance_num` 的集群写入路径，不能把后者当成
只读命令。原生 YAML 渲染本身也可能只读查询节点标签，应在批准的隔离集群中验收。

原始 `user_config.json`、`env.json` 及未知字段保持原样保存、修订与导出。执行前可以明确
拒绝未支持的角色、参数或不安全输入；不能为了让界面显示“支持”而删字段、改原生调度逻辑，
或把源码声明直接当成实际可执行能力。

## 配置模型与部署产品分别适配

当前驱动复用 Python Motor 的 `user_config.json`、`env.json`、`model_configs/` 及固定 deployer。对应原生[全量参数参考](https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/configuration/config_reference.md)、[配置示例](https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/features/config_sample.json)和[热更新白名单](https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/configuration/update_config_whitelist.md)均绑定同一源码提交。升级时比较这些定义及真正消费字段的代码；保存未知字段不等于已经生成专用表单、执行配置或验证该字段生效。

原生文档中的 `deploy.py --update_config` 是有白名单限制的写入/热更新入口，不是只读检查，也不由平台保存配置自动触发。若新增该能力，需要独立核对受管资源归属、允许变更字段、原生文件更新、watcher 生效、平台修订一致性和失败恢复，不能把通用部署成功算成热更新合同通过。

`motor-cli-v1` 不是 `msctl` 适配器。当前平台没有接入 `msctl` / `ms_server` 或 `infer_server.json`；如需管理提供这套接口的 MindIE MS 产品，应分别确认发行版本、可校验二进制、配置 schema、服务身份及 TLS/认证协议，建立独立受审画像和相应驱动合同。不能仅凭同属 MindIE、命令同名、版本号相似或文档使用 `latest` 就沿用现有服务绑定，也不能把已有 JSON 通道描述成这些接口已经接通。

## 只读检查工具

在仓库根目录执行，提供操作者已审阅、可信的本地 Motor Git checkout：

```bash
python web/scripts/audit_motor_upgrade.py \
  --candidate-dir /srv/review/motor-candidate \
  --output /srv/review/evidence/motor-source-audit.json
```

Windows PowerShell 可使用单行：

```powershell
python web/scripts/audit_motor_upgrade.py --candidate-dir 'F:\review\motor-candidate' --output 'F:\review\evidence\motor-source-audit.json'
```

输出文件的父目录须已存在，文件须是新文件，并位于两个原生 checkout 之外。省略 `--output`
时 JSON 写到标准输出。`--baseline-dir` 可以指定另一份本地基线 checkout，但 HEAD 和内置
manifest 的全部受保护文件仍必须与上述固定基线一致。基线中全部普通 tracked 文件的原始
字节、文件存在性与 index 也必须和该 commit 一致，包括未收进 manifest 的文件；不能用修改
过的工作树充当比较原点。工具不接受继承的自定义
`MOTOR_SOURCE_MANIFEST` 作为静默替代基线；请在独立审查进程中使用内置基线配置。

工具仅执行固定的 Git 元数据/对象读取命令，并使用 Python 标准库解析字节、AST 与 JSON。
它不会导入、执行候选 Python，不会运行候选 `--help`、dry-run、构建或安装脚本，不会连接
服务器、更新 submodule、生成部署审批或输出 `contract-tested` profile。Git `status` 可能
触发配置的 clean filter，因此本工具用 HEAD/index 条目和工作树原始字节比较，不运行该命令。

报告包含：

- 内置 profile/manifest/驱动、适配器源码摘要、候选 commit、Git tree/index 及源码 SHA256 清单。
- 工作树原始字节变化、index 变化、缺文件、未跟踪的非 ignored 路径，以及不跟随的 symlink、
  junction、submodule 等条目。原始 CRLF/filter 字节差异不等同于 Git porcelain 的“dirty”。
- 按入口、deployer helper、启动脚本与模板、配置示例、官方能力表、原生运行时及其他源码分组的
  具体路径变化和 unified diff。默认每文件最多 160 行；`--diff-lines 0..2000` 调节，截断会标明。
- 实际调用的 helper/重新导出入口是否可静态解析、签名与常量结构、CLI 参数声明、Python 类字段
  和 HTTP 路由装饰器的变化；JSON 示例列出新增、删除、类型和值变化，不按固定键集裁剪。
- 必须补做的真实合同清单及静态分析的限制。

现有原生仓库含文档非普通条目，例如 `docs/zh/mkdocs` 实际是 Git mode `120000` 的符号链接；
工具不会跟随。受保护文件/deployer 中出现未检查的
非普通条目属于静态阻断，其余路径明确列为人工审阅项。所有候选文件和基线会在分析结束时
再次读取核对，发生并发变动则拒绝生成一个貌似完整的快照。

### 如何解释结果

报告总状态始终为 `requires_contract_acceptance`，`compatible` 为 `null`。
相同 commit、零 diff 或相同 helper 签名都不能证明运行兼容；hash 或签名变化也不自动证明
不兼容。新增角色、动态 import、动态生成的 CLI/HTTP 路由、helper 返回对象及行为变化，必须
结合实际调用与运行结果审阅。一个版本字符串也不能替代镜像 digest、驱动/CANN、引擎、硬件、
CRD/operator 和网络拓扑的验收记录。

退出码 `0` 表示静态分析完成且未发现本工具认定的阻断；仍未完成运行兼容验收。
退出码 `3` 表示已生成报告，但存在缺原生入口、缺所需 CLI/helper、无法解析关键 deployer 等
阻断项；先修正适配边界再验收。退出码 `2` 表示输入、基线、大小限制、Git 读取、并发修改或
输出失败；此时不能把部分输出当完整结论。

报告可能包含被审阅源码/示例配置的具体差异。将审查报告保存在受控本地证据目录，不要把
运行凭据、kubeconfig、真实数据库或环境私有配置提交仓库。审查工具与候选源码可离线使用，真实
硬件和外部系统验收仍依赖相应环境。

## 升级验收清单

| 合同 | 至少应记录的真实结果 |
| --- | --- |
| 来源与绑定 | 精确 commit、干净的交付字节与 manifest、唯一 profile ID、适配驱动/Agent 协议；原服务仍绑定旧 profile。 |
| 原始配置 | 保存、导入、导出、修订未知嵌套字段；区分仅保存成功与执行参数受到支持。 |
| Docker 渲染 | A2/A3/A5、PD/Union/单容器、管理面/KV/Render、设备映射、挂载、IPC、网络、端口、环境与 Docker SDK 参数。 |
| Docker 运行 | 独立 NPU 节点实际部署/停止/重启/删除；资源重新准入、过期/未知数据、账本/回执、并发冲突与失败清理。 |
| 分布式推理 | 正确 P/D 实例数、HCCS/RoCE 拓扑、实际就绪、普通与流式请求、故障与恢复；保留失败原件。 |
| Kubernetes | 候选生成的 YAML/ConfigMap、真实 CRD/operator、角色布局、EPD、RBAC、UID、存储保留、HPA、扩缩容及部分失败恢复。 |
| HTTP/安全 | Coordinator/Controller 原生接口和响应结构、timeout、错误、认证、HTTPS/mTLS；不能接受静默回退为 HTTP。 |
| 原生特性 | 能力表与入口实现逐项对应；KV Conductor/亲和/池恢复、故障容错与快照按实际配置独立验收。 |
| 外部系统 | Prometheus/Grafana、CCAE registry/Kafka/控制回执、AIGW 发现与路由使用真实已授权环境；缺环境明确保留未验收状态。 |
| 升级回滚 | 平台与原生 profile 分别保留旧镜像、数据库/配置备份；验证失败可恢复，不自动迁移旧服务或修改原生算法。 |

静态报告只是上述验收的输入，不能把它直接作为 `package_motor_profile.py --contract-evidence`
的运行验收报告。现有打包脚本记录操作者提供材料的摘要，不替操作者完成硬件/集群验收。
实际验收报告应绑定候选完整 commit、适配器/镜像版本、原始配置摘要、环境、逐项结果与原始
证据、未验收边界及审核人。通过后才按经过审阅的交付流程打包、固定 manifest SHA、安装独立
profile 并由操作者显式选择。保留旧服务与旧 profile，原生基线 submodule 仍保持固定。

## 官方文档链接的显式打包省略

官方 3.2.0 checkout 包含 `docs/zh/mkdocs` 文档 symlink（Git mode `120000`）。默认打包继续拒绝非普通文件，
不递归获取文档仓库。经操作者确认后可仅为 `docs/` 下的文档引用启用显式省略：

```bash
python web/scripts/package_motor_profile.py \
  --upstream-dir /srv/review/motor-3.2.0 \
  --commit FULL_40_CHARACTER_COMMIT \
  --profile-id motor-reviewed-FULL_40_CHARACTER_COMMIT \
  --output-dir /srv/qinghuan/native-profiles/new-profile \
  --contract-evidence /srv/review/evidence/reviewed-runtime-contracts.json \
  --omit-documentation-sources
```

`FULL_40_CHARACTER_COMMIT` 须替换为该 checkout 的完整小写 commit；profile ID 末尾也须是
同一完整 commit。输出必须是产品独立命名空间中的新目录，不能覆盖旧目录或写进源码 checkout。

此选项只省略 `docs/` 路径下 Git mode `120000`（symlink）或 `160000`（gitlink）的条目，
不解析、不跟随、不复制目标，也不递归 clone。manifest 与回执的
`omitted_documentation_sources` 都记录原始 `path`、`mode`、`objectid` 和省略原因。
其余普通 tracked 文件仍逐文件计算完整 SHA256 并核对复制结果。`motor/`、`examples/deployer/`
和原生入口中的 symlink/gitlink，或其他非普通文件，仍明确拒绝；本选项不会跳过运行代码。

回执的 `packaged: true` 只说明打包合同完成，`runtime_acceptance` 明确标为本工具未验证。
manifest 原有 `contract_status: contract-tested` 代表操作者提供的验收声明，不是打包程序自动
认证；程序只记录所提供文件的摘要。验收材料必须真实对应候选 commit、适配器、镜像与目标环境，不能用示例或占位文件代替。打包回执和真实硬件/集群验收报告应分别保留。
