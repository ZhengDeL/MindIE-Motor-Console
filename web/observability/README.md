# Docker 运行观测模块

此模块独立运行 Grafana OSS、Prometheus 和 HTTPS 入口，复用已经安装的 Node Exporter、Ascend NPU Exporter。它不修改 Motor 原生调度逻辑，不挂载 NPU 或 Docker socket，也不参与推理流量。平台、数据库、Agent 与模型服务可以分别维护。这里的独立部署模块不是 Grafana 插件市场安装包；平台 Grafana 插件只管理连接、只读检查和可选导航入口，Grafana 自身仍使用独立登录。

本文配置示例使用 Grafana OSS 13.3.1、Prometheus 3.13.5 LTS；准备脚本接收显式镜像 ID，不会自动下载或验证某个版本。镜像来源分别是官方 `grafana/grafana` 和 `prom/prometheus`；安装时必须核验所需架构并固定镜像 ID，禁止使用 `latest` 或来源不明的镜像代理。Grafana 的独立账号与平台账号分别管理，平台对接使用 Viewer 服务账号令牌。

## 准备与启动

1. 在目标 Docker 主机准备三个已核验的 arm64/amd64 官方镜像，包括 nginx。离线场景先传入镜像归档，校验 SHA256 后 `docker load`，再核对导入后的完整 image ID、架构和来源收据。
2. 准备受信任的 HTTPS 服务端证书及匹配私钥。证书 SAN 必须覆盖访问主机。只能使用服务端私钥，不得复制 CA 私钥。
3. 确认现有 Exporter 的地址、指标和网络可达性。Node Exporter 负责 CPU、内存、OS、磁盘和网络，NPU Exporter 按其版本、驱动和运行时支持提供型号、利用率、HBM、温度、功耗、健康等设备指标，进程与容器归属覆盖须另外核实；缺失指标不能当作 0。平台复用明确的观测源，不把设备声明当成真实硬件指标。
4. 在全新目录生成配置，下面的地址与镜像 ID 均为示例，执行前替换：

```sh
python3 web/scripts/prepare_observability.py \
  --root /srv/qinghuan/observability \
  --origin https://monitor.example.com:19444 \
  --bind-ip 0.0.0.0 --port 19444 \
  --grafana-image 'sha256:<verified-grafana-image-id>' \
  --prometheus-image 'sha256:<verified-prometheus-image-id>' \
  --nginx-image 'sha256:<verified-nginx-image-id>' \
  --server-cert /secure/server.crt --server-key /secure/server.key \
  --node-exporter worker.example.com:10100 \
  --npu-exporter worker.example.com:8082
```

每个目标可重复传入相应参数。工具仅创建新目录、配置和随机凭据，不安装 Exporter、不启动容器、不重启其他服务。重复执行会拒绝覆盖现有目录。准备后先核验：

```sh
cd /srv/qinghuan/observability
docker compose --env-file .env.observability -f compose.yaml config --quiet
docker compose --env-file .env.observability -f compose.yaml run --rm --no-deps \
  --entrypoint /bin/promtool prometheus check config /etc/prometheus/prometheus.yml
```

第二条命令使用同镜像的 promtool 检查完整配置；启动后还应执行 `nginx -t` 和真实 HTTPS 检查。确认目标、证书、预算和端口后启动本模块：

```sh
docker compose --env-file .env.observability -f compose.yaml up -d --no-build --pull never
docker compose --env-file .env.observability -f compose.yaml ps
```

初始管理员用户名为 `admin`，随机密码保存在安装目录的 `private/access.json` 与 `private/grafana/admin-password`，不要复制到 Git、工单或日志。首次登录后按组织要求管理账号；需要平台只读访问时，在 Grafana 创建 Viewer 服务账号并颁发有限期令牌，写入平台私有配置。不要开启匿名访问来省略授权。

Grafana 内置简体中文，无需另装语言包。本模块默认 `GF_USERS_DEFAULT_LANGUAGE=zh-Hans`。已有安装可以在“管理 → 常规 → 默认首选项”选择中文（简体）并保存，无需重启；个人“头像 → 个人资料 → 首选项”的语言会覆盖组织和服务器默认值。看板名称与说明独立编写为中文，指标名和接口名保留原始标识。

## 数据、日志与资源

| 内容 | 安装目录内路径 | 说明 |
| --- | --- | --- |
| Prometheus 历史指标 | `data/prometheus` | UID/GID 65534；默认保留 7 天、1GB 历史块 |
| Grafana 数据库和插件目录 | `data/grafana` | UID/GID 472；与平台业务数据库分离 |
| Exporter 与 Motor 目标 | `config/prometheus/targets/*.json` | file_sd 每 60 秒读取；默认采样 30 秒、请求超时 10 秒 |
| 数据源与看板 | `config/grafana` | 只读挂载；保留原生看板文件的 SHA256 |
| 登录和签名密钥 | `private/grafana` | 仅 Grafana 用户读取；不在环境变量中传明文密码 |
| 服务端 TLS | `private/tls` | 独立服务端证书和私钥副本；私钥仅 nginx 所属组读取 |
| 容器日志 | Docker `json-file` 日志 | 每容器 10MB × 3；通过 `docker compose logs` 查询 |

资源上限合计 2 CPU、1920 MiB RAM，无额外 swap；三个容器均使用 runc、非 root、capabilities 全部删除和只读根文件系统。默认 CPU/内存预算适合少量节点的轻量观测，目标数、并发查询或看板增多时需要重新测量容量。Prometheus 的 1GB 限制约束历史块，WAL、head、临时合并和 Grafana 数据仍需额外磁盘空间，不能把它理解为整个目录的绝对磁盘配额。

每目标采样上限为 Node Exporter 50,000 条、NPU Exporter 20,000 条。指标名称数量与时间序列数量不同，大 CPU 主机的单个指标名会产生许多带 CPU、设备或挂载点标签的序列；容量检查应统计完整样本数量，不能只数指标名称。超过上限会使整个目标本次采样失败，不会默认为零。NPU 和宿主图例分别包含设备 ID、网卡或挂载点，便于区分同一宿主上的多个对象。

需要变更采样周期、日志轮转、保留期或资源预算时，修改本模块配置并执行配置检查，只重建本模块对应服务。不要对系统、模型权重或现有 Exporter 目录递归 chown。准备工具仅设置新建观测目录中各服务数据和秘密文件的所有者。

## 看板与验收边界

- “集群概览 · 宿主与 NPU”提供紧凑摘要、节点资源对照和节点级趋势。点击节点进入“节点详情 · 宿主与 NPU”，查看逐芯片利用率、HBM、温度、功耗及上报进程数；展开 CPU/内存、存储/网络和采集诊断可检查宿主细节。顶部支持环境、节点、网卡筛选，并保留时间范围。
- 两个看板使用 Node Exporter 和 NPU Exporter 的真实字段。NPU 型号来自 `npu_chip_info_name`，健康状态按官方定义 1 正常、0 异常。当前值要求指标与 `up` 的时间戳均在过去 90 秒内、目标抓取成功、数值合法且设备来源唯一；HBM 的已用和容量必须同时有效。缺失或过期保持空值，不补零。设备观测数量、利用率和进程数均不等于平台资源预留或可部署空闲量。
- 网络按所选宿主网卡分别展示，不累加 bond 与从接口，也不将宿主网络解释为 NPU 专用互联。文件系统按挂载点列出，避免重复累加容量；操作系统使用 `node_os_info.pretty_name`，不根据内核版本猜测发行版。
- 看板由 `python3 web/scripts/build_host_npu_dashboards.py` 确定性生成，支持 `--output-dir` 输出到独立目录核对。只使用 Grafana 内置面板，无需安装第三方插件。已有安装可先备份原看板、核对旧 SHA，再替换两个 JSON；文件 provisioning 自动读取，不必重启模型、Exporter、Agent、Grafana 或 Prometheus。
- CPU 历史使用 `qinghuan:host_cpu_busy_percent:rate2m` 预计算序列：每 30 秒在 Prometheus 内对既有 CPU 计数器计算一次固定 2 分钟的节点忙碌比例，长时间范围仅读取节点级结果，避免高核数主机超过查询样本预算。`rules/host-npu.rules.yaml` 由同一生成器输出；可用 `--rules-output-dir` 指定独立目录。规则仍检查有限数值、当前采样时效和唯一成功来源，缺失不补零。
- 新安装会配置 `rule_files`。已有安装应先备份原配置，加入 `/etc/prometheus/rules/host-npu.rules.yaml`，经 `promtool check rules` 和 `promtool check config` 校验后，仅向对应 Prometheus 进程发送 SIGHUP，并检查 reload 成功及规则无错误。无需重启容器、增加采集器或修改抓取周期、资源预算。新规则从启用后积累历史，不回填生产 TSDB；生效前的 CPU 汇总曲线留空，当前 CPU 即时读数仍来自 Exporter。
- 原生 `motor-all-metrics` 看板从固定 Motor 源码复制，Prometheus 数据源 UID 保持 `prometheus`。默认原生目标为空；模型停止时看板没有模型数据是预期结果。启动真实模型后，需要添加真实 Coordinator/引擎目标、正确标签和 TLS/认证配置，再分别验收指标与业务链路。
- 本模块不安装 Loki、Tempo、OpenTelemetry Collector 或官方 CCAE/AIGW 本体，不提供不存在的数据源。日志、trace 和 CCAE/Kafka 业务链路需要独立配备与验收。
- 验收至少包括 HTTPS 证书验证、匿名拒绝、Viewer 权限、数据源健康、所有已配置 Exporter 目标的真实状态、看板查询与无数据语义、容器资源约束和受保护服务的身份/状态不变。容器 healthy 或 HTTP 200 不能代替模型推理验收。

## 备份与维护

备份配置、TLS/秘密文件和 Grafana 数据库时使用组织授权的加密备份，秘密与备份密钥分离保存。Grafana 使用 SQLite 时采用一致性备份或只停止本模块 Grafana 后备份；不要直接复制正在写入的 SQLite 数据库。Prometheus 历史指标可按组织策略保留，块目录备份应遵循其官方快照/一致性要求。

升级前记录完整镜像 ID、配置 SHA、Dashboard UID、目标清单和权限。固定新镜像后先隔离验证，再只更新本模块。失败时恢复旧镜像和原配置，不自动覆盖正在写入的数据库，不重建 Exporter、Agent 或模型。停止本模块使用精确项目名，禁止全局 prune。

Grafana 容器重建且 IP 改变时，入口 nginx 需要重新解析服务地址；先确认 Grafana 健康，再仅重建本项目的 `ingress`。Prometheus 配置变更先执行 promtool，通过后可向本项目 Prometheus 进程发送 SIGHUP 热重载，并核对 reload 成功指标及目标状态；不需要重启 Exporter。

## 官方依据

- [Grafana 仪表板设计最佳实践](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/)
- [Grafana Linux 节点与集群看板案例](https://grafana.com/docs/learning-paths/linux-server-integration/understand-dashboards/)
- [Prometheus 预计算规则](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)
- [Grafana OSS 13.3.1 发布与官方镜像](https://grafana.com/grafana/download/13.3.1?edition=oss&platform=docker)
- [Grafana Docker 安装与目录权限](https://grafana.com/docs/grafana/latest/setup-grafana/installation/docker/)
- [Prometheus 下载与 LTS 版本](https://prometheus.io/download/)
- [Prometheus 官方 Docker 镜像](https://prometheus.io/docs/prometheus/latest/installation/)
- [Prometheus 本地存储与保留期](https://prometheus.io/docs/prometheus/latest/storage/)
- [Ascend NPU Exporter 指标语义](https://www.hiascend.com/document/detail/en/mindcluster/2600/clustersched/schedulingug/docs/en/scheduling/api/npu_exporter/01_prometheus_metrics_api.md)
