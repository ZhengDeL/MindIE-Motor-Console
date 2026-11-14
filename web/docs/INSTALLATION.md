# 安装与升级

MindIE-Motor Console 由独立 Web/API、数据库插件和执行 Agent 组成。Web 不持有节点执行权限；原生 Motor 使用固定源码画像，不随控制台更新自动变更。

本指南用于 Linux Docker 单实例 Control 的安装和组件更新。命令在 **Linux Bash、仓库根目录**执行，域名、路径和摘要须替换为实际值。示例镜像地址不是预构建镜像下载源。本地体验见[开发指南](../README.md)，运行维护见[生产手册](../PRODUCTION.md)。

源码获取方式及当前版本见[项目首页](../../README.md#获取源码)。取得源码后，按目标架构构建镜像，再执行安装；已有部署不会自动更新。

## 1. 兼容范围与部署前提

| 项目 | 要求 |
| --- | --- |
| 产品身份 | `motor-console`，与可编辑的界面品牌分开 |
| Control | 单实例、单写者，使用独立持久目录；Web 不持有数据库或 Agent 秘密 |
| 数据库 | SQLite 或 PostgreSQL，schema 2；PostgreSQL 不自动提供控制台高可用 |
| Agent | 最低兼容版本 0.10.1，Agent/操作协议 1；产品、回执、账本、准入能力还须逐项通过 |
| 社区插件 | 插件 API 1；实际宿主应报告 `community-import-v1`、`http-routes-v1`、`status-table-v1` |
| 原生 Motor | 内置源码固定为 `37d615f232a36b8dba8630ffc3a01a20d289c65e`；模型镜像与驱动版本单独核对 |
| 观测 | Node/NPU 数据源与 Prometheus/Grafana 独立部署；已有采集器优先复用 |

需要 Linux、Docker Engine、Compose V2、可信镜像来源和目标架构制品。宿主准备/维护工具需要 Python3.11+ 及项目锁定依赖，建议与已验证 API 镜像保持同一环境；SQLite 须通过 WAL-reset 修复版本门禁。Web 证书 SAN 必须覆盖实际访问域名/IP，客户端信任签发 CA。

安装前核对数据盘与 Docker 存储容量/inode、端口、时间、现有服务和管理网络。API 不公开宿主端口；每台 Docker 执行宿主通常一个 Agent，每个 Kubernetes 集群通常一个执行 Agent。不要为平台修改 Docker daemon、重复安装 Exporter 或重新分配已有业务设备。

## 2. 构建镜像

按[项目首页](../../README.md#获取源码)取得源码后，在仓库根目录确认源提交与固定子模块：

```bash
git submodule status mindie-motor/upstream
git rev-parse HEAD
```

原生提交应与上表一致。GitHub 自动源码压缩包不展开子模块，也不包含 `.git`；依赖 Git 索引的构建或画像工具应使用递归克隆的源码。离线安装需同时准备固定子模块、依赖制品、许可证和校验清单。

分别构建 API 与 Web，记录真实源提交、架构、Image ID 和仓库摘要：

```bash
console_version=$(python3 -c 'from web.backend.version import CONSOLE_VERSION; print(CONSOLE_VERSION)')
docker build -f web/Dockerfile --target api \
  --build-arg "CONSOLE_VERSION=$console_version" \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  -t "registry.example.test/motor-console/api:$console_version" .
docker build -f web/Dockerfile --target web \
  --build-arg "CONSOLE_VERSION=$console_version" \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  -t "registry.example.test/motor-console/web:$console_version" .
```

Dockerfile 前端构建基线为 Node22，API 基线为 Python3.12。目标 ARM64/AMD64 各自构建和验证，不能将本机可用推定为另一架构可用。安装使用审核过的 `image@sha256:...`；可变标签和全零摘要不能作为升级凭据。Agent 使用包含运行时适配的 API 制品，不使用纯 Web 镜像。

## 3. 安全初始化

先准备受信发布者的 Ed25519 公钥，以及只包含 Web `server.crt/server.key` 的私有证书目录。签名私钥留在受控发布环境，应用加密密钥和 bootstrap token 由准备器独立生成；它们不是同一种材料。

自行构建与发布时，可在**受控离线发布环境**生成专用签名密钥。下面在已有、受保护的 `/secure` 下创建全新目录；不要在 Control 主机、平台源码或数据目录执行，也不要覆盖已有发布密钥。使用已安装项目锁定依赖的 Python：

```bash
umask 077
python3 - /secure/motor-console-release <<'PY'
from pathlib import Path
import os
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

directory = Path(sys.argv[1])
if not directory.is_absolute() or any(p.is_symlink() for p in directory.parents):
    raise SystemExit("Use a new absolute directory with trusted, non-symlink parents")
directory.mkdir(mode=0o700, exist_ok=False)
key = Ed25519PrivateKey.generate()
with (directory / "publisher.key").open("xb") as stream:
    stream.write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
os.chmod(directory / "publisher.key", 0o600)
with (directory / "release.pub").open("xb") as stream:
    stream.write(key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
PY
```

仅将 `release.pub` 经受信通道复制到控制端的 `/opt/motor-console/publisher/release.pub`，并由部署方核对其摘要；`publisher.key` 保留在离线发布环境，用于[签名升级清单](../PRODUCTION.md#升级与失败恢复)。目录已存在或生成中断时先人工核对，不删除后重跑。私钥未加密存储，因此必须由发布环境的文件权限、离线保管和加密备份保护；它不是 TLS 私钥、应用加密密钥或备份恢复密钥。

对新的空数据目录运行：

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

准备器检查不可变镜像标识、TLS 有效期、SAN 和私钥匹配，生成私有环境并复制所需材料；不启动服务、不覆盖既有环境文件或非空数据。默认 Control 数据归 UID/GID10001，Web 为 UID101。保留准备器设置的最小权限，不把目录全局可读作为修复方法。

默认 Web 监听 `0.0.0.0:19443`，浏览器访问实际域名或服务器 IP。`0.0.0.0` 不是访问地址，防火墙按所需管理网段开放。通过严格 HTTPS 的页面使用一次性 bootstrap token 创建首个管理员；**没有通用默认账号和密码**。随后创建独立运维和只读账号，验证真实拒权。初始化 token、私钥和完整 Compose 展开结果不进入日志或仓库。

默认页面名称为 MindIE-Motor Console。升级不会覆盖已有安装保存的自定义品牌；需要使用新默认名称时，在“系统设置 → 平台品牌”恢复默认并保存。显示名称与 Compose 项目名、数据目录和数据库身份分别维护，见[品牌与技术身份](BRANDING.md#显示品牌与技术身份)。

需要 PostgreSQL 时，先准备专用数据库及受限连接秘密文件，配置 `MOTOR_DATABASE_BACKEND=postgresql`、`MOTOR_DATABASE_URL_FILE`，按[存储手册](../STORAGE.md)验证 CA、服务端身份与访问来源。变更 URL 不会自动迁移已有数据；后端不可用时启动失败，不回退空 SQLite。

## 4. Agent 与模型接入

按[Agent 运维](../AGENT_OPERATIONS.md)或[新节点推送](../AGENT_ROLLOUT.md)准备独立凭据、TLS 和持久账本。推送只安装新节点，不覆盖已有 Agent。默认只读，先核对身份、产品协议、运行时与资源来源，再授权管理写入。

CPU/内存/OS 来自 [Node Exporter](DOCKER_NODE_EXPORTER.md)，NPU 来自[受信宿主快照或 NPU Exporter](DOCKER_NPU_EXPORTER.md)。数据缺失、陈旧或设备映射未知时不能补零或绕过准入。Kubernetes 的监控由集群侧维护；集群调度资源份额不是实时空闲。

模型镜像、所有节点可读的权重、驱动、通信网络和端口分别准备。模板保存不启动模型；审查原生 JSON、角色/节点/设备布局并执行预检，再由 Agent 最终准入。Kubernetes 部署还需集群管理员预置必要组件并按[原生集成说明](NATIVE_INTEGRATION.md)审核权限，不将单命名空间 Role 或平台页面状态视为完整部署能力。

## 5. 社区插件导入与持久化

登录后读取 `GET /api/plugin-sdk` 核对实际 features，再按[插件开发指南](../PLUGIN_DEVELOPMENT.md)构建和导入 `.qhplugin`。HTTP401表示尚未建立登录会话，不用关闭权限解决。导入不执行代码；管理员审阅来源和内容、明确信任并选择后，下次 Control 启动才加载。demo 和 Agent 不加载上传的控制面代码。

当前插件是受信 Python 同进程扩展，不能作为沙箱或绕过宿主权限。社区包提供公开 SDK 内的 HTTP 路由和声明式页面，不能任意替换数据库早期 bootstrap、原生 K8s 执行器或注入前端编译包。启用或回退前检查依赖与兼容范围，停止接收新管理任务并处理未决操作后，才维护重启 Control；不需要因此重建模型。

`MOTOR_PLUGIN_DIR` 默认 `/var/lib/motor-console/plugin-packages`，标准 Compose 位于已有 Control 数据卷。改成外部路径时必须另行配置可写挂载及进程 UID 权限；只改环境变量不会自动增加绑定。**数据库快照和 .mcba 应用归档均不包含此目录**，已使用插件的安装按[独立备份步骤](../BACKUP_RECOVERY.md#社区插件包目录单独备份)保留整个目录与匹配宿主修订。

## 6. 更新已有安装

更新前确认原安装的来源、产品身份、数据库后端/schema、备份格式和部署布局均在目标版本的兼容范围内。版本号相近或显示名称相同不能证明可原地更新。其他产品、来源不明的安装及跨数据库迁移，需要单独的数据迁移和回退方案，不使用以下增量流程。

1. 核对源提交、API/Web 镜像、数据库后端/schema、完整 Compose 栈和 Agent 合同。当前 schema 为 2、管理/Agent 协议为 1、最低 Agent 版本为 0.10.1。控制台更新与 Agent、模型分别维护。
   同时记录实际 Compose 项目名、原数据和日志挂载、进程 UID/GID、监控看板 UID 与预计算序列名；更名时沿用这些值，不因显示名称变化另起项目或迁移数据。已有配置中的兼容标识不要求批量改写。
2. 处理执行中及结果未明的管理操作；生成一致数据库快照和加密应用归档，验证恢复密钥。另留 Agent 状态、TLS、环境、旧镜像及使用中的插件目录。不能复制运行中的单个 SQLite 文件作为完整备份。
3. 构建并核验目标架构的 API/Web 制品。使用真实镜像摘要、数据库兼容范围和受信发布者签名制作清单；结构参考 [release.example.json](../releases/release.example.json)，全零占位摘要会被拒绝。
4. 在系统维护页审查计划，由[生产维护 CLI](../PRODUCTION.md#升级与失败恢复)按实际 Compose 拓扑应用，仅更新平台组件并保留持久目录。已有安装不重新运行首次初始化，也不生成新的应用密钥覆盖旧密钥。
5. 验证实际版本、构建修订、SDK features、三角色、节点连接、日志、备份和原操作回执；使用社区插件时核对包摘要、选择状态和当前加载结果。
6. 需要升级 Agent 时作为独立维护项，保留原账本、凭据和工作区，先排空任务再更新；不能用全新 Agent 安装替代原账本交接。

即使 schema 相同，也应保留一致备份并验证恢复。更新失败时停止新写入，按已审核的计划回退。

## 7. 回退与启动失败

API/Web 回退必须使用同一受审计划，保留原 Compose 文件栈、环境、数据库及加密密钥。对数据库的任何恢复先停止唯一写者，不让旧二进制直接写不兼容 schema。维护回执为未知或 `rollback_failed` 时保留标记与日志，先对账实际状态，不删锁强启。

新增社区包后回退到不具备加载器的版本，不等于包代码已被宿主热卸载。先记录当前加载摘要及下一次选择，按[插件恢复说明](../backend/plugin_host/packages/README.md)检查并保留插件目录，再关闭唯一 Control 并回退；旧宿主忽略或无法加载某个目录不代表插件业务状态已恢复。

插件导致启动失败时，使用与当前宿主匹配的 CLI 检查或恢复选择，保留 `selection-backups` 和包文件，不删除数据库或模型。`reset-selection` 不回滚业务数据库；按指南重启控制面并核对插件状态。

## 8. 观测与上线验证

Prometheus/Grafana 与平台 API/Web 独立维护，详见[运行观测手册](../observability/README.md)。更新控制台不自动覆盖 Exporter、看板、TSDB、网关路由或 CCAE 配置。

上线前检查 HTTPS、版本/修订与 SDK、三角色权限、持久化和恢复、Agent 与设备身份、采样时效、部署/重启准入、目标 P/D 数量、普通/SSE 推理及日志指标；已启用的外部集成按各自手册验证。
