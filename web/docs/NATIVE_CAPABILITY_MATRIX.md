# 原生 Motor 能力、平台入口与验证矩阵

本矩阵说明平台 0.11.1 的入口范围，以固定原生提交 `37d615f232a36b8dba8630ffc3a01a20d289c65e` 为依据，核对官方声明、实际执行入口、平台操作和验收边界。入口说明以仓库中的运行时适配器为准；插件停用后，对应入口不可用。不同引擎、镜像、硬件和外部系统需要独立验证，不能从单一部署结果外推全部能力。

## 固定源码与镜像版本

本表描述源码提交 `37d615f232a36b8dba8630ffc3a01a20d289c65e` 与当前适配入口。该提交不是官方 `3.2.0` 标签对应的 `96846215cf4f05aaf44068e1784f25d64c18e845`。镜像标签、Python 包版本、源码 profile 和平台版本分别记录，不能将任一名称当成另一项的兼容证明。

候选版本的官方能力表可能增删项目或改变引擎声明，不能直接套用本表。新版本应重新核对实际入口、依赖和运行合同，见 [原生集成](NATIVE_INTEGRATION.md) 与 [升级审查](MOTOR_UPGRADE.md)。本文中的“部署验证要求”是操作者需要完成的检查，不是所有目标环境均已通过的声明。

## 如何使用本表

- **官方声明**：来自该提交的 [supported_features.md][official]，不是本平台的验收结论。21 项均声明 Kubernetes 支持；14 项声明 Docker 支持，其中 EPD 与固定 Docker 启动入口存在差异；7 项声明仅 Kubernetes 支持。9 项同时声明 vLLM Ascend 与 SGLang，其他 12 项仅声明 vLLM Ascend。动态分桶为官方 POC，其他项目在该表中标为功能正常。
- **入口可达**：代码存在从配置到实际消费者的路径，仍依赖镜像、模型、运行时、通信和外部组件。能保存一个字段、生成 YAML 或显示页面，不证明功能已经生效。
- **平台专用操作**：存在对应业务 API/按钮，而非仅保存原生 JSON。通用部署、停止、重建、日志与推理调试，不自动成为每项特性的专用管理器。
- **部署验证要求**：应绑定精确版本、引擎、模型、硬件、拓扑和原始请求结果。配置解析、清单生成、容器健康和真实业务验证分别记录；P/D 主链成功不能替代某项特性的负载、故障或外部系统验收。
- **JSON/env 通道**：配置按原生格式保存并交付，原生进程消费。本版没有把所有字段转换成专用表单，也没有专用原生热更新执行入口；保存配置修订不会自动调用 `deploy.py --update_config`。原生支持的热更新范围仍以固定版本白名单、实际 watcher 和运行进程为准。

平台 `catalog.capabilities()` 保留 `validation: native_declared`，并为 EPD 单列 `execution_supported`；不能将 `docker_supported` 等官方声明字段解释为当前入口已经执行成功。源码位置：[`catalog.py`](../backend/catalog.py)。功能配置页提供离线说明、增量示例和本地源码定位；复制示例不会自动保存或部署。

## 21 项官方能力

表中“vLLM”指官方列的 vLLM Ascend，“两者”指 vLLM Ascend 和 SGLang。“当前入口可达”说明配置有实际消费者，不表示依赖自动安装、配置可热更新或业务已验收。

| 编号 | 官方能力 / 引擎 | 官方 Docker / K8s | 当前入口可达 | 平台可操作 / 是否仅 JSON | 部署验证要求 | 原生或适配固有限制 |
| --- | --- | --- | --- | --- | --- | --- |
| F01 | PD分离 / 两者 | 是 / 是 | Docker 与 K8s 的 P/D 注册、实例就绪和原生路由链可达 | 原生 JSON、角色/设备布局、预检、部署、停止、重启/重建、拓扑、日志、调试；不是仅 JSON | 逐实例核对角色、DP endpoint 与 ACTIVE；验证普通/SSE、跨 P/D KV 交接、预期路由及故障恢复 | P/D 实例、DP endpoint、Pod、容器和 NPU 数是不同维度；权重/HBM、Connector 与网络必须匹配。见 [F01 依据](#f01-pd主链与部署边界) |
| F02 | EPD分离 / vLLM | 是 / 是 | K8s Encode 模板与角色目标可达；固定 Docker 入口无 Encode 角色，平台拒绝执行 | K8s JSON 配置、布局、原生清单及 Encode 就绪观察；无独立多模态验证向导；Docker 不能借 JSON 保存宣称支持 | 使用真实图像/视频验证 E→P→D、EC Connector/共享存储与各角色就绪，不能只检查生成的 Encode 清单 | 不支持 single_container/Union；E/P EC Connector 与共享存储一致，P/D KV 链路独立核对。见 [F02](#f02-epd声明与入口差异) |
| F03 | 动态分桶调度 / vLLM，POC | 是 / 是 | Coordinator Decode 调度消费者可达 | JSON 设置 `decode_scheduler_type=dynamic_bucket`；通用部署；无专用调参、收益归因或分桶验收按钮，属于配置通道 | 验证至少两个合格 Decode endpoint 的长短桶、真实输入长度分布和回退；收益需同负载对照 | 仅 Decode；至少两个合格 endpoint 才有长短双桶。token 化失败或无候选会回退负载均衡/轮询；不能把配置成功当作实际使用该策略。见 [F03](#f03-f07-调度与kv配置通道) |
| F04 | KV Cache亲和性调度 / 两者 | 是 / 是 | Coordinator→Conductor 查询与事件索引路径可达；K8s 可生成 Conductor；Docker 需已有 Conductor | JSON/env、原生依赖预检及通用生命周期；无专用 Conductor 安装/池化命中验收器，主要为配置通道 | 分别验证事件源、HBM/CPU/Disk 索引命中、实际选路和 Conductor 重启后的恢复 | Prefill/Union 与 Decode 策略不可混淆；tokenizer、block_size、事件源必须匹配；查询缺失/低命中可回退。Conductor 重启后的集中 pool 订阅恢复有固定限制。见 [F04](#f03-f07-调度与kv配置通道) |
| F05 | Agent Hint 会话亲和与调度 / vLLM | 是 / 是 | 原生 `X-Agent-Hint` 解析、会话表、优先级等消费者可达 | env/JSON 配置；Header 由实际客户端构造，平台普通文本调试不覆盖完整 Hint 协议；无会话管理器，主要为配置通道 | 构造真实 Hint Header、跨 Worker 共享表、优先级与 cache_control；普通文本请求不覆盖此协议 | 此 Agent 指应用请求 Hint，不是平台执行 Agent。跨 Worker 依赖共享内存；共享表失败可能回退进程内。不是跨主机共享表，也不保证引擎 KV 不驱逐。见 [F05](#f03-f07-调度与kv配置通道) |
| F06 | KV池化能力部署 / vLLM | 是 / 是 | 原生 Store/Connector 路径可达；平台 Docker 打包 MemCache/Mooncake 启动路径 | JSON/env、KV store 布局及通用部署；无独立数据写读、清理和层级命中验收器；不是所有后端均可 Docker 部署 | 在选定后端完成真实写读、层级命中、迁移和重启恢复，并核对 Connector 与内存预算 | Docker 对未打包后端显式拒绝；YuanRong 等依赖不能由任意 JSON 自动补齐。P→D 实时 KV 传输与 HBM/DRAM/Disk 池化不同；模型/Connector/内存预算须专项核对。见 [F06](#f03-f07-调度与kv配置通道) |
| F07 | max_tokens自适应 / vLLM | 是 / 是 | 原生 dispatch 在调度前执行上下文预算调整 | JSON 设置 `context_budget_mode`；无专用裁剪追踪或边界请求生成器，属于配置通道 | 验证超长输入、字段优先级、上下文边界与有效裁剪，缺少 token IDs 时核对原生行为 | 仅处理正整数输出预算，需可信 token IDs 与有效上下文上限；无 token IDs、无有效上限或输入已占满上下文时不会伪造裁剪。见 [F07](#f03-f07-调度与kv配置通道) |
| F08 | 自动弹性扩缩容 / 两者 | 否 / 是 | 原生 InferServiceSet 角色 scalingPolicy 可达；需集群指标适配器 | JSON `scaling_policy`、原生清单与角色目标/Pod 收敛观察；没有 HPA 安装/标定/负载驱动向导，主要为配置通道 | 验证外部指标与 Operator、真实负载触发的扩缩、资源不足/指标缺失及请求连续性 | 不是 Docker 自动择机；仅适用受支持 CRD 模式。指标缺失不是零负载；需 Operator/外部指标、空闲资源及单一副本控制者。见 [F08–F16](#f08-f16-伸缩启动加速与恢复) |
| F09 | 手动扩缩容 / 两者 | 否 / 是 | K8s受控ISS角色目标变更可达，不覆盖原生CLI所有部署模式 | 专用 scale plan/submit API 与窗口；调用原生纯helper，再按UID/resourceVersion受控patch；不是仅 JSON | 验证计划、在线扩/缩的真实角色收敛、冲突拒绝、配置一致性与推理；停止/重建不能替代在线伸缩 | 当前仅单个受管InferServiceSet+motor-config，spec.replicas=1、角色数量1..16；拒绝scaling_policy/scalingPolicy及命名空间内HPA，拒绝配置/角色漂移。非Docker、非通用Deployment在线伸缩。见 [F09](#f08-f16-伸缩启动加速与恢复) |
| F10 | 容器快照 / vLLM | 否 / 是 | 原生快照配置、生成器和 NodeManager 路径可达，外部 CRIU/NPU 插件前提独立 | JSON 配置和原生部署；无快照制作/恢复/库存/过期管理专用 API，属于配置通道 | 验证 Host/Device 快照制作、冷/热恢复和版本/硬件兼容；通用渲染不覆盖快照恢复 | 依赖匹配 OS、containerd、定制 CRIU/设备插件及快照调度能力；路径、模型/并行/硬件兼容须验证。不是平台 MCBA 数据库备份。见 [F10](#f08-f16-伸缩启动加速与恢复) |
| F11 | D2D 权重加载 / vLLM | 是 / 是 | Controller 收集同角色 ACTIVE peer；NodeManager 消费 netloader 配置 | JSON 设置 `model_loader_extra_config`，走通用部署；无专用传输/回退验证按钮，属于配置通道 | 先从磁盘启动 seed，再验证后续实例确实经 ACTIVE peer 传输权重，记录无 peer/失败回退 | 首实例仍从磁盘加载；需匹配镜像 netloader 与模型、开放计算后的端口。没有 peer 或回退磁盘不证明 D2D 已启用。见 [F11](#f08-f16-伸缩启动加速与恢复) |
| F12 | 请求重调度 / vLLM | 否 / 是 | 共享 Coordinator 代码含请求重试/续推实现；官方正式范围仍为 K8s | JSON 配置；无平台请求重放器和断点续推控制器；Docker 字段可保存不等于官方支持 | 注入原生故障，分别验证未输出请求和已输出 SSE 的续推边界、Token-ID 协议与结果一致性 | 输出后续推依赖 token IDs、Token-ID Completions；限制 n=1/文本等协议语义。不能无条件重放已输出 SSE，不能与 Controller TokenReinference 占位策略混为一谈。见 [F12](#f08-f16-伸缩启动加速与恢复) |
| F13 | 实例重调度 / 两者 | 否 / 是 | 原生 Pod 标签与外部调度组件可达 | 原生模板/JSON 配置、通用状态观察；无平台替代集群调度器或专用故障迁移按钮 | 注入实际节点/Pod 故障，检查整实例迁移、存储与身份一致、ACTIVE 恢复和推理 | 依赖 MindCluster、Volcano/Device Plugin/ClusterD 等配套；多 Pod 整实例、持久存储、标签/亲和和剩余容量须满足；Running 不等于 ACTIVE。见 [F13](#f08-f16-伸缩启动加速与恢复) |
| F14 | 故障实例重启 / 两者 | 是 / 是 | 原生 Controller/NM 引擎重拉及恢复状态机默认入口可达 | 配置/原生默认策略；平台“重启服务”是另一种显式管理操作，不是该策略开关；有通用任务，Docker 另有持久操作回执 | 验证原生引擎故障重拉、预算耗尽与最终推理恢复；平台显式重启是另一种管理操作 | 默认 enable_engine_relaunch 与恢复预算有独立配置；引擎重拉、容器重建、迁移节点是不同层次；中间 relaunch 日志不等于最终可推理。见 [F14](#f08-f16-伸缩启动加速与恢复) |
| F15 | ScaleP2D故障恢复 / vLLM | 否 / 是 | 原生 Controller 策略和 K8s 优先级/实例删除路径可达 | JSON 与原生角色调度策略；无独立故障注入/可释放P容量审批器，主要为配置通道 | 在满足容量与至少保留一个 P 的布局下注入指定 D 故障，核对释放、替换及请求恢复 | 会停止 P 以释放 D 需要的容量；至少要保留一个P，1P1D不能据此成功恢复。选P成本算法标为placeholder，实际仍按INITIAL优先/ID排序，不是成本最优。仅指定D故障/部署前提；异构PR/DT不能直接互换。见 [F15](#f08-f16-伸缩启动加速与恢复) |
| F16 | 主备倒换特性 / 两者 | 否 / 是 | 原生 Controller/Coordinator 独立选主及 etcd 路径可达 | JSON/原生生成器；无平台 etcd 安装或选主演练管理器，属于配置通道 | 验证主进程退出/网络隔离、etcd 选主、入口切换和请求连续性；记录真实中断窗口 | 原生两组件 HA 不代表平台 Control/Web/数据库 HA；需 etcd、持久化、反亲和及入口。备节点 readiness 非就绪可为预期，不能承诺零中断。见 [F16](#f08-f16-伸缩启动加速与恢复) |
| F17 | 虚推健康探测 / 两者 | 是 / 是 | vLLM 主动虚推与 SGLang 原生生成式 health 路径不同，均有实现 | JSON `health_check_config`，通用状态/日志；没有专用探针过程与误判验收页面，主要为配置通道 | 验证忙碌/空闲、首次 warmup、排队、超时、误判与恢复；区分 vLLM 和 SGLang 探针链 | 依赖可用AI Cube指标；vLLM首次 warmup 与周期超时不同；忙碌/capacity排队不应计作失活。SGLang自身health不随vLLM主动虚推开关关闭。见 [F17–F21](#f17-f21-健康保护观测与混淆) |
| F18 | 服务限流 / 两者 | 是 / 是 | Coordinator 启动时装配 simple/OLC 中间件 | JSON 配置；无共享全局配额服务/按租户控制台，属于配置通道 | 验证 Worker 数量下的实际配额、突发/恢复、OLC 异常回退以及直接访问引擎的旁路 | simple 是每Worker独立桶；scope不是跨Worker/跨节点总配额，也不自动按IP分桶。OLC异常可回退simple；直连引擎绕过Coordinator限流。见 [F18](#f17-f21-健康保护观测与混淆) |
| F19 | 精度检测特性 / vLLM | 是 / 是 | Coordinator采样→msprobe→告警及Controller可选处置路径可达 | 可保存原生 JSON 字段、查看离线说明与通用日志；无专用热更新执行入口，也没有精度基准平台或异常注入/处置完整工作台 | 验证模型映射、采样对齐、真实异常告警及可选终止；核对检测开销和处置权限 | msprobe模型映射、token/logprob对齐及采样开销独立；检测与自动处置是两个开关，自动处置可能终止P/D。CCAE是另一条控制链。见 [F19](#f17-f21-健康保护观测与混淆) |
| F20 | Tracing能力部署 / vLLM | 是 / 是 | Coordinator OTLP exporter及trace上下文传播可达；引擎侧需相应SDK | JSON/env及通用部署；没有内置Trace后端安装/查询工作台，属于配置通道 | 使用真实请求验证 Coordinator/引擎 Span、采样、取消和故障关联，确认后端可查询 | 非空endpoint和合法协议启用，非通用enable字段；仅Coordinator配置不证明P/D Span。Collector/Tempo/Jaeger及TLS、存储均需部署。见 [F20](#f17-f21-健康保护观测与混淆) |
| F21 | 数据混淆推理 / vLLM | 是 / 是 | Render/token-only、token/图像混淆服务与协议拒绝路径可达 | JSON/env、可生成Render辅助容器及通用部署；没有专用权重转换/seed秘密注入/安全验收工作台 | 使用匹配 SDK/seed/权重验证文本/图像语义、实际载荷变化及失败拒绝，不能只检查启动 | 同SDK/seed/白名单/模型几何的混淆权重；只支持有Render合同的接口；SDK/Render失败拒绝而非明文回退，但仅启动成功也不能证明SDK确实改变载荷。见 [F21](#f17-f21-健康保护观测与混淆) |

## F01 P/D主链与部署边界

Docker 适配器调用固定 deployer 的设备、绑定、端口和启动 helper，生成容器规格，由执行 Agent 通过 Docker SDK 管理容器；内层仍执行原生 `docker_deploy.py --start` 路径。`build_in_place_inner_command` 是 helper 函数名，不是新增的 `--in-place` CLI 参数。K8s 适配器执行固定 `deploy.py --dry-run` 生成原生清单，再按平台所有权、UID与准入条件执行；不是重新实现原生 Operator。

平台负责资源布局建议、准入和执行结果管理；Docker 有持久操作回执，K8s 采用资源所有权与 UID 记录。Controller/NodeManager负责实例生命周期；Coordinator负责请求选路；引擎负责批次、并行和KV。外部客户端通常直接访问 Coordinator/网关，不经过平台 Agent 或控制数据库；平台内 K8s 推理调试通过受控 Agent 接口访问服务。Docker容器依赖原生注册协议协作，不依赖Docker自身提供K8s式调度器。资源建议不是已经证明的全局拓扑最优调度。

依据：[`motor_adapter.py`](../backend/motor_adapter.py)、[`kubernetes_runtime.py`](../backend/kubernetes_runtime.py)；[原生P/D说明][pd]。完整配置、不同引擎、网络/Connector、生命周期与原生请求路由分别验收，不从单一成功请求外推全部并发/故障路径。

## F02 EPD声明与入口差异

[官方表][official]声明Docker支持EPD，但原生 [`docker_commands.py:154`][docker-commands] 对正 `e_instances_num` 直接拒绝，角色生成只有 Prefill/Decode/Union。平台 [`motor_adapter.py`](../backend/motor_adapter.py) 同样拒绝不完整Docker计划，并明确single_container不启动Encode。K8s可渲染Encode角色，并有角色目标与Pod收敛检查；仍需真实多模态模型、EC共享存储及KV链路验收。不能为追求“覆盖全部”绕过拒绝或暗改上游。

## F03-F07 调度与KV配置通道

| 能力 | 核对的原生消费位置 | 需要追加的实际证据 |
| --- | --- | --- |
| F03 动态分桶 | [`scheduler_client.py:2554`][scheduler-bucket] 调用Decode分桶，失败后回退；[`dynamic_bucket.py:205`][bucket-split] 保证多endpoint时非空双桶 | 实际policy、输入token长度、长短桶成员、选中endpoint、负载与回退；同负载基线比较收益。POC不升级为生产收益保证 |
| F04 KV亲和 | [`kv_cache_affinity.py:198`][affinity] 查询Conductor与分层命中；[`generator/kv_conductor.py:23`][conductor-generator] 生成K8s角色 | 真实注册/事件写入、同前缀命中与未命中、HBM/CPU/Disk独立计数、选路与重启后恢复。平台[`native_contracts.py`](../backend/native_contracts.py)检查依赖，但依赖字段存在不等于Conductor ready |
| F05 Hint | [`agent_hint_wire.py:161`][hint-wire] 解析Header；[`session_affinity.py:270`][session-table] 创建共享/进程内表 | 同会话跨Worker、epoch/stale/TTL、优先级及字段传输；共享表失败回退须可见。平台普通文本调试API只构造固定Chat请求，不是完整Hint协议测试器 |
| F06 池化 | [`node_manager.py:937`][nm-config]解析Store配置与后端依赖，原生Store启动脚本与引擎Connector消费；平台[`motor_adapter.py`](../backend/motor_adapter.py)仅接受有Docker启动脚本的后端 | 实际远端写读、命中与驱逐、同布局/版本命名空间、内存压力、Store/Connector重启；不能用cached_tokens单字段证明完整池化生效 |
| F07 上下文预算 | [`kv_cache_affinity.py:60`][context-budget] 在不同调度策略前统一裁剪；[`dispatch.py:381`][dispatch-budget]实际调用 | `max_tokens`/`max_completion_tokens`优先级、真实token IDs、P/D有效上限、输入溢出与未提供预算等边界；不是粗略字符数裁剪 |

F04需要区分亲和索引与实际KV存储。Docker本版没有Conductor布局角色；管理员须先准备受支持的外部实例并配置地址。原生集中pool订阅在Conductor单独重启后的恢复仍有已知限制，见平台[`native_contracts.py`](../backend/native_contracts.py)；配置通过不能消除该限制。

## F08-F16 伸缩、启动加速与恢复

这些能力分属于原生Controller/NM、Kubernetes Operator/调度器和外部依赖，不能统一解释成“Agent帮忙重启一个容器”。

- **F08**：原生InferServiceSet生成器把`scaling_policy`写入角色`scalingPolicy`。HPA依赖受支持的InferOperator和外部指标适配器；平台不安装指标适配器、不把指标缺失转换为零，也不声称Docker具备同样自动伸缩。
- **F09**：平台有专用`/scale/plan`、`/scale`入口；[`kubernetes_runtime.py`](../backend/kubernetes_runtime.py)明确不运行原生`--update_instance_num`CLI，而是调用原生纯helper `validate_only_instance_changed`、`validate_instance_nums`、`update_infer_service_replicas_only`，再按UID/resourceVersion执行角色replicas与motor-config的JSONPatch（`:833`）。当前仅支持`infer_service_set`、单个受管`mindcluster.huawei.com/v1` ISS及motor-config、`spec.replicas=1`、角色数量1..16；`:769`拒绝配置的伸缩策略及命名空间内任意HPA，`:782`/`:799`拒绝ConfigMap/角色目标漂移。多资源部分提交失败保留`scale_pending`，不盲回滚。不能宣称已经覆盖原生CLI的所有部署模式；声明数量、当前Operator目标与实际注册数也分别观察。
- **F10**：快照配置的原生入口存在，但制作/恢复仍依赖外部CRIU、设备插件与存储。自动模板校验路径要求`infer_service_set`；已经提供`snapshot_metadata_path`时原生校验会走不同分支，不能把前述模式限制泛化为原生所有手工恢复路径。平台未提供独立快照生命周期管理API，数据库备份也不能恢复设备权重/进程状态。
- **F11**：[`instance_assembler.py:1099`][d2d-controller] 以`source=auto`和`listen_port`判定开关，`:1144`收集同角色ACTIVE peer；[`vllm/config.py:215`][d2d-nm] 生成设备/DP偏移的peer源，`:354`消费netloader配置。首seed冷加载正常；需要新peer的真实传输与回退证据。
- **F12**：原生请求重调度与输出后Token-ID续推具有接口、工具/多模态、流式提交和取消边界；官方表只声明K8s支持，不能从共享代码存在推导Docker正式兼容。P/D说明中的Docker RAS文字又提及“故障请求重调度”，与总表口径不同；本文保留总表原始声明并列出实现差异，不擅自改为Docker完整支持。Controller的`enable_token_reinference`是另一种策略，固定实现仍为占位，平台对显式启用拒绝并对默认风险提示。
- **F13**：整实例迁移依赖集群故障调度与资源容量；平台有原生清单/所有权和状态核对，不替代Volcano/ClusterD。单Pod Running或单成员重建不证明多Pod整实例已恢复。
- **F14**：原生默认引擎重拉与平台“重启服务”分开。NodeManager完整服务配置的历史键是角色内`motor_nodemanger_config`；不要自作主张改成其他拼写或放错层级。
- **F15**：ScaleP2D可能停止Prefill让出资源，有容量和故障等级前提；保留最后一个P。不将文档中与容量公式矛盾的1P1D示例作为成功验收依据。`_select_instances_algorithm`标为placeholder并记录`algorithm_not_implemented`，但实际仍按所需实例数、INITIAL优先及ID排序返回候选；不能误写成整个策略完全未实现，也不能称已具备生产成本最优选P算法。
- **F16**：Controller与Coordinator各自选主，依赖etcd、反亲和和可随主切换的入口。原生HA不等于本平台单控制写者已获得HA，也不保证业务零中断。

各项官方说明：[自动伸缩][hpa]、[手动伸缩][manual-scale]、[快照][snapshot]、[D2D][d2d-guide]、[请求重调度][reschedule]、[实例重调度][instance-reschedule]、[ScaleP2D][scale-p2d]、[主备倒换][standby]。平台执行入口、原生实现与限制的精确补充见下方源码索引。

| 能力 | 实际源码定位与边界 |
| --- | --- |
| F08 HPA | [`infer_service.py:224`][hpa-generator]构造策略，`:288`应用，`:366`写入角色，`:461`处理作用域；生成角色策略不是安装HPA指标适配器 |
| F09 手动数量 | [`config_validator.py:118`][manual-validator]限制只改实例数；[`generator/engine.py:443`][manual-counts]校验数量；[`infer_service.py:600`][manual-replicas]更新ISS角色；平台受控执行范围见上文，不调用有集群副作用的原生CLI |
| F10 快照 | [`container_snapshot.py:53`][snapshot-source]校验自动模板路径并限制模式，`:66`已有metadata提前返回；`:106`标签、`:113`runtime、`:150`存储、`:206`应用；[`node_manager.py:77`][snapshot-nm]快照模式禁配置watcher。快照文件存在、恢复元数据接收和业务ready分别判断 |
| F12 续推与占位区别 | [`rescheduler.py:131`][rescheduler-source]判断是否允许输出后续推，`:192`构造重试计划；[`token_reinference.py:18`][token-placeholder]是独立Controller占位策略，不能用前者证明后者可用 |
| F13 整实例迁移 | [`generator/engine.py:227`][instance-labels]在DP缩容/引擎FT组合条件下设置外部故障调度标签；[`recovery_service.py:36`][instance-recovery]先隔离再逐NM停止；[`pod_lifecycle.py:24`][pod-lifecycle]明确后续Pod回收由MindCluster负责，不是平台Agent独立完成迁移 |
| F14 引擎重拉 | [`engine_relaunch.py:112`][engine-relaunch]探测/派发NM并等待NORMAL；失败时按是否已派发决定abort和外部容器恢复；[`daemon.py:215`][nm-relaunch]串行重拉、暂停FT并后台等待ready。不能以API返回或中间日志替代最终ACTIVE/真实推理 |
| F15 ScaleP2D | [`scale_p2d.py:539`][scale-p2d-source]选择P，`:587`可释放节点为每P节点数×(可用P数−1)，`:640`占位选择成本算法，`:674`开始停止/释放P |
| F16 选主 | [`standby_manager.py:96`][standby-source]启动，`:171`主备循环、`:204`租约续期、`:268`争取etcd锁；租约存在不证明业务RTO/RPO或零中断 |


## F17-F21 健康、保护、观测与混淆

| 能力 | 实际消费位置 | 关键验收边界 |
| --- | --- | --- |
| F17 虚推 | [`virtual_inference/worker.py:226`][virtual-worker]；[原生说明][virtual-guide]区分vLLM与SGLang | vLLM先warmup再周期探测，结合AI Cube与capacity waiting，不将监控缺失/排队报为硬件失活；SGLang走自身生成式health。必须验证真实故障与正常繁忙场景 |
| F18 限流 | [`inference_server.py:503`][rate-setup]装配中间件，`:525`每进程创建SimpleRateLimiter；[`rate_limiter.py:128`][rate-limiter]持有本地桶 | 测Worker总额度、突发、skip路径/请求体、OLC回退与429；不能称跨进程总限流或默认按IP/用户分桶。首次装配与部分配置更新范围不同 |
| F19 精度 | [`inference_server.py:376`][precision-init]建立采样/Reporter；[原生精度说明][precision-guide] | 完整请求的token/logprob对齐、检测结果与阈值、告警/清除；自动处置独立控制且可能终止实例。依赖import成功、启动日志或无错误均不足以验收 |
| F20 Trace | [`tracing.py:48`][tracing-source]读取endpoint/采样，`:106`建立exporter，`:144`解析协议 | 真实Span、父子关系、采样、OTLP协议/TLS、后端检索、失败/取消；不是安装Grafana便自动产生Trace。平台没有替外部Collector/后端执行完整安装 |
| F21 混淆 | [`coordinator.py:1197`][obfuscation-schema]校验Render/token-only/白名单/seed；[`inference_server.py:427`][obfuscation-init]创建服务；[`dispatch.py:352`][obfuscation-api]拒绝无Render合同的API | 同版本SDK/seed/几何的权重、文本/图像往返与拒绝明文回退；`seed_content`需受控秘密注入，普通模板保存不是秘密注入工作台。SDK可能原样返回载荷的模型几何边界需单独验证 |

## 观测、CCAE与网关集成

以下为集成能力，不额外计入官方21项。

| 集成 | 原生来源与当前平台入口 | 可执行范围 | 外部准备与验收 |
| --- | --- | --- | --- |
| CCAE Reporter | 原生角色启动 Reporter，等待 `north_config` 与 `north_tls_config`；`native.ccae` 是原生配置通道 | 保留 JSON 并提示控制副作用，`external_system_verified=false` 不代表已检查外部状态 | 无 CCAE 本体安装器、专用逐项健康或控制闭环工作台；需验证注册、库存、告警、Kafka 和控制回执。`precisioncontrol` 可终止 P/D；`switchControl/immediateDelivery` 仅记录状态 |
| Kafka 日志 | Reporter 从 CCAE 注册回包获得 topic/端口，直接创建 Producer，不经平台 Agent | 传递原生配置，HTTP 注册与 Kafka 投递分别判断 | 需 broker、topic/ACL、TLS、消费回执及断连恢复；默认 Controller/Coordinator 文件不等于引擎、OS 和平台全部日志 |
| Grafana / Prometheus | 保存连接、只读健康/查询与打开入口；管理员可下载配置/目标/看板及 SHA 清单 ZIP；另有独立 Docker 观测模块 | 检查状态 TTL 为 300 秒；配置生成只处理受管 Docker Coordinator，下载不会启动服务 | 按[观测手册](../observability/README.md)独立安装、配置和验收；无 K8s 完整目标自动生成。宿主/NPU 看板不替代模型指标、profiling 和 Tracing |
| Tempo/Loki/OTel/profiling | 固定原生栈有样例，Tracing 有配置入口 | 按目标协议保存/生成配置，不据此声称已有数据 | Collector/后端、引擎 profiling 和集中日志需独立准备；用真实 Span/日志/请求及查询关联验收，空面板不补零 |
| AIGW 原生模型发现 | Coordinator `/v1/models`；平台读取受控服务入口 | 展示长度/SLO 及可用 P/D 实例数 | 实例数不是卡数、容器数或路由数；还需普通/SSE、认证、限流和故障切换验证 |
| 已有 OpenAI 兼容网关 | 连接 CRUD、模型绑定、列表检查和连接描述导出 | 管理平台连接记录，不安装网关或修改外部路由 | 没有随产品交付的官方 Motor AIGW 制品/安装合同；`deploy/stop/upgrade` 明确返回 `artifact_not_configured`。不可将同名软件或连接描述当成可部署 AIGW |
| Node/NPU Exporter | Docker 手册和部署示例；K8s 复用集群侧采集；Agent 有界读取已有来源 | Node Exporter 提供 CPU/内存/OS；裸 Docker NPU 可用宿主只读快照或经核验的 Exporter | 无通用网页一键安装器；NPU Exporter 容器归属依赖 CRI/containerd，不是挂 docker.sock 即完成。磁盘/网络历史需 Prometheus；原生虚推 AI Cube 是另一条采样链 |

CCAE源码定位：[`extensions.py`](../backend/extensions.py)、[`InfrastructureSettings.tsx`](../frontend/src/InfrastructureSettings.tsx)；原生[启动与等待][ccae-run]、[注册/上传][ccae-reporter]、[Kafka/TLS与角色差异][ccae-base]。Reporter显式使用HTTPS，关闭`enable_tls`不代表自动变明文HTTP；注册成功也不证明Kafka的SSL和消费正常。Reporter helper退出与模型业务就绪是不同状态，不从Motor容器Running推断Reporter健康。

Grafana源码定位：[GrafanaIntegration](../backend/extensions.py) 的 `settings`、`probe`、`provision` 与 `download` 分别处理保存回执投影、显式检查、配置生成和管理员ZIP下载；[原生Grafana指南][grafana-guide]提供案例。过期状态更新不自动发起外部探测，空目标不代表已采集模型数据；ZIP仅包含该次生成并校验的文件。独立[Docker观测模块](../compose.observability.yaml)复用已有Exporter，持久指标与平台数据库分开维护。原生观测栈的K8s目标发现不等于登记 Docker 宿主的自动发现。

AIGW源码定位：[`integrations.py`](../backend/integrations.py)读取原生模型列表；[`gateway_registry.py`](../backend/gateway_registry.py)连接 API 与无制品生命周期拒绝。平台不安装未知网关、不自动修改已有网关路由。详细使用边界见[已有网关](GATEWAY_CONNECTIONS.md)、[服务指标](SERVICE_METRICS.md)、[Node Exporter](DOCKER_NODE_EXPORTER.md)与[NPU采集](DOCKER_NPU_EXPORTER.md)。

## 能力边界

1. 官方声明、平台JSON通道、原生入口可执行、模型真正ready、真实业务验证是五个不同结论；版本适配检查不能自动升级验证等级。
2. 平台保持原生JSON任意字段和原生源码固定；明确不支持的入口应拒绝，而不是删字段、静默改变语义或给出模拟部署成功。
3. 平台已知固定原生异常窗口见[`native_contracts.py`](../backend/native_contracts.py)：CAS提交后补偿、release ACK后本地清理、最后实例DEL与空SET补偿、TLS回退、agent-status鉴权、Conductor恢复及TokenReinference占位。静态风险、已知限制、已复现缺陷与已修复项必须区分，不将提示警告描述成原生问题已解决。
4. UI普通文本调试不能替代完整OpenAI原生接口、工具/多模态、Hint Header、Token-ID续推、Trace或混淆协议验收。真实客户端入口须与所选配置一致。
5. 没有CCAE/Kafka、Grafana/Prometheus、AIGW、HPA/EPD、快照或故障注入专项证据的功能，不标记完整业务验收。现有P/D主链验证不自动覆盖它们。

## 后续原生升级的最小合同

平台、Agent协议、native source profile、推理镜像中的Motor/引擎版本分别记录。镜像名称或源码版本字符串相同都不能代替固定字节、CLI、SDK/ABI和行为合同一致。当前[`compatibility.py`](../backend/compatibility.py)校验source manifest、外部固定SHA、driver、Agent协议和绑定；自定义manifest通过完整性检查不代表所有特性已经兼容，也不是上传任意驱动代码或自动升级现有服务。

Agent [`node_agent.py`](../backend/node_agent.py)的`/agent/health`从当前`public_profile()`返回`motor_commit`与`motor_profile_id`，以所选 profile 的身份为准；正确profile应与鉴权`/agent/meta`一致。manifest缺失、错误pin或不兼容profile时健康检查失败关闭，不返回`ok`。此处只检查配置身份和manifest本身，不连接Docker、执行候选代码或扫描全部原生文件；原生字节完整性和模型业务就绪仍由相应预检及实际验收证明。升级差异与人工放行流程见[原生升级审计](MOTOR_UPGRADE.md)。

| 升级步骤 | 必须核对的内容 |
| --- | --- |
| 来源和依赖 | 新commit/manifest、推理镜像digest、实际Motor与vLLM/SGLang、SDK/Connector、原生二进制ABI；保留上一profile与可回退制品 |
| 官方能力差分 | 逐项比较21项名称/引擎/运行时/成熟度、新增/删除特性；额外核查CCAE、指标/看板、AIGW协议。不能只复制旧表 |
| deployer合同 | Docker CLI/角色、设备/端口/绑定、内层启动命令；K8s dry-run、ConfigMap、CRD/YAML字段、角色目标及UID；原生字段与未知字段原样保留 |
| 依赖和拒绝 | 明确运行时不可执行、缺依赖、未审核接口/策略的拒绝；EPD、Conductor、Store、Render、CRIU、外部指标、TLS各自验证，不伪装ready |
| 隔离合同 | 原生配置解析/角色/数量/字段更新、幂等回执、失败与未知结果、权限/证书、升降级数据库/制品兼容；旧服务profile绑定不被静默改写 |
| 真实业务 | 各目标运行时/引擎的普通、SSE、取消、reasoning/content、全部预期角色与endpoint；每个需要声明完成的高级特性另有专项负载/故障/外部回执 |
| 可观测与发布 | 指标聚合层次/序列重置、日志来源/时效、CCAE控制副作用、看板源SHA/overlay、网关路由；更新本矩阵及对应离线指南，再按实际证据发布 |

本矩阵用于说明固定源码与产品入口，不是对原生所有函数、所有并发状态或外部组件的正确性证明。增加按钮、安装组件、生成文件和通过静态检查都不能代替对应业务验收。

## 固定原生源码索引

以下链接均绑定同一原生提交；行号只适用于该提交，升级后须重新核对。

[official]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/supported_features.md
[pd]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/pd_disaggregation.md
[docker-commands]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/deployer/lib/docker_commands.py#L154
[scheduler-bucket]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/scheduler/runtime/scheduler_client.py#L2554
[bucket-split]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/scheduler/policy/dynamic_bucket.py#L205
[affinity]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/scheduler/policy/kv_cache_affinity.py#L198
[conductor-generator]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/deployer/lib/generator/kv_conductor.py#L23
[hint-wire]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/domain/agent_hint_wire.py#L161
[session-table]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/scheduler/runtime/session_affinity.py#L270
[nm-config]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/config/node_manager.py#L937
[context-budget]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/scheduler/policy/kv_cache_affinity.py#L60
[dispatch-budget]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/router/dispatch.py#L381
[hpa]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/auto_scaling.md
[manual-scale]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/manual_scaling.md
[hpa-generator]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/deployer/lib/generator/infer_service.py#L224
[manual-validator]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/deployer/lib/config_validator.py#L118
[manual-counts]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/deployer/lib/generator/engine.py#L443
[manual-replicas]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/deployer/lib/generator/infer_service.py#L600
[snapshot]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/container_snapshot.md
[snapshot-source]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/deployer/lib/container_snapshot.py#L53
[snapshot-nm]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/node_manager/node_manager.py#L77
[d2d-guide]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/startup_acceleration.md
[d2d-controller]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/controller/core/instance_assembler.py#L1099
[d2d-nm]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/node_manager/core/services/native_engine/backends/vllm/config.py#L215
[reschedule]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/fault_tolerance/rescheduler.md
[rescheduler-source]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/router/rescheduler/rescheduler.py#L131
[token-placeholder]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/controller/fault_tolerance/strategy/token_reinference.py#L18
[instance-reschedule]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/fault_tolerance/instance_rescheduling.md
[instance-labels]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/deployer/lib/generator/engine.py#L227
[instance-recovery]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/controller/core/recovery_service.py#L36
[pod-lifecycle]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/controller/fault_tolerance/pod_lifecycle.py#L24
[engine-relaunch]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/controller/fault_tolerance/strategy/engine_relaunch.py#L112
[nm-relaunch]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/node_manager/core/daemon.py#L215
[scale-p2d]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/fault_tolerance/scale_p2d.md
[scale-p2d-source]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/controller/fault_tolerance/strategy/scale_p2d.py#L539
[standby]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/fault_tolerance/standby.md
[standby-source]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/common/standby/standby_manager.py#L96
[virtual-worker]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/node_manager/core/services/native_engine/virtual_inference/worker.py#L226
[virtual-guide]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/sim_inference.md
[rate-setup]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/api_server/inference_server.py#L503
[rate-limiter]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/middleware/rate_limiter.py#L128
[precision-init]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/api_server/inference_server.py#L376
[precision-guide]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/features/precision_detection.md
[tracing-source]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/tracer/tracing.py#L48
[obfuscation-schema]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/config/coordinator.py#L1197
[obfuscation-init]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/api_server/inference_server.py#L427
[obfuscation-api]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/motor/coordinator/router/dispatch.py#L352
[ccae-run]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/features/observability/ccae_reporter/run.py#L26
[ccae-reporter]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/features/observability/ccae_reporter/reporters/ccae_reporter.py#L652
[ccae-base]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/features/observability/ccae_reporter/reporters/base_reporter.py#L74
[grafana-guide]: https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/features/observability/stack/GRAFANA_GUIDE.md
