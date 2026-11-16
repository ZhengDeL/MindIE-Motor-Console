# 不安装 Web 的 MCP 部署

Headless 部署只运行 MCP 与 Control 两个服务。MCP 负责管理协议和工具授权，Control 复用原有配置、任务协调、Agent 与原生 Motor 适配。模型业务推理仍访问其独立业务入口。原生 Motor pin、已有 Web 默认入口和浏览器 Cookie/CSRF 语义保持不变。

架构和插件合同分别见 [MCP 入口](../../mcp/README.md) 与 [插件开发](../../mcp/PLUGIN_DEVELOPMENT.md)。本指南中的域名、镜像名和摘要均为示例占位，部署前替换为经审查的真实值。

实际运行操作需要事先安装、注册且经身份/连接验证的执行 Agent、模型镜像/权重与目标资源。节点注册与 Agent 引导安装沿用[已有 Agent 部署流程](../AGENT_ROLLOUT.md)和管理员 Control API；参考 MCP 工具集未开放节点凭据登记或平台账号管理。空数据库只会返回空资源列表，不会自动发现宿主或模拟可用 NPU。

## 认证与工具权限

参考方案使用部署方登记的私有服务凭据。它不是 OAuth authorization server，不提供 OAuth issuer、动态客户端注册、浏览器授权或发现端点。要求 OAuth 的客户端需要自行提供经审查、可替换的认证插件与真实 issuer；不能把静态服务 token 配成虚构 OAuth issuer。

每个主体有两枚不同的随机 token：客户端只使用 MCP token；MCP 通过该主体独立 backend token 请求 Control。两侧 registry 只登记 SHA256，gateway registry 另外引用私有 backend token 文件。id、subject、role、scopes 与服务/节点目标必须完全一致；Control client 每次业务调用前核对真实下游身份，不接受共享 legacy token、Cookie 或 MCP token 透传。

| 工具 | HTTP API | scope | role |
| --- | --- | --- | --- |
| `list_nodes`, `get_node_resources` | GET `/api/nodes`, `/api/nodes/{id}` | `nodes:read` | viewer/operator |
| `list_templates`, `get_template` | GET `/api/templates`, `/api/templates/{id}` | `templates:read` | viewer/operator |
| `list_services`, `get_service` | GET `/api/services`, `/api/services/{id}` | `services:read` | viewer/operator |
| `list_jobs`, `get_job` | GET `/api/jobs`, `/api/jobs/{id}` | `jobs:read` | viewer/operator |
| `get_service_logs` | GET `/api/services/{id}/logs` | `logs:read` | viewer/operator |
| `get_service_metrics` | GET `/api/services/{id}/metrics` | `metrics:read` | viewer/operator |
| `create_service_draft`, `update_service_draft` | POST `/api/services`, PUT `/api/services/{id}` | `configuration:write` | operator |
| `plan_deployment`, `get_deployment_plan` | POST `/api/plugins/mcp/operations/plan`, GET `/api/plugins/mcp/operations/plans/{id}` | `deployment:plan` | operator |
| `apply_deployment` | POST `/api/plugins/mcp/operations/apply` | `deployment:apply` | operator |

只有具备所有 required scopes 的工具才可发现及调用；viewer 的写 scope 即使误配也不能提升为 operator。后端以真实 service credential 再次授权。服务/节点白名单限制会拒绝对应无界集合列表；受限 `list_jobs` 需传授权 `service_id`。服务白名单禁止 create；节点白名单禁止 create/PUT，避免放置参数绕过目标授权。特定服务、节点、job、日志与指标仍逐一核对权限。通用模板读取没有节点/服务绑定。

配置工具保存完整草稿，部署前不会执行运行操作。更新必须提交当前 `expected_revision`，`user_config`、`env` 及原生未知字段不做白名单投影；工具输出会脱敏，不能拿脱敏输出中的密钥标记覆盖原始配置。日志 `tail` 默认 200、最大 2000；请求、结果和总时长均有固定上限。

## 构建两个独立镜像

从仓库根目录使用 BuildKit 执行，固定当前源码和各基础镜像摘要。`headless` target 的构建依赖图没有前端，不复制 UI manifest 或静态资源；现有 `api`、`web`、`bundled` target 语义不变。新的 MCP Dockerfile 要求显式提供两个基础镜像参数，没有可静默使用的默认 tag。

```bash
DOCKER_BUILDKIT=1 docker build -f web/Dockerfile --target headless \
  --build-arg PYTHON_BUILD_IMAGE='python:3.12-slim-bookworm@sha256:<reviewed-digest>' \
  -t registry.example.invalid/motor-control:reviewed .
DOCKER_BUILDKIT=1 docker build -f mcp/Dockerfile \
  --build-arg NODE_BUILD_IMAGE='node:22-bookworm-slim@sha256:<reviewed-digest>' \
  --build-arg NODE_RUNTIME_IMAGE='node:22-bookworm-slim@sha256:<reviewed-digest>' \
  -t registry.example.invalid/motor-mcp:reviewed .
```

自行构建、扫描并得到实际镜像摘要后，再设置部署变量；不使用上述占位摘要启动：

```bash
export MOTOR_HEADLESS_ROOT=/srv/motor-headless
export MOTOR_HEADLESS_UID=10001 MOTOR_HEADLESS_GID=10001
export MOTOR_HEADLESS_CONTROL_IMAGE='registry.example.invalid/motor-control@sha256:<actual-reviewed-digest>'
export MOTOR_MCP_IMAGE='registry.example.invalid/motor-mcp@sha256:<actual-reviewed-digest>'
export MOTOR_MCP_PUBLIC_ORIGIN=https://mcp.example.invalid:8443
# 默认只发布到宿主loopback；对外开放需明确指定受控接口及防火墙策略。
export MOTOR_MCP_BIND_IP=127.0.0.1 MOTOR_MCP_PORT=8443
```

Compose 服务都以同一 UID/GID 运行。所有 registry、token、加密密钥与 TLS 私钥须为该 UID 所有、普通独立文件、POSIX 权限 0600；不用组可读密钥。父目录不要使用符号链接或 junction。以下 Linux 示例由管理员准备目录，之后 CLI 以服务 UID 运行：

```bash
sudo install -d -m 0700 -o "$MOTOR_HEADLESS_UID" -g "$MOTOR_HEADLESS_GID" \
  "$MOTOR_HEADLESS_ROOT" "$MOTOR_HEADLESS_ROOT/issuer" \
  "$MOTOR_HEADLESS_ROOT/control-config" "$MOTOR_HEADLESS_ROOT/control-credentials" \
  "$MOTOR_HEADLESS_ROOT/mcp-config" "$MOTOR_HEADLESS_ROOT/mcp-credentials" \
  "$MOTOR_HEADLESS_ROOT/data" "$MOTOR_HEADLESS_ROOT/logs" "$MOTOR_HEADLESS_ROOT/tls"
```

将真实证书链和私钥分别安装为 `tls/server.crt` 与 `tls/server.key`，证书 SAN 覆盖实际 MCP 主机名；私钥为服务 UID 所有且 0600。生产客户端必须校验证书链，不关闭 TLS 校验。为 Control 新建独立、稳定的 `control-credentials/encryption.key` 与另一枚独立的 `control-credentials/bootstrap.token`（各至少 32 个随机字符，0600）；不要在日志、命令参数或 Git 中传递密钥值。初次启动尚无平台账号时，原有生产 guard 仍要求 bootstrap token；MCP 服务凭据不替代该本地账号初始化边界，不需要通过浏览器初始化才能使用服务凭据。

## 离线准备并明确装配

准备 Control Session/操作扩展只生成新文件，不启动服务。它检查实际源码摘要，并把容器模块根固定到 `/app/backend`：

```bash
docker run --rm --user "$MOTOR_HEADLESS_UID:$MOTOR_HEADLESS_GID" \
  -v "$MOTOR_HEADLESS_ROOT/control-config:/out" \
  --entrypoint python "$MOTOR_HEADLESS_CONTROL_IMAGE" /app/scripts/prepare_mcp.py \
  assembly --output /out/assembly.json --module-root /app/backend
```

凭据 CLI 一次生成两侧登记和两枚独立 token。以下例子创建只读主体；操作主体用另一个 id、operator role 与明确授权的 scopes，不复用读主体 token：

```bash
docker run --rm --user "$MOTOR_HEADLESS_UID:$MOTOR_HEADLESS_GID" \
  -v "$MOTOR_HEADLESS_ROOT/issuer:/issuer" \
  --entrypoint python "$MOTOR_HEADLESS_CONTROL_IMAGE" /app/scripts/prepare_mcp.py \
  issue --directory /issuer --id automation-reader --subject automation-reader \
  --role viewer --scopes nodes:read templates:read services:read jobs:read logs:read metrics:read \
  --expires-in 86400 --runtime-directory /run/motor-mcp/credentials
```

在受控管理员会话中，把 `issuer/backend-credentials.json` 安装到 `control-credentials/backend-credentials.json`；把 `issuer/mcp-credentials.json` 安装到 `mcp-credentials/mcp-credentials.json`，把 `issuer/backend-automation-reader.token` 安装到 `mcp-credentials/backend-automation-reader.token`，均使用 `install -m 0600 -o UID -g GID`。`issuer/mcp-automation-reader.token` 只经安全渠道交给该客户端。每增加主体都同步两个完整 registry 和对应独立 backend token；不要挂载整个 issuer 目录给服务。

生成 MCP 配置时，脚本在镜像内部计算制品 SHA256 与绝对 root；运行容器必须使用同一镜像目录布局。命令不联网、不执行插件，并以 `wx` 拒绝覆盖已有配置：

```bash
docker run --rm --user "$MOTOR_HEADLESS_UID:$MOTOR_HEADLESS_GID" \
  -v "$MOTOR_HEADLESS_ROOT/mcp-config:/out" \
  --entrypoint node "$MOTOR_MCP_IMAGE" scripts/prepare-composition.mjs \
  --transport http --output /out/composition.json \
  --registry-file /run/motor-mcp/credentials/mcp-credentials.json \
  --control-origin http://control:8000 --allow-insecure-control true \
  --public-origin "$MOTOR_MCP_PUBLIC_ORIGIN" --tls-directory /run/motor-mcp/tls \
  --toolsets query,configuration,operations
```

只读部署可选 `--toolsets query`，去掉配置与运行工具包的选择。配置文件内 `selected` 和精确 binding 可直接审查；改变工具域、认证或 Control 实现应准备新的 candidate、validate、停机并显式切换，保留旧配置供恢复。更换镜像会改变插件摘要，须重新生成并审查配置。

## 启动与网络

```bash
docker compose -f web/compose.headless.yaml config --quiet
docker compose -f web/compose.headless.yaml up -d
docker compose -f web/compose.headless.yaml ps
```

MCP TLS 地址为 `${MOTOR_MCP_PUBLIC_ORIGIN}/mcp`，HTTP 客户端每次发送该主体的 `Authorization: Bearer ...`；不要把 token 放 URL。`allowedHosts` 只含 hostname，`allowedOrigins` 为精确 scheme/host/port。CLI 客户端不发送 Origin 时可访问；浏览器 Origin 必须显式在列表中。

Control 只有私有网络地址，不发布端口；SQLite DB 只存在于 Control 数据目录，没有独立数据库公网服务。MCP 与 Control 通过 `control_plane` 内部网络通信，此示例只在该隔离网络上显式允许明文内部 HTTP；MCP 对外仍强制 TLS。需要对内部链路也加密时，为 Control 设置经审查的 HTTPS 入口和可信 CA，并更改固定 Control origin，不关闭证书验证。

Control 另有独享 `runtime_egress` 网络，用于注册 Agent、Exporter 及原生服务探测。它不发布 Control 端口，但默认出站网络不等于防火墙或网络命名空间安全隔离；仍需已有 Agent TLS、注册 URL/来源限制及部署方出站策略。Compose 不挂载宿主 Docker socket，不启动 Web，不让 MCP 直接访问数据库、SSH、Docker 或 Kubernetes。

`MOTOR_UI_ENABLED=false` 和 `MOTOR_STATIC_ENABLED=false` 使 Control 冷启动不读取 UI manifest、不注册静态 Web 入口。准备 CLI 明确装配 Session 与 MCP 操作扩展，旧入口默认装配不受此启动方式影响。

## 本地 stdio 与 loopback 开发

本地依赖构建后可运行同一准备脚本，使用宿主自己的绝对路径；其 artifact roots 自动固定为当前 checkout 的构建目录：

```bash
node mcp/scripts/prepare-composition.mjs --transport stdio \
  --output /private/motor-mcp/composition.json \
  --registry-file /private/motor-mcp/mcp-credentials.json \
  --stdio-credential-id automation-reader --control-origin http://127.0.0.1:8000 \
  --toolsets query
node mcp/dist/cli.js start --config /private/motor-mcp/composition.json
```

stdio 由受信本地 launcher 选择 credential id，不把协议 stdin 中自填身份当授权；launcher 与 registry/token 文件访问权限就是本地信任边界。若使用容器 stdio，应 `docker run --rm -i` 且不分配 TTY，准备配置中的 Control origin 必须在容器网络内可达。宿主与容器的 `127.0.0.1` 不互通，不能把本地模板直接当容器可达入口。

不带 TLS 的 HTTP 开发必须使用 `--transport loopback-http`，仅绑定 literal `127.0.0.1:8081`，只接受 loopback hostname 与模板中精确 Origin。此模式供同一宿主客户端使用；生产 Compose 使用带证书的 HTTP transport，不通过容器端口映射绕过 loopback 限制。

## 操作、对账与凭据维护

`plan_deployment` 的 `action` 支持 deploy/stop/restart/rebuild/scale/undeploy，scale 携带受限实例 counts。返回持久化计划、摘要和有效期后，由授权人审查或按事先明确的策略授权，再调用 `apply_deployment` 提交同一 `plan_id`、原始 `plan_digest` 与服务名称 `confirmation_name`。服务名确认字段与模型输出都不是独立人类审批证明。

后端校验计划 owner、服务修订摘要、动作参数、provider generation、有效期与当前权限，以该计划的固定幂等键进入原任务协调器。修改服务后旧计划拒绝 apply；同一已提交计划重放返回同一 job，不能换 MCP request id 触发第二次执行。禁止自动重试写请求。

超时、断连、回复过大或损坏时，写操作可能已完成。结构化错误的 `outcomeUnknown:true` 和 `reconciliation` 会保留原路由/plan_id；先 `get_deployment_plan`、`get_job` 或限定服务的 `list_jobs` 对账，再决定后续动作。create/update 的不确定结果也须先读取服务及修订，不能假定请求失败。

HTTP 2xx 还须匹配对应回执合同：计划需包含规范 handle、摘要、匹配的服务/动作、有效时间与预检对象；apply 需返回非空 job_id 和布尔 replayed；配置保存需返回匹配资源与修订。成功 JSON 缺少这些字段时返回 `CONTROL_RECEIPT`，写结果仍标记未知并保留对账线索。证据明确区分 `configuration_saved`、`plan_created`、`job_accepted` 与 Control 来源的 `snapshot_read`，不根据任意成功响应制造受理或 ready 结论。

任务被接收、job succeeded、进程 Running、HTTP 200 与模型 ready 是不同证据。实际交付还需执行 Agent/资源归属、真实状态、模型请求/流式、路由及相关缓存与性能验收；参考工具不会把 job receipt 宣称成已完成 NPU/Kubernetes 部署。

撤销时在 issuer 目录运行 `prepare_mcp.py revoke --directory /issuer --id ID`，再以原子文件替换方式同步两个 registry。服务每次重新读取登记，撤销/过期会阻止后续调用；已提交任务仍需按 job 对账。轮换用新 id 生成独立 token，部署并验证后撤销旧 id，不直接覆盖仍在运行的凭据导出文件。备份私有 registry、稳定加密密钥、DB、审查后的配置与固定镜像摘要；不要提交这些材料。

本功能的本地协议与权限验证、隔离后端检查和容器构建检查分别记录。真实 NPU、真实集群、生产证书/网络、第三方客户端适配及长稳/并发验收需部署环境补充，不能由本地夹具或编译通过推定成功。
