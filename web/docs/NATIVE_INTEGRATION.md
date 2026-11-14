# MindIE-Motor 原生集成

平台是附加的管理控制面，复用 Motor 原生部署与运行逻辑。它不需要把 Web 代码合入 Motor 仓库，也没有取代 Controller、Coordinator、NodeManager、推理引擎或 Kubernetes 调度器。本说明解释配置从控制台到模型的实际执行链；各项特性范围见 [能力矩阵](NATIVE_CAPABILITY_MATRIX.md)。

## 四类版本身份

| 身份 | 当前合同 |
| --- | --- |
| 平台与 Agent | 产品版本、产品身份及管理/操作协议，用于控制面与执行端兼容检查 |
| 原生源码 profile | 固定 commit、source manifest 与适配驱动；内置 commit 为 `37d615f232a36b8dba8630ffc3a01a20d289c65e`，驱动为 `motor-cli-v1` |
| 推理镜像 | 操作者提供的集成镜像；须记录 digest、容器内实际 Motor、vLLM Ascend/SGLang、Connector 与 SDK 版本 |
| 运行环境 | CANN/驱动、NPU、通信网络、容器运行时，以及 Kubernetes CRD/operator 等依赖 |

镜像标签 `3.2.0` 与内置源码 commit 是不同身份；当前 pin 不是官方 `3.2.0` 标签对应的 `96846215cf4f05aaf44068e1784f25d64c18e845`。版本名称相似不能证明 helper、Python 包、接口或运行行为一致。profile 固定平台使用的源码合同，镜像中的软件仍需单独检查。新版本按 [升级审查](MOTOR_UPGRADE.md) 引入，不通过静默替换 submodule 升级旧服务。

## 原生配置与入口范围

平台使用该固定源码的 `user_config.json`、`env.json` 与 `model_configs/` 配置目录，按所选引擎、模型和运行时交付原生配置。参数定义参见固定版本的[全量配置参考](https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/configuration/config_reference.md)和[完整配置示例](https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/examples/features/config_sample.json)，不要将浮动 `latest` 文档或其他产品安装包的字段直接视为当前 profile 的合同。

| 配置或入口 | 原生用途与平台边界 |
| --- | --- |
| `user_config.json` | 保存部署与组件运行配置；`motor_deploy_config` 包含角色数、设备数、镜像、权重路径及硬件类型，Controller/Coordinator/引擎配置分别交给其消费者 |
| `env.json` | 提供配套启动环境配置，由固定 deployer/helper 处理；与服务布局、Agent 凭据等平台数据分开保存 |
| `model_configs/` | 按引擎、模型和硬件选取原生模板；自定义模板保留完整原生字段，仍需核对实际镜像与外部依赖 |
| `deploy.py` | Kubernetes 原生清单生成及原生维护入口；平台仅调用已适配并审查的路径，不代表全部 CLI 动作均有平台按钮 |
| `docker_deploy.py` 与 `lib` helper | 生成 Docker 容器规格及容器内启动命令；平台按 Docker SDK 参数合同和 Agent 准入执行 |

`motor-cli-v1` 是本平台的适配驱动标识，指上述固定 Python 脚本/helper 合同，**不是 `msctl` 命令协议**。当前平台没有调用 `msctl`，也没有接入 `ms_server` 的服务管理 API 或 `infer_server.json` 配置模型。带这些接口的 MindIE MS 产品/安装包不能直接当作当前 Python Motor profile 使用；如需支持，应以其具体发行版本、二进制、配置和 TLS/API 合同建立独立适配画像并验收，见[升级审查](MOTOR_UPGRADE.md)。

全量 JSON 保留、专用界面表单和运行时生效是三个不同层次。平台离线功能指南按能力组织，不等同于每个原生参数都有专用表单。原生仅有部分字段支持热更新，固定版[热更新白名单与操作说明](https://github.com/Ascend/MindIE-Motor/blob/37d615f232a36b8dba8630ffc3a01a20d289c65e/docs/zh/user_guide/configuration/update_config_whitelist.md)列出范围及 `deploy.py --update_config` 入口；本平台保存配置修订不会自动调用该命令，也未提供覆盖全部原生热更新的管理接口。参数是否生效仍需核对所选版本的白名单、实际配置文件、watcher 与运行进程。

## Docker 执行链

```text
原生 JSON + 角色/节点/设备布局
  → Control 配置校验与计划
  → Docker Agent 最终资源准入
  → 固定 docker_deploy.py / lib helper 生成容器规格
  → Docker SDK 创建、启动与检查容器
  → 容器内原生 --start 链
  → Controller / Coordinator / NodeManager / 引擎
```

[`motor_adapter.py`](../backend/motor_adapter.py) 导入固定 deployer 的 helper，处理设备映射、挂载、共享内存、端口、角色和容器内启动命令。Docker SDK 只接受适配器已支持的参数；不支持的规格应在执行前拒绝。`build_in_place_inner_command` 是 helper 名称，并不是新增 `--in-place` CLI。

执行端负责最终准入和容器归属核验，Docker 资源账本与操作回执持久化到 Agent 数据目录。平台锁和账本约束受管操作，不能阻止其他管理员或不受管服务直接占用同一设备；仍需独立设备归属、观测新鲜度和最终冲突检查。重启也要重新准入，不能把历史分配当作当前空闲。

Docker 不提供 Kubernetes 式跨宿主 Pod 调度。平台先确定角色放在哪个 Agent/设备，原生 Motor 再通过注册、心跳和实例状态协作。容器创建成功不代表实例 ACTIVE、P/D 通信或推理已通过。

## Kubernetes 执行链

```text
原生 JSON + Kubernetes 配置
  → Kubernetes Agent 私有工作目录中的固定 deployer
  → deploy.py --dry-run --nostep --skip-check --config_dir …
  → 原生 YAML 与 ConfigMap 资产审查
  → 命名空间 / 资源归属 / UID / resourceVersion 检查
  → kubectl create/replace 已审查清单
  → 原生 Operator 与 Kubernetes 调度
```

[`kubernetes_runtime.py`](../backend/kubernetes_runtime.py) 复用原生生成器并记录每个已创建资源的 UID；资源替换、停止、删除及部分失败恢复依据所有权记录执行。生成工作负载清单可读取节点标签，dry-run 不等于完全离线，也不证明服务器准入或实际写入权限已通过。

原生 Motor 另有云原生部署 Job 方案；**当前平台执行路径尚未适配为该 Job 方案**。两者的生命周期、资源归属或重试记录不能互换。Kubernetes 的 `ownership.json` 与 Docker 的设备账本、操作 ID 回执是不同合同。

手动扩缩容采用受控 InferServiceSet 路径和原生纯 helper，按 UID/resourceVersion 更新目标；不覆盖原生 CLI 所有部署模式。原生 `--update_instance_num` 存在集群写入路径，不能因为设置 `--dry-run` 就将其当作只读命令。当前范围及自动扩缩容冲突约束见能力矩阵 F08/F09。

## 三种调度

| 层次 | 执行者 | 依据 |
| --- | --- | --- |
| 角色与设备布局 | 平台计划与 Agent 准入；Kubernetes 场景另受原生选择器约束 | 配置、设备声明、账本、有效观测、归属和端口等；建议不证明全局最优 |
| Pod 放置与资源分配 | Kubernetes 及配置的调度器/设备插件 | 节点标签、资源份额、亲和、拓扑与集群策略 |
| 推理请求与引擎执行 | Motor Coordinator 与原生引擎 | 实例状态、P/D 角色、负载/KV/亲和策略；引擎执行批处理与并行 |

外部客户端通常直接访问 Coordinator 或已有推理网关，不经过控制台数据库和 Agent。平台内 Kubernetes 推理调试通过受控 `/agent/kubernetes/inference` 访问服务，是管理功能的例外，不能据此把 Agent 当成生产推理网关。

## 配置与能力边界

原生 `user_config.json`、`env.json` 及未知嵌套字段保持保存、修订和导出；平台布局、审计和凭据单独管理。能保存字段不意味着该运行时已实现功能、参数可热更新或依赖已安装。例如固定 Docker 入口缺少 Encode，平台拒绝 EPD 执行，Kubernetes 则按原生前置条件使用。

官方声明、当前入口可达、专用管理操作和部署验证是不同结论。KV Conductor、外部指标、CRIU、CCAE/Kafka、Grafana 与已有网关分别需要准备和验收。原生 `/v1/models` 的可用实例数不是 NPU 数；宿主/NPU 观测不能代替模型可用性。

页面和协议适配器属于本项目插件系统，不表示 Motor 会自动发现或加载本项目插件。原生行为改变时须审查适配驱动、版本化合同、指南和真实业务结果，保持旧服务及其 profile 可回退。

## 排查入口

- 部署、角色或清单问题：核对服务绑定 profile、原生配置、计划、任务和 Agent 工作目录，保留原生渲染/启动错误。
- 容器已启动但模型不可用：检查 Controller/NodeManager 实例状态、Coordinator 路由及引擎日志，再验证普通与流式请求。
- 监控空白：区分平台当前快照、服务按需指标和 Prometheus 历史抓取；参见 [服务指标](SERVICE_METRICS.md)、[观测模块](../observability/README.md)。
- 外部集成：参见 [已有网关](GATEWAY_CONNECTIONS.md) 和能力矩阵的 CCAE/Grafana 章节；保存连接不代表已安装软件或路由成功。
