# MindIE-Motor Console 统一目录部署示例

本目录提供 `/opt/motor-console` 统一根目录的配置参考，示例域名和摘要必须替换。它不是可直接启动的完整安装包，不包含管理员密码、Agent token、证书私钥或模型材料。常规安装先使用[生产手册](../../PRODUCTION.md)的准备器生成独立身份及私有配置。

| 文件 | 用途与限制 |
| --- | --- |
| [control.env.example](control.env.example) | bundled 兼容控制面的路径、端口与 origin 关系；模块化 API/Web 应按生产手册生成 `compose.modular.yaml` 配置，不能直接套用此 env |
| [docker-agent.env.example](docker-agent.env.example) | 单宿主 Docker Agent 路径与资源上限参考；由 `prepare_agent.py` 生成独立凭据，不复制示例充当初始化 |
| [ingress.env.example](ingress.env.example) | bundled 兼容拓扑的独立 TLS 代理环境；模块化 Web 已包含 TLS 时不重复安装 |
| [motor-npu-collector.service](motor-npu-collector.service) | 将受信快照写入 `/opt/motor-console/npu/observation.json` 的替代 unit，与默认 systemd unit 择一使用 |

Control 只有一个写者。每台参与模型执行的 Docker 宿主通常一个 Agent，控制台所在宿主若也执行模型同样需要自己的 Agent。Control data 与 Agent data 不共享，源码、私有配置、日志、工作区和恢复材料分别保留；已有模型权重仅按经过审查的只读路径引用，不迁移或删除其他项目材料。

镜像使用实际审核过的不可变摘要；API 和 Agent 不使用纯 Web 镜像。TLS 与凭据按[Agent 运维手册](../../AGENT_OPERATIONS.md)准备，Agent 服务端私钥保留在所属节点，只向 Control 提供受信公共 CA。默认只读 Agent 完成资源归属及采样核验后才授权管理写入。

使用本目录采集 unit 时，将固定版本的 `web/backend/host_npu_collector.py` 安装到 `/opt/motor-console/collector`，与[通用 timer](../systemd/motor-npu-collector.timer)配套，并以 `motor-npu-collector.service/timer` 名称安装到 systemd。不要同时启用默认快照路径的另一份采集。已有旧名称 timer 的迁移先核对归属、停旧并等待查询退出，再启新，详见[资源管理](../../RESOURCE_MANAGEMENT.md)。

备份定时器示例在 [deploy/systemd](../systemd/)，解释器、项目/数据路径、远端挂载条件及 `ReadWritePaths` 须一起调整。备份密钥独立保管；Control 应用归档不包含 Agent 账本、模型权重或社区插件包目录，按[备份恢复手册](../../BACKUP_RECOVERY.md)分别保留并演练。

安装后检查 Compose 项目与容器身份、HTTPS、三角色权限、Agent 连接和指标时效；失败时保留配置、日志及原状态，不用重新初始化覆盖既有数据。
