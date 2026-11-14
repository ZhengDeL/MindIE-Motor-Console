# 擎寰平台 Agent 安装、凭据与任务维护

本手册对应 0.11.0 控制面的 `motor-console` 执行 Agent；最低 Agent 兼容版本仍为 0.10.1，实际还需通过协议和能力检查。新安装、凭据轮换、任务恢复和配置编辑是不同操作；每项变更前先核对 Agent 身份、实际运行目录及未决任务。Agent 镜像版本与平台可独立升级，但必须符合产品、协议和最低版本要求。

Console 与 Agent 独立发布，默认使用固定的原生 Motor profile；调用原生部署与配置入口，保留任意原生 JSON 字段。Control 数据库可选 SQLite/PostgreSQL，仍只允许一个控制写者；Agent 操作账本单独持久化于本地 SQLite。不要共享 Agent 数据目录，也不要把 SQLite 文件放到网络文件系统。选用 PostgreSQL 不会自动提供任务高可用，见[存储说明](STORAGE.md)。

## 安装数量和职责

| 运行环境 | 建议数量 | 所需连接 | 权限边界 |
| --- | --- | --- | --- |
| Docker | 每台参与执行的宿主机 1 个 | 本机 Docker socket、固定工作区 | Docker socket 等同高权限执行边界；仅接入受信任管理网络 |
| Kubernetes | 每个集群 1 个执行 Agent | 该集群专用 kubeconfig | 不需每个 worker 安装；通过 API 查询所有节点，不挂 Docker socket 或宿主根目录 |
| Console | 1 个控制面 | 各 Agent 的受信任地址 | 不挂宿主 Docker socket，不接收任意远程 shell 命令 |

管理员可在「节点管理 → 推送 Agent」通过已核对指纹的 SSH 安装新 Agent，也可使用配置管理或下列命令手工准备。推送器复用目标机已有 Docker/Compose，提供固定摘要镜像与离线包两种方式，仅创建新安装；已有 Agent 不会被覆盖或重启。SSH 凭据仅保留在当前安装进程内存中，不进入数据库。操作流程、离线镜像打包和中断边界见 [Agent 推送说明](AGENT_ROLLOUT.md)。防火墙和集群授权仍由运维维护。不要把 SSH 密码、kubeconfig 或令牌写入仓库。

## 新节点准备

在 Linux 目标主机的仓库根目录，使用安装了项目依赖的受控 Python 环境执行。先把示例镜像替换成实际审核过的 digest。数据目录、环境文件位于项目目录之外；已有目录和文件不会被覆盖。

```bash
python3 web/scripts/prepare_agent.py \
  --runtime docker --project-dir /opt/motor-console-project \
  --data-dir /var/lib/motor-console-docker-agent \
  --env-file /etc/motor-console/docker-agent.env \
  --workspace-dir /var/lib/motor-console-workspace \
  --image 'registry.example.test/motor-console@sha256:<64位真实摘要>'
```

Kubernetes 执行宿主把 `--runtime` 改为 `kubernetes`，使用 `--kubeconfig /etc/motor-console/cluster.kubeconfig` 替代 `--workspace-dir`。kubeconfig 须为私有、flatten/minify 后的静态配置，使用 HTTPS 与可信 CA；准备器拒绝不验证服务器、外部文件引用和可执行凭据插件。准备器只做本地检查，不连接集群，也不授予 RBAC。

手工准备默认监听 `127.0.0.1:19082`，可用可信 HTTPS 入口转发到回环地址。推送器默认在指定管理 IP 启用 Agent 自身 HTTPS，为每个 Agent 生成独立 CA 与服务端密钥，私钥仅保留在目标机；Console 保存该节点的公共 CA，并校验证书链与 IP。手工配置直接 TLS 使用 `configure_agent_tls.py`；已有 HTTPS 代理模式需要管理员明确确认。准备器的 `--https-proxy-confirmed` 本身只是声明，必须在启动前完成实际 HTTPS 配置。生产 Console 拒绝向远程 HTTP 地址发送令牌；HTTP 仅允许明确的本机回环 IP。两种方式都应通过管理网络和防火墙限制来源。

准备结果会给出完整 `docker compose --env-file ... -f ... config/up/ps` 命令，先使用 `config --quiet` 校验，再在受控位置审查需要展开的配置。不要把含凭据的完整配置输出上传工单。默认 Agent 限制为 1 CPU、1 GiB 内存、256 PID，昂贵原生生成任务的并发为 1；可通过 `MOTOR_AGENT_CPUS`、`MOTOR_AGENT_MEMORY_LIMIT`、`MOTOR_AGENT_PIDS_LIMIT`、`MOTOR_AGENT_NATIVE_CONCURRENCY` 调整，原生并发只允许 1..4。并发已满返回 429 和 Retry-After，不在服务锁前无限排队。限制只针对 Agent，不改变 Motor 工作负载的 NPU、CPU 或内存配置；须在目标模型和规模下单独验证资源预算。

准备器默认只读，`MOTOR_AGENT_READ_ONLY=true` 时不能部署模型。先完成 HTTPS、协议、观测与资源归属核验，再由管理员显式授权写入并仅重建该 Agent。主机 CPU/内存/OS 由 [Node Exporter](docs/DOCKER_NODE_EXPORTER.md)提供；NPU 使用[受信快照或 NPU Exporter](docs/DOCKER_NPU_EXPORTER.md)。Agent 在线不代表指标完整，更不代表设备已经获准分配。

## 模型部署 IP 与通信网卡

节点配置中的通信网卡留空表示自动选择。Console 采集资源时通过可选的 `node_address` 查询参数，将该节点登记的模型部署 IPv4 传给 Agent；Agent 使用原生 IP/网卡查询函数，仅在本机找到唯一精确匹配时上报接口名称。模型部署 IP 与 Agent 的 HTTPS 监听地址分别配置，不会把通配监听地址当成模型地址，也不会猜测使用 `bond0` 等固定名称。

自动匹配只读取本机接口信息，复用完整资源缓存，不重新采集 Docker/NPU、不刷新旧资源的有效期。人工指定的网卡优先于上报值；缺失或非法的有效网卡会在资源建议中按节点排除，最终部署仍保留原生 IP/网卡匹配检查。可在“节点管理 → 查看配置”检查登记和上报结果，修改后重新探测。

直接调用 Agent 而未提供 `node_address` 时保留原有 `MOTOR_NODE_ADDRESS` / `MOTOR_NODE_NIC` 环境配置行为，Docker Agent Compose 显式透传这两个可选设置。旧 Agent 可忽略查询参数；需要自动匹配时应更新 Agent，或经核实后在节点配置中明确指定网卡。

## 独立 TLS 信任和可选 mTLS

Console 的 `MOTOR_AGENT_CA_BUNDLE` 设置 Agent 服务端 CA 路径，默认使用受信任公共 CA。可选的 `MOTOR_AGENT_CLIENT_CERT_FILE`、`MOTOR_AGENT_CLIENT_KEY_FILE`、`MOTOR_AGENT_CLIENT_KEY_PASSWORD_FILE` 提供客户端身份；`MOTOR_AGENT_MTLS_REQUIRED=true` 强制 HTTPS 和客户端证书配置。文件须挂入 Console 可读取的私有目录，私钥只允许拥有者访问。Agent 反向代理还必须真正验证客户端证书并保护后端。客户端发送证书本身不能证明服务端要求 mTLS。

这些设置与原生 Motor API 的 `MOTOR_CLIENT_*` 完全独立。证书链和主机名始终校验；不跟随重定向，也不读取代理环境变量。证书和 CA 文件替换后会重新加载，部署时应使用目录挂载保证原子替换可见，并验证新旧证书过渡。

推送器生成的服务端证书默认有效期 365 天，不提供静默自动续期。到期前通过受信任 SSH 维护证书，再在节点凭据更新接口提交新的公共 `agent_ca_pem`；接口先用新信任验证连接，再原子替换原记录，保留节点 ID。省略该字段保留现有信任，空字符串明确恢复部署级 CA。公共 CA 摘要与到期时间通过节点接口返回。独立 CA HTTPS 加 Bearer 凭据不等同于双向 TLS；需要双向 TLS 时仍应配置真正验证客户端身份的代理。

## 凭据迁移、轮换和撤销

新准备器生成私有 `credentials/agent-token.json`，内容为凭据策略 JSON，Agent **只读挂载整个专用凭据目录**。文件权限 0600、目录 0700，属主为 root 或进程 UID；不允许链接、宽松目录权限或无关材料。不要仅挂载该文件：文件原子替换后，单文件 bind 可能仍指向旧 inode。

只轮换已经核对归属的本产品 Agent 凭据，不跨安装复制 token、credential ring 或 CA 私钥。新安装生成新身份，已有安装轮换保留原节点与账本。凭据目录整体只读挂载在 `/run/motor-agent-credentials`，文件名为 `agent-token.json`。

轮换时在 **Agent 宿主机的仓库根目录**、已安装项目依赖的受控 Python 环境执行以下命令。镜像内模块入口为 `backend.agent_credentials`，源码入口为下面的 `web.backend.agent_credentials`，不要混用工作目录。CLI 不输出令牌，`export` 只独占写入新的私有文件；先准备受限的输出目录，使用受控渠道在 Console「节点管理 → 更新凭据」输入新令牌，然后逐节点探测，最后撤销旧令牌。

```bash
python3 -m web.backend.agent_credentials rotate --file /var/lib/motor-console-docker-agent/credentials/agent-token.json --grace-seconds 900
python3 -m web.backend.agent_credentials export --file /var/lib/motor-console-docker-agent/credentials/agent-token.json --output /secure/motor-console/new-token.transfer
# Console 校验并保存新令牌后：
python3 -m web.backend.agent_credentials revoke --file /var/lib/motor-console-docker-agent/credentials/agent-token.json --previous-only
python3 -m web.backend.agent_credentials status --file /var/lib/motor-console-docker-agent/credentials/agent-token.json
```

current 与 previous 最多允许 24 小时重叠，期限到达即拒绝旧令牌；文件配置损坏时拒绝访问。全量 `revoke` 会立即停用两代身份；重新启用必须显式 `rotate --enable`，且不恢复旧令牌。CLI 互斥与原子写入避免半个文件被读取；运行中请求不会因令牌撤销被自动取消。转移文件用完后按凭据管理要求处理。

## 中断任务与资源归属

进程重启把未完成任务标为中断，保留可能存在的资源记录，**不会自动重放部署**。管理员或运维在任务窗口执行「资源对账」，仅查询真实受管资源；Docker 检查服务/节点归属标签，Kubernetes 保留已记录 UID 的所有权校验。每页最多 16 个执行节点，支持继续分页。Agent 不可达显示未知，不把查询失败判为空资源。

对账只保存观测证据，原任务状态保持不变，不更改原生 JSON、启停容器或调整副本，也不判定推理验收通过。配置或任务并发变化时拒绝旧结果。根据对账及日志，通过正常的停止、清理、重建流程恢复，重新部署前仍须经过原生预检和审批。

## Kubernetes 权限

原生 Infer Operator 26.2 要求 InferServiceSet 的集合 `spec.replicas` 至少为 1。平台停止该类服务时保存 UID 绑定的原始角色基线，将各角色外层 `spec.template.roles[i].replicas` 缩为 0；保留内层工作负载模板 `roles[i].spec.replicas`。这两层副本数含义不同，不能用集合缩零或仅改内层模板冒充已停止。写入使用 UID、resourceVersion 和原角色内容前提，控制台等待实际 Pod 退场后才显示已停止。重新部署仍使用已审查的原生生成配置；通用 Deployment/StatefulSet 的停机合同不变。

停止请求已受理但尚未收敛时，保留服务、基线及回执。没有同服务执行中的任务后，可以显式选择“继续停止”，核对并确认后提交新的停止意图；不会自动重放旧的未知结果。InferServiceSet 不套用 Deployment 的滚动重启注解，使用已支持的停止、预检和部署路径。卸载按已记录 UID 等待前台删除收敛，命名空间和 PVC 保留。

固定版原生 `controller_template.yaml` 和 `infer_service_template.yaml` 会创建 ClusterRole/ClusterRoleBinding，运行时 Controller 需要读取或监听 Node。当前适配器用 create/replace 管理归属资源，执行身份因而还需要对应 update 权限。直接删除集群角色写权限会影响这些原生部署路径；只读查看节点和执行部署所需的权限不同。

建议使用独立集群执行身份和受限管理网络，逐项审核真实授权及准入策略。现有适配器不接管预先创建但未登记 UID 的资源；仅由管理员预创建角色不能替代资源归属交接。进一步收敛权限需要明确管理员初始化边界、归属交接和准入约束，并验证原生各部署及故障恢复路径后再切换。Kubernetes 对角色创建和绑定还有防提权校验，不能只凭 `can-i create clusterroles` 判断任意提权，详见 [RBAC 官方说明](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)。

停止和手动扩缩容会读取目标命名空间的 `horizontalpodautoscalers.autoscaling`，以避免与自动扩缩容控制器冲突。即使当前没有 HPA，也需要该命名空间的 `get/list` 权限；实际存在受管 HPA 时，再按启停合同审核必要的修改权限。部署成功不能代替停止、缩放、日志和资源回收的逐项权限验证。读取 HPA 不需要扩大到其他命名空间，也不需要 `cluster-admin`、`bind`、`escalate` 或 Secrets 读取权限。

对集群角色的 `get/update/delete` 可按已审核的原生名称限制；`create` 无法通过 `resourceNames` 限制新对象名称，需要结合 Kubernetes 防提权校验及部署方准入策略审核。CRD/Operator 初始化与每个原生服务专属 RBAC 的创建是两件事；当前适配器不能仅靠命名空间 Role 完成所有固定原生部署路径。

加密恢复包与定时备份见 [备份恢复说明](BACKUP_RECOVERY.md)。Agent 数据库、凭据和工作区须另外保留，Control 数据库快照不能代替 Agent 状态。故障恢复时先按原操作 ID 对账，不通过重新推送空 Agent 覆盖原有账本。


## 已接入 Agent 的详情与配置

从节点管理点击 Agent 名称或“详情 / 配置”，分别查看登记信息、连接信息和最近上报的运行信息。管理员可以修改名称与登记标签；运维操作员、只读用户查看详情。令牌与私有 CA 内容不在详情接口中返回。

名称修改只更新平台登记，不改变节点 ID、既有容器名称、服务配置或模型进程；服务列表按节点 ID 实时解析最新名称。同一宿主的管理入口、令牌与证书更新不承载线上推理流量。正常业务走客户端、网关/Coordinator 与引擎；K8S 内置“推理验证”由执行 Agent 代发测试请求。

Agent 管理 URL 与模型部署 IP/通信网卡分别展示。IP 与网卡是后续 Motor 部署及访问所用的登记值，不会修改宿主网卡或自动重配容器；存在受管资源、未决分配或执行中任务时暂不可改，以保持后续运维目标一致。名称、标签和原 URL 的凭据轮换仍可使用。

人工指定网卡优先于 Agent 上报网卡；清空人工配置后使用上报值。详情分别显示人工配置、上报和生效值，后台采样不会覆盖人工选择。没有可靠上报时保持未知，部署仍需通过原生预检。

每次登记信息编辑使用独立 configuration_revision 校验，凭据另用 credential_revision。遇到 409 时保留草稿，读取最新配置并核对后再提交；网络超时先读回确认，不自动重复保存。

连接凭据表单支持保持现有 CA 信任、替换为新的公开 CA PEM，或清除节点专用 CA、改用部署默认信任。默认保持不变；表单不回显原 CA 内容，不能上传证书私钥。新连接通过 TLS、令牌和协议校验后才保存；不会修改宿主机的证书文件。

更换管理 URL 时先验证新连接。Docker 仍有关联服务时需证明同宿主、同 Docker daemon；存在执行中任务或 pending/uncertain 分配时保留原 URL，先完成任务或对账。已有 K8S 受管资源的 URL 迁移在当前协议下缺少完整集群与执行状态身份，明确拒绝；这不限制原 URL 的令牌/CA 轮换，也不停止推理。跨宿主、跨 Agent 执行状态的迁移需要单独规划。

Agent 推送任务重新接入已有节点时保留管理员已设置的名称、标签与部署网络，更新已验证的连接；新节点初始化独立配置修订。重接入后重新采集硬件状态，旧值作为历史保留。
