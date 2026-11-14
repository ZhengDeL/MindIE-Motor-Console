# 擎寰平台 0.11.0 部署与升级

0.11.0 是首次正式包含社区插件包导入、受保护 HTTP 扩展和声明式状态页面的版本。平台继续使用独立 Web/API、数据库插件和执行 Agent；原生 Motor 固定适配基线为 `37d615f232a36b8dba8630ffc3a01a20d289c65e`，不随平台版本自动更新。

本文覆盖 Linux Docker 单实例 Control 的新安装，以及同产品 0.10.1/0.10.2 的升级。下列命令在 **Linux Bash、仓库根目录**执行，域名、路径和摘要均须替换。示例仓库名称不代表项目已经提供对应预构建镜像。开发环境另见[本地指南](../README.md)，运行维护见[生产手册](../PRODUCTION.md)。

## 1. 兼容范围与部署前提

| 项目 | 0.11.0 合同 |
| --- | --- |
| 产品身份 | `motor-console`，与可编辑的界面品牌分开 |
| Control | 单实例、单写者，使用独立持久目录；Web 不持有数据库或 Agent 秘密 |
| 数据库 | SQLite 或 PostgreSQL，schema 2；PostgreSQL 不自动提供控制台高可用 |
| Agent | 最低兼容版本 0.10.1，Agent/操作协议 1；产品、回执、账本、准入能力还须逐项通过 |
| 社区插件 | 插件 API 1；实际宿主应报告 `community-import-v1`、`http-routes-v1`、`status-table-v1` |
| 原生 Motor | 固定源码/profile、模型镜像及驱动分别核对，不由平台 0.11.0 号推断原生版本 |
| 观测 | Node/NPU 数据源与 Prometheus/Grafana 独立部署；已有采集器优先复用 |

需要 Linux、Docker Engine、Compose V2、可信镜像来源和目标架构制品。宿主准备/维护工具需要 Python3.11+ 及项目锁定依赖，建议与已验证 API 镜像保持同一环境；SQLite 须通过 WAL-reset 修复版本门禁。Web 证书 SAN 必须覆盖实际访问域名/IP，客户端信任签发 CA。

安装前核对数据盘与 Docker 存储容量/inode、端口、时间、现有服务和管理网络。API 不公开宿主端口；每台 Docker 执行宿主通常一个 Agent，每个 Kubernetes 集群通常一个执行 Agent。不要为平台修改 Docker daemon、重复安装 Exporter 或重新分配已有业务设备。

## 2. 获取固定源码与构建

获取正式标签并保留原生子模块：

```bash
git clone --recurse-submodules --branch v0.11.0 \
  https://github.com/ZhengDeL/MindIE-Motor-Console.git /opt/motor-console/source-0.11.0
cd /opt/motor-console/source-0.11.0
git submodule update --init --recursive
git submodule status mindie-motor/upstream
git rev-parse HEAD
```

原生提交应为上面的固定基线，不切换到原生 master。GitHub 自动源码压缩包及本次 Release 的平台源码 ZIP 均不展开子模块，不能直接当完整离线构建上下文。推荐使用上述递归克隆；离线安装另行准备完整源码、依赖、许可证和校验清单。压缩包没有 `.git`，不能在其中直接使用依赖 Git 索引的离线构建/源码画像工具，`git rev-parse HEAD` 也不能用于取得其来源；来源以随包清单为准。

分别构建 API 与 Web，记录真实源提交、架构、Image ID 和仓库摘要：

```bash
docker build -f web/Dockerfile --target api \
  --build-arg CONSOLE_VERSION=0.11.0 \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  -t registry.example.test/qinghuan/api:0.11.0 .
docker build -f web/Dockerfile --target web \
  --build-arg CONSOLE_VERSION=0.11.0 \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  -t registry.example.test/qinghuan/web:0.11.0 .
```

Dockerfile 前端构建基线为 Node22，API 基线为 Python3.12。目标 ARM64/AMD64 各自构建和验证，不能将本机可用推定为另一架构可用。安装使用审核过的 `image@sha256:...`；可变标签和全零摘要不能作为升级凭据。Agent 使用包含运行时适配的 API 制品，不使用纯 Web 镜像。

## 3. 安全初始化

先准备受信发布者的 Ed25519 公钥，以及只包含 Web `server.crt/server.key` 的私有证书目录。签名私钥留在受控发布环境，应用加密密钥和 bootstrap token 由准备器独立生成；它们不是同一种材料。

对新的空数据目录运行：

```bash
python3 web/scripts/prepare_production.py \
  --project-dir /opt/motor-console/source-0.11.0 \
  --data-dir /opt/motor-console/control-data \
  --agent-rollout-root /opt/motor-console/agents \
  --image 'registry.example.test/qinghuan/api@sha256:<reviewed-api-digest>' \
  --web-image 'registry.example.test/qinghuan/web@sha256:<reviewed-web-digest>' \
  --web-tls-dir /opt/motor-console/tls \
  --origin https://console.example.test:19443 \
  --release-public-key /opt/motor-console/publisher/release.pub \
  --log-dir /opt/motor-console/logs
docker compose --env-file web/.env.production -f web/compose.modular.yaml config --quiet
docker compose --env-file web/.env.production -f web/compose.modular.yaml up -d control web
docker compose --env-file web/.env.production -f web/compose.modular.yaml ps
```

准备器检查不可变镜像标识、TLS 有效期、SAN 和私钥匹配，生成私有环境并复制所需材料；不启动服务、不覆盖既有环境文件或非空数据。默认 Control 数据归 UID/GID10001，Web 为 UID101。保留准备器设置的最小权限，不把目录全局可读作为修复方法。

默认 Web 监听 `0.0.0.0:19443`，浏览器访问实际域名或服务器 IP。`0.0.0.0` 不是访问地址，防火墙按所需管理网段开放。通过严格 HTTPS 的页面使用一次性 bootstrap token 创建首个管理员；**没有通用默认账号和密码**。随后创建独立运维和只读账号，验证真实拒权。初始化 token、私钥和完整 Compose 展开结果不进入日志或仓库。

需要 PostgreSQL 时，先准备专用数据库及受限连接秘密文件，配置 `MOTOR_DATABASE_BACKEND=postgresql`、`MOTOR_DATABASE_URL_FILE`，按[存储手册](../STORAGE.md)验证 CA、服务端身份与访问来源。变更 URL 不会自动迁移已有数据；后端不可用时启动失败，不回退空 SQLite。

## 4. Agent 与模型接入

按[Agent 运维](../AGENT_OPERATIONS.md)或[新节点推送](../AGENT_ROLLOUT.md)准备独立凭据、TLS 和持久账本。推送只安装新节点，不覆盖已有 Agent。默认只读，先核对身份、产品协议、运行时与资源来源，再授权管理写入。

CPU/内存/OS 来自 [Node Exporter](DOCKER_NODE_EXPORTER.md)，NPU 来自[受信宿主快照或 NPU Exporter](DOCKER_NPU_EXPORTER.md)。数据缺失、陈旧或设备映射未知时不能补零或绕过准入。Kubernetes 的监控由集群侧维护；集群调度资源份额不是实时空闲。

模型镜像、所有节点可读的权重、驱动、通信网络和端口分别准备。模板保存不启动模型；审查原生 JSON、角色/节点/设备布局并执行预检，再由 Agent 最终准入。Kubernetes 部署还需集群管理员预置必要组件并按[原生集成说明](NATIVE_INTEGRATION.md)审核权限，不将单命名空间 Role 或平台页面状态视为完整部署能力。

## 5. 社区插件导入与持久化

登录后读取 `GET /api/plugin-sdk` 核对实际 features，再按[插件开发指南](../PLUGIN_DEVELOPMENT.md)构建和导入 `.qhplugin`。HTTP401表示尚未建立登录会话，不用关闭权限解决。导入不执行代码；管理员审阅来源和内容、明确信任并选择后，下次 Control 启动才加载。demo 和 Agent 不加载上传的控制面代码。

当前插件是受信 Python 同进程扩展，不能作为沙箱或绕过宿主权限。社区包提供公开 SDK 内的 HTTP 路由和声明式页面，不能任意替换数据库早期 bootstrap、原生 K8s 执行器或注入前端编译包。启用或回退前检查依赖与兼容范围，停止接收新管理任务并处理未决操作后，才维护重启 Control；不需要因此重建模型。

`MOTOR_PLUGIN_DIR` 默认 `/var/lib/motor-console/plugin-packages`，标准 Compose 位于已有 Control 数据卷。改成外部路径时必须另行配置可写挂载及进程 UID 权限；只改环境变量不会自动增加绑定。**数据库快照和 .mcba 应用归档均不包含此目录**，已使用插件的安装按[独立备份步骤](../BACKUP_RECOVERY.md#社区插件包目录单独备份)保留整个目录与匹配宿主修订。

## 6. 已有 0.10.1/0.10.2 安装升级

1. 核对实际产品身份、源提交、API/Web 镜像、数据库后端/schema、完整 Compose 栈和 Agent 合同。0.11.0 保持 schema2、协议1、最低 Agent0.10.1，不因平台升级强制重启模型或所有 Agent。
2. 等待并审查执行中/结果未明的管理操作；生成一致数据库快照和加密应用归档，验证外部恢复密钥。另留 Agent 状态、TLS、环境、旧镜像及插件目录（如果旧开发安装已使用它）。不能复制运行中的单个 SQLite 文件作为完整备份。
3. 构建并核验目标架构的0.11.0 API/Web 制品。使用真实镜像摘要、数据库兼容范围和受信发布者签名制作清单；结构参考 [release.example.json](../releases/release.example.json)，示例全零摘要不可应用。
4. 在系统维护页审查计划，由[生产维护 CLI](../PRODUCTION.md#升级与失败恢复)按实际 Compose 拓扑应用，仅更新平台组件并保留持久目录。已有安装不重新运行首次初始化，也不生成新的应用密钥覆盖旧密钥。
5. 升级后验证实际0.11.0版本、构建修订、SDK features、三角色、节点连接、日志、备份和原操作回执。此前固定发布的0.10.x没有正式社区包能力；不要预设其数据库包含已启用包。
6. 需要升级 Agent 时作为独立维护项，保留原账本、凭据和工作区，先排空任务再更新；不能用全新 Agent 安装替代原账本交接。

本版本没有因加入社区包而改变控制主库 schema，不能因此省略备份或跨后端验证。跨数据库、跨产品或不兼容 schema 的迁移不是此增量升级流程；失败应停止写入并采用已审核的恢复计划。

## 7. 回退与启动失败

API/Web 回退必须使用同一受审计划，保留原 Compose 文件栈、环境、数据库及加密密钥。对数据库的任何恢复先停止唯一写者，不让旧二进制直接写不兼容 schema。维护回执为未知或 `rollback_failed` 时保留标记与日志，先对账实际状态，不删锁强启。

新增社区包后回退到不具备加载器的版本，不等于包代码已被宿主热卸载。先记录当前加载摘要及下一次选择，按[插件恢复说明](../backend/plugin_host/packages/README.md)检查并保留插件目录，再关闭唯一 Control 并回退；旧宿主忽略或无法加载某个目录不代表插件业务状态已恢复。

插件导致启动失败时，使用**与当前0.11.0宿主匹配**的受控 CLI 检查/恢复选择，保留 `selection-backups` 和包文件，不删除数据库或模型。恢复工具的 `reset-selection` 不是业务数据库回滚，也不移除已经运行进程中的代码；按指南完成显式重启和实际状态核验。无法确认的失败保留停机状态，使用受审恢复材料处理。

## 8. 观测与上线验证

Prometheus/Grafana 与平台 API/Web 独立维护，详见[运行观测手册](../observability/README.md)。升级平台不自动覆盖已有 Exporter、看板、TSDB、网关路由或 CCAE 配置。配置生成、连接成功、真实指标及业务请求是独立验收项。

放行前逐项核对：严格 HTTPS、版本/修订与 SDK、三角色权限、持久化和恢复、Agent与设备身份、采样时效、部署/重启准入、目标 P/D 数量、普通/SSE 推理、日志指标，以及实际启用的外部集成。截图、CLI参数、demo、容器健康和真实模型验收分别留证；本安装说明不宣称这些目标环境已经完成验证。
