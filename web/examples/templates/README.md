# GLM-5.3-Flash A3 HCCS 3P1D 自定义模板

`glm53-flash-a3-hccs-3p1d.json` 可在“模板管理 → 导入模板”中导入并保存，也可在已有自定义模板中查看和复制。模板绑定固定 Motor 配置 `motor-37d615f232a3`，运行环境为 Docker；不会修改官方模板或原生 Motor 源码。

参数依据 [vLLM Ascend v0.30.0 GLM-5.3-Flash 第 5.3 节的 A3 脚本](https://docs.vllm.ai/projects/ascend/zh-cn/v0.30.0/tutorials/models/GLM5.3-Flash.html#53-1p1d-pd-disaggregated-deployment)，由 Motor 原生配置、角色启动和 Coordinator 管理引擎。没有引入文档中的独立演示代理，也没有启用第 5.4 节的 KV 缓存池。

| 项目 | Prefill | Decode |
| --- | --- | --- |
| Motor 实例 | 3 | 1 |
| 每实例宿主机 | 1 | 1 |
| 每实例逻辑设备 | 16 | 16 |
| 实例内部并行 | DP2 / TP8 / PP1 | DP4 / TP4 / PP1 |
| 单引擎序列上限 | 64 | 32 |
| 单次调度 token 上限 | 4096 | 192 |
| MTP | 3，draft eager | 3，draft graph |

这里的 3P1D 是四个独立 Motor 实例组，使用四台 A3、共 64 个逻辑设备；不是全局 DP3，也不是把 Prefill 的 DP 改为 6。每台 Prefill 有两个 DP rank，Decode 有四个，均由原生 Motor 管理。

镜像为 `swr.cn-south-1.myhuaweicloud.com/ascendhub/mindie-motor:3.2.0-vllm_ascend0.30.0-a3-openeuler24.03-py3.12`，权重默认路径为 `/mnt/models/GLM-5.3-Flash-w8a8`。在其他环境导入后，应修改为该环境已验证的镜像和权重路径。纯 `vllm-ascend` 镜像不能代替含 Motor 的集成镜像。镜像标签不等于模板 profile 的源码身份，应分别记录镜像 digest、实际 Motor/引擎版本和固定源码合同，参见 [原生集成](../../docs/NATIVE_INTEGRATION.md)。

HCCS 环境使用 `hccs:device` 和资源配置版本 `1.2`；只有确认跨节点 HCCS 拓扑、驱动和通信依赖适配后才应使用。节点 IP、通信网卡、设备映射及实例身份交给原生部署流程处理，不在模板中固化某个节点的值。模型 API 名称为 `glm-5.3-flash`（仅将官方示例的服务别名 `glm` 改为明确的模型名称）；默认上下文上限为 1,048,576 token，实际承载能力需要结合输入长度、并发和 HBM 预检、推理验证确认。

本模板绑定的原生 A3 Docker 启动入口会在引擎加载前逐卡查询 NPU 设备 IP，再生成 Rank Table。部署前，应在每台宿主机对分配的 16 个逻辑设备分别执行只读的 `hccn_tool -i 设备号 -ip -g`，确认返回已规划的 IP 和掩码。宿主机 `bond0` 等通信网卡地址不能代替设备 IP；HCCS 协议变量也不会跳过这个固定版本启动器的前置步骤。设备地址由实际网络规划确定，不能为通过检查而填伪地址或修改原生脚本。此处是当前 Motor 启动器的约束，不是对所有底层 HCCS 场景的通用要求。

创建服务时，分别选择三个 Prefill 实例组和一个 Decode 实例组，各放置到不同宿主机并分配 16 个设备；另外放置一个不分配 NPU 的 Coordinator/Controller 管理角色。模板不保存 Agent ID、节点地址 或布局，保存模板也不会创建或启动模型服务。模板将 Coordinator 推理 API 配置为 `19225`，管理及观测使用 `19226`–`19229`，便于与引擎共置。实际端口可达性须由预检确认。原生 Motor 负责引擎端口和 KV 身份处理；不要把文档演示端口当成平台的固定部署端口。

模板保存、原生参数渲染、镜像软件版本检查和权重分片存在性检查不等同推理验收。首次使用仍须完成原生预检、全部引擎健康、跨 P/D KV 交接、普通响应与流式响应等验证；实际部署前检查 HBM、网络和端口准入。
