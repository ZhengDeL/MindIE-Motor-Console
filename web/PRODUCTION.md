# MindIE-Motor Console 生产运行手册

本手册面向部署和维护人员，适用于单实例控制面。本手册随 0.11.1 源码维护，部署步骤见 [0.11.1 部署与升级](docs/DEPLOYMENT_0.11.1.md)。0.11.0 正式包含社区插件包；仍须核对实际构建修订、SDK features 和发布清单。插件导入、信任选择、启动加载是独立步骤，页面版本号不足以证明包已加载。

下列命令在 Linux Bash 和仓库根目录执行；域名、目录、镜像摘要均为示例。没有通用默认密码，首个管理员通过部署时生成的一次性 bootstrap token 创建。

## 拓扑与运行前提

推荐 `compose.modular.yaml`：HTTPS Web 与 Control API 分容器部署，API 不公开宿主端口；Web 不持有数据库或 Agent 凭据。数据库可选本地 SQLite 或独立 PostgreSQL，始终只有一个控制写者。Docker 每台执行宿主通常一个 Agent；Kubernetes 每个集群通常一个执行 Agent。Agent 不承载客户端正常推理流量。

显示名称统一为 MindIE-Motor Console，已有安装仍应沿用原 Compose 项目名、环境、持久目录和凭据。项目名属于运行资源身份，不因显示名称变化自动迁移；兼容配置中的既有标识及处理方式见[名称与技术身份](docs/BRANDING.md#显示品牌与技术身份)。维护时记录实际 Compose 项目名，并对同一文件栈进行检查、升级和回退。

需要 Linux、Docker Engine、Compose V2、可信镜像来源及覆盖浏览器实际地址的 HTTPS 证书。准备工具和维护 CLI 使用 Python 3.11+ 及项目锁定依赖，建议与所用 API 镜像保持相同环境。SQLite 必须通过代码中的 WAL-reset 修复版本门禁；升级 Python 本身不能替代检查其链接的 SQLite。

Control 使用非 root UID、只读根文件系统和有界临时目录；不挂 Docker socket、宿主根目录或 kubeconfig。平台与 Agent 使用 `runc`，原生模型容器继续使用 Motor 配置的运行时。不得为平台重置 Docker daemon 或修改现有模型网络。

SQLite 必须使用可靠本地持久存储，不能放在共享文件系统由多个实例写入。PostgreSQL 也使用单写者租约，选用它不等于获得高可用。进程重启会将未完成任务标记为中断，不自动重放主机操作；不能删除租约或数据库绕过未决状态。

## 构建固定版本

下方命令获取 `v0.11.1` 正式发行源码。构建时记录实际源提交与制品摘要，不能把本地构建参数当作发布凭据。

```bash
git clone --recurse-submodules --branch v0.11.1 \
  https://github.com/ZhengDeL/MindIE-Motor-Console.git /opt/motor-console/source
cd /opt/motor-console/source
git submodule update --init --recursive
git submodule status mindie-motor/upstream
docker build -f web/Dockerfile --target api \
  --build-arg CONSOLE_VERSION=0.11.1 --build-arg VCS_REF="$(git rev-parse HEAD)" \
  -t registry.example.test/motor-console/api:0.11.1 .
docker build -f web/Dockerfile --target web \
  --build-arg CONSOLE_VERSION=0.11.1 --build-arg VCS_REF="$(git rev-parse HEAD)" \
  -t registry.example.test/motor-console/web:0.11.1 .
```

原生提交应为 `37d615f232a36b8dba8630ffc3a01a20d289c65e`。不要在安装时跟随原生 master；GitHub 自动源码归档不含子模块内容，离线构建必须另备完整固定源码。分别为目标架构构建、验证并记录真实 Image ID 和仓库摘要。示例标签不表示项目提供对应预构建镜像。

安装使用审核过的 `registry/name@sha256:<64位真实摘要>`。可按所选 Dockerfile 配置构建源及 kubectl 版本，不把凭据写入 build arg，不关闭 TLS 或哈希检查。Docker-only 制品可以不含 kubectl；启用 Kubernetes 的 Agent 必须包含受支持 kubectl。

## 初始化与启动

先确认数据盘和 Docker 存储的可用容量、inode、架构、时间、端口及管理网络来源。容量检查使用容器用户可用空间，不能以 root 保留空间代替。日志、镜像、模型权重和恢复材料分别预留容量。

准备受信发布者的 Ed25519 公钥 PEM，以及包含 `server.crt`、`server.key` 的私有 TLS 目录。自行发布时按[部署指南](docs/DEPLOYMENT_0.11.1.md#3-安全初始化)在受控离线环境生成独立发布密钥，仅传递公钥。证书 SAN、访问端口与 origin 必须匹配；签名私钥留在受控发布环境。对新独立空目录运行：

```bash
python3 web/scripts/prepare_production.py \
  --project-dir /opt/motor-console/source \
  --data-dir /opt/motor-console/control-data \
  --agent-rollout-root /opt/motor-console/agents \
  --image 'registry.example.test/motor-console/api@sha256:<reviewed-api-digest>' \
  --web-image 'registry.example.test/motor-console/web@sha256:<reviewed-web-digest>' \
  --web-tls-dir /opt/motor-console/tls \
  --origin https://console.example.test:19443 \
  --release-public-key /opt/motor-console/publisher/release.pub \
  --log-dir /opt/motor-console/logs
docker compose --env-file web/.env.production -f web/compose.modular.yaml config --quiet
docker compose --env-file web/.env.production -f web/compose.modular.yaml up -d control web
docker compose --env-file web/.env.production -f web/compose.modular.yaml ps
```

准备器检查镜像、证书、私钥匹配、有效期和 SAN；生成私有环境、应用加密密钥和 bootstrap token，并复制 Web 配置。它不启动容器，不覆盖已有环境或非空数据目录。root 初始化后默认数据归 UID/GID `10001:10001`；保持生成器设置的权限。完整 Compose 展开结果、token 和私钥不得上传工单或仓库。

默认 Web 监听 `0.0.0.0:19443`，浏览器访问实际域名或服务器 IP，如 `https://console.example.test:19443`。`0.0.0.0` 不是访问地址。按所需网段配置防火墙或 `MOTOR_WEB_BIND_IP`，向客户端分发受信 CA；不以关闭证书检查或 Secure Cookie 作为访问方案。

严格 HTTPS 下检查 `/health`、`/ready`、实际版本和账号登录，再创建独立运维、只读账号并验证拒权。健康接口只反映组件状态，不证明 Agent、NPU 或模型可用。

## bundled 兼容拓扑与 host 网络

`compose.production.yaml` 提供 API 加静态页面的兼容拓扑，默认仅公开 `127.0.0.1:19080`。准备时不传 `--web-image/--web-tls-dir`，使用 `bundled` 镜像，并按[独立 HTTPS 入口](INGRESS.md)配置代理。它与模块化 Web 是两种选择，不在同一端口同时启动。

仅兼容拓扑支持 `compose.production.host-network.yaml`。Compose **2.24.4+** 才能处理其中的 `!reset`；确有需要时在私有环境设置 `MOTOR_CONTROL_NETWORK_MODE=host`、`MOTOR_CONTROL_BIND_IP=127.0.0.1`、`MOTOR_CONTROL_PORT=19080`，在所有操作中保留完整栈：

```bash
docker compose --env-file web/.env.production \
  -f web/compose.production.yaml -f web/compose.production.host-network.yaml config --quiet
docker compose --env-file web/.env.production \
  -f web/compose.production.yaml -f web/compose.production.host-network.yaml up -d control
```

不能将此 overlay 叠加到 `compose.modular.yaml`，也不能只改环境标记而省略 overlay。维护工具核对实际拓扑与组合摘要；端口冲突时调整本项目配置，不停止未知进程。

## 账号、日志与日常维护

日常不共用管理员。角色由后端校验，隐藏按钮不能代替权限控制。可选 OIDC 须独立配置 issuer、client、端点信任与角色映射，并验证身份源故障、到期和撤权。

Control 默认上限为 `2 CPU / 1 GiB / 256 PID`，Docker Agent 为 `1 CPU / 1 GiB / 256 PID`；上限不是实测开销。模块化 Compose 将 API 结构化日志写到 `/var/log/motor-console`，`MOTOR_LOG_HOST_DIR` 指定宿主目录；`MOTOR_LOG_SEGMENT_BYTES` 和 `MOTOR_LOG_ROTATIONS` 控制轮转。改变环境或挂载后按原拓扑维护重建组件，单独 `docker restart` 不加载新环境。

Docker 输出、应用日志、数据库审计、Agent 账本和原生日志有不同保留策略，分别规划容量和归档。详见[存储](STORAGE.md)、[Agent 运维](AGENT_OPERATIONS.md)、[资源采集](RESOURCE_MANAGEMENT.md)和[备份恢复](BACKUP_RECOVERY.md)。数据库快照不能代替 Agent 状态或社区插件包目录的备份。

使用文档入口默认隐藏；有专用站点时设置 `MOTOR_DOCUMENTATION_URL=https://docs.example.test/platform/`。只接受完整、无凭据的 HTTP/HTTPS URL。修改后按原 Compose 栈仅重建 Control 并刷新页面，不需要重建前端、Agent 或模型。

## 升级与失败恢复

先确认没有执行中或结果未明的操作，验证一致备份及密钥，保留旧 API/Web 镜像与完整配置。清单采用 `release_schema: 1`，签名绑定 `product_id=motor-console`。以[结构示例](releases/release.example.json)填写真实摘要、协议和数据库兼容范围，零摘要不能应用。

在受控发布环境签名，只将清单和签名交付到 Control 的 releases 目录：

```bash
python3 web/scripts/sign_release.py --manifest /secure/motor-console-release/release.json \
  --private-key /secure/motor-console-release/publisher.key
```

系统维护页审查计划后，在宿主机受控环境执行：

```bash
python3 web/scripts/console_admin.py apply --project-dir /opt/motor-console/source \
  --data-dir /opt/motor-console/control-data --plan-id '<reviewed-plan-id>'
```

工具验证产品、签名、镜像、Compose 身份并记录日志。失败按计划回滚；显式回滚使用相同参数将 `apply` 改为 `rollback`。`rollback_failed` 时保留标记和日志，停止新维护动作，核对实际镜像、数据库及回执，不能删锁强启。维护期间不改 Compose/env，不并行运行第二个工具。

Agent 与平台独立升级，保留本 Agent 的 data、凭据和工作区，先排空管理任务。更新 Agent 不自动重建模型，但中断的管理操作仍需按操作 ID 对账。社区插件由[插件宿主说明](backend/plugin_host/README.md)约束；数据库驱动不是网页导入的社区包。

## 上线检查

按实际启用功能逐项保留结果：严格 HTTPS、三角色权限、节点产品协议、设备映射及数据过期处理、部署和重启准入、普通/SSE 推理、备份恢复演练、升级回滚。截图、静态检查、容器健康和真实请求分别记录；未执行的环境检查保持未验证，不能由文档存在推定完成。
