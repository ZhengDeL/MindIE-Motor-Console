# Docker 裸机 Node Exporter 离线安装

适用范围：MindIE-Motor Console 的 Docker 主机观测接口。下面的命令在 **Linux Bash** 中执行。示例目录为 `/opt/motor-console/monitoring/node-exporter`，示例地址为文档专用地址，需替换为实际管理网地址。Kubernetes 节点复用集群侧已有监控，不执行本手册重复安装。

Node Exporter 提供宿主 CPU、内存、系统版本、磁盘、文件系统和网络指标；NPU 使用 [NPU 采集与 Exporter 手册](DOCKER_NPU_EXPORTER.md)。本版主机观测统一采用 **Node Exporter → Agent → 控制台**：CPU、内存和操作系统来自同一采集源，Agent 不再自行读取宿主 `/proc/stat`、`/proc/meminfo` 或 `os-release` 采集这些指标，也不以 Docker 信息回退为宿主系统。用于宿主身份绑定和 NPU 快照时效校验的只读信息独立保留。采集缺失时保留不可用状态，不能补零或混入另一来源。

安装 Node Exporter 后，Agent 直接展示 CPU、内存和 OS 等已适配字段；磁盘、网络及历史曲线由 Prometheus 抓取和 Grafana 看板展示，不能因 Exporter 安装成功就推定整条链路完成。平台可生成 Grafana 配置并单独部署观测模块，见[运行观测手册](../observability/README.md)；配置生成不替代实际抓取及看板验收。

## 1. 先确认是否已有采集器

只读检查目标节点：

```bash
uname -m
docker compose version
docker info --format '{{json .Runtimes}}'
docker ps -a --format '{{.ID}}\t{{.Names}}\t{{.Image}}'
systemctl status node_exporter --no-pager || true
pgrep -a -f '(^|/)node_exporter([[:space:]]|$)' || true
ss -lntp 'sport = :9100'
```

已存在 Node Exporter、9100 监听、同用途 systemd 服务或 `qinghuan-host-monitoring` Compose 项目时，先确认归属并复用。不要覆盖配置、停止旧实例，或换个端口重复采集。记录已有容器的完整 ID、镜像 ID、启动时间和重启次数，之后确认未受影响：

```bash
docker inspect --format '{{.Id}} {{.Image}} {{.State.StartedAt}} {{.RestartCount}}' EXISTING_CONTAINER_ID
```

已有实例也可能监听非默认端口。通过容器启动参数、服务 unit 和节点维护记录确认实际 `/metrics` 地址，再填写 Agent 的 `MOTOR_NODE_EXPORTER_URL`；不能因为 9100 空闲就认定没有采集器。

本方案要求 Docker Compose **2.15.1 或以上**、可用的 `runc` 和宿主只读文件访问。`uts: host` 用于获取宿主主机名；`pid: host`、`network_mode: host` 和宿主根目录只读绑定遵循官方 Docker 部署方式。宿主挂载有敏感信息，应仅使用经审核镜像。不要增加 `privileged`、Docker socket、NPU 设备或 Ascend runtime；不要为监控升级驱动、修改 SELinux 或业务挂载。

## 2. 固定镜像与离线交付

Compose 文件位于 [node-exporter.compose.yaml](../deploy/monitoring/node-exporter.compose.yaml)，要求显式提供不可变镜像引用，不会联网自动拉取。

以下固定镜像示例对应官方 **v1.12.1**；部署前按选定发布的来源、架构和配套要求复核，不将它视为自动跟随最新版的推荐：

```text
quay.io/prometheus/node-exporter@sha256:1b4e4438faca4dd7e001dd445d161a4a2091b0fededa84093b3a8dfeae1f1be0
```

使用者仍需核验仓库 manifest、目标架构和拉取后的 Image ID。摘要一致不等于目标系统运行验收或镜像签名验证，软件来源和兼容性应另行审查。

**文件系统只读标记：**官方[问题 #3755](https://github.com/prometheus/node_exporter/issues/3755)与[修复 PR #3758](https://github.com/prometheus/node_exporter/pull/3758)讨论了只读根目录绑定导致 `node_filesystem_readonly` 与宿主挂载属性不一致的问题。核对所选二进制是否包含相关修复，并将结果与宿主 `findmnt` 对照后再配置只读故障告警；不能仅凭 PR 状态或版本号推定目标镜像行为。已有经验证实例优先复用，不因本文自动升级。

联网交付机选择与目标一致的架构，以下为 arm64 示例。使用一个新的交付目录，不覆盖已有包：

```bash
set -eu
umask 077
mkdir node-exporter-offline
cd node-exporter-offline
image_ref='quay.io/prometheus/node-exporter@sha256:1b4e4438faca4dd7e001dd445d161a4a2091b0fededa84093b3a8dfeae1f1be0'
docker pull --platform linux/arm64 "$image_ref"
image_id=$(docker image inspect --format '{{.Id}}' "$image_ref")
docker image inspect --format '{{.Os}}/{{.Architecture}} {{.Config.User}} {{json .RepoDigests}}' "$image_id"
printf '%s\n' "$image_id" > node-exporter.image-id
docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image_id" > node-exporter.platform
docker save --output node-exporter.image.tar "$image_id"
sha256sum node-exporter.image.tar node-exporter.image-id node-exporter.platform > SHA256SUMS
```

`aarch64` 对应 `linux/arm64`，`x86_64` 对应 `linux/amd64`。目标节点收到包后，在该包目录执行：

```bash
set -eu
sha256sum -c SHA256SUMS
image_id=$(cat node-exporter.image-id)
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
docker load --input node-exporter.image.tar
test "$(docker image inspect --format '{{.Id}}' "$image_id")" = "$image_id"
test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image_id")" = "$(cat node-exporter.platform)"
```

同时核对目标 `uname -m`，不能只核对包内自带的平台文件。SHA256 清单用于发现传输变化，其可信来源应由交付渠道保证；不要把同包清单当作独立来源认证。

## 3. 在目标镜像核对启动参数

先执行不接宿主命名空间、不挂宿主目录的帮助命令。输出留在本地交付目录，不能用“容器启动成功”代替参数和指标验证：

```bash
docker run --rm --pull never --runtime runc --network none \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 32 --cpus 0.25 --memory 64m \
  --entrypoint /bin/node_exporter "$image_id" --version
docker run --rm --pull never --runtime runc --network none \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 32 --cpus 0.25 --memory 64m \
  --entrypoint /bin/node_exporter "$image_id" --help > node-exporter.help.txt 2>&1
for collector in cpu meminfo os uname stat loadavg diskstats filesystem netdev netstat; do
  grep -F "collector.$collector" node-exporter.help.txt >/dev/null || exit 1
done
grep -F 'path.procfs' node-exporter.help.txt
grep -F 'path.sysfs' node-exporter.help.txt
grep -F 'path.rootfs' node-exporter.help.txt
```

Linux 的 `node_boot_time_seconds` 来自 **`stat` collector**。不要填 `--collector.boottime`：官方的独立 `boottime` collector 适用于其他操作系统，不是 Linux 入口。本配置关闭所有默认 collector，仅启用上述十项；不打开 perf、systemd、processes、textfile 等额外采集。

## 4. 安装独立实例

先完成第 1 节检查，确认 9100 与项目名没有被占用。将发布包内审核过的 Compose 文件复制到一个**尚不存在**的目录；本例从发布包根目录执行：

```bash
set -eu
umask 077
install_dir=/opt/motor-console/monitoring/node-exporter
test ! -e "$install_dir"
install -d -m 0700 "$install_dir"
install -m 0600 web/deploy/monitoring/node-exporter.compose.yaml "$install_dir/compose.yaml"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
(set -o noclobber; printf 'MOTOR_NODE_EXPORTER_IMAGE=%s\nMOTOR_NODE_EXPORTER_LISTEN_ADDRESS=127.0.0.1:9100\n' \
  "$image_id" > "$install_dir/node-exporter.env")
cd "$install_dir"
docker compose --project-name qinghuan-host-monitoring --env-file node-exporter.env -f compose.yaml config --quiet
docker compose --project-name qinghuan-host-monitoring --env-file node-exporter.env -f compose.yaml up -d --pull never node-exporter
docker compose --project-name qinghuan-host-monitoring --env-file node-exporter.env -f compose.yaml ps
```

不要 `source` 来路不明的环境文件。若任一步失败，保留报错与安装目录供检查，不重复覆盖执行。资源上限为 0.5 CPU、128 MiB 内存、64 PID，Go 并行度为 1；日志使用 Docker `local` 驱动，单文件 10 MiB，最多 3 个轮转文件。额度是保护上限，不是实测开销，也不能保证所有大型挂载环境都能在此额度内正常抓取。

宿主 `/` 挂载为 `/host:ro,rslave`，分别显式设置 `/host/proc`、`/host/sys`、`/host`。官方 v1.12.1 的 OS collector 将 `path.rootfs` 加到 `/etc/os-release` 或 `/usr/lib/os-release` 前，所以 `node_os_info` 应来自**宿主发行版**；`node_uname_info` 表示内核、架构、主机名。两者不能互相替代。

宿主根目录只读绑定不等于所有内核版本下递归子挂载都强制只读；Docker 对递归只读的支持与内核有关。不要为此修改生产挂载传播属性或重挂载文件系统。采集权限不足时明确显示失败并收敛检查权限，不直接改为 root/privileged。

## 5. 监听与 Prometheus 接入

默认只监听 **`127.0.0.1:9100`**，没有 Docker `ports` 映射。适用于宿主进程形式或共享宿主网络的 Prometheus。桥接网络里的 Prometheus 容器访问 `127.0.0.1` 会访问它自己；`host-gateway` 也不能让只绑定 loopback 的服务自动对外开放。

远程 Prometheus 需要由维护方把 `MOTOR_NODE_EXPORTER_LISTEN_ADDRESS` 改为指定管理网 IP，例如 `192.0.2.10:9100`，审核配置后精确重建本项目实例，并仅允许 Prometheus 管理地址（如 `192.0.2.20`）访问。不要直接开放到所有网卡或互联网；本手册不自动修改防火墙。Exporter 不使用平台登录凭据或 Agent Bearer token。跨不可信网络需要按官方 exporter-toolkit 配置 TLS/认证与 `--web.config.file`，另行审核只读证书挂载。

已有 Prometheus 可增加以下 job；替换地址后检查配置并按其既有变更流程加载，不启动第二套 Prometheus：

```yaml
scrape_configs:
  - job_name: qinghuan-host
    scrape_interval: 30s
    scrape_timeout: 10s
    static_configs:
      - targets: ["192.0.2.10:9100"]
```

十项 collector 中，filesystem 会访问挂载点。大量磁盘、失联 NFS 或慢挂载仍可能产生开销。Compose 排除常见虚拟、容器和 kubelet 子挂载，但保留业务模型目录；按实际清单审阅排除规则，不要为了缩短抓取把业务磁盘隐藏。先用 30 秒采样，观察抓取耗时、失败项和 Exporter 自身资源，再决定是否调整。

### Agent 读取同一采集源

在 Agent 的部署环境中配置以下参数，审核后只重建该 Agent，不重建业务模型容器：

```dotenv
MOTOR_NODE_EXPORTER_URL=http://127.0.0.1:9100/metrics
MOTOR_NODE_EXPORTER_ALLOWED_HOSTS=127.0.0.1
MOTOR_NODE_EXPORTER_CACHE_SECONDS=10
MOTOR_NODE_EXPORTER_TTL_SECONDS=45
```

回环地址仅适用于与 Exporter 共享宿主网络的 Agent。使用其他网络模式时需改为 Agent 实际可达的管理地址，并显式授权精确主机；不能假设容器的 `127.0.0.1` 指向宿主。缓存周期范围为 1–60 秒，观测有效期为 5–300 秒且不小于缓存周期；网页刷新复用缓存，不启动新的硬件命令。

Agent 读取的是 Exporter 的当前采样；CPU 使用率需要两次有效累计值计算，首次样本不足时不展示百分比。`node_os_info` 提供发行版、`node_uname_info` 提供内核信息，两者分别保留来源。响应失败、缺字段或超过有效期时按实际状态展示，不使用上次百分比冒充当前采样。即便 CPU/内存不可用，其他独立且有效的 NPU 观测仍按其自身来源和有效期处理。

复用未启用宿主 UTS namespace 的已有 Exporter 时，`node_uname_info` 的 `nodename` 可能是容器名称，不能据此修改平台的宿主名称。Agent 只读取其中的内核 `release`、系统类型与架构，宿主身份继续使用已核验的 Agent/节点身份。

HTTPS 采集可通过 `MOTOR_NODE_EXPORTER_CA_FILE` 指定受信 CA；`MOTOR_NODE_EXPORTER_TOKEN_FILE` 与 `MOTOR_NODE_EXPORTER_BASIC_AUTH_FILE` 二选一，后者为 `{"username":"...","password":"..."}` 形式的私有 JSON 文件。认证文件应仅受控账号可读并通过秘密文件挂载，不写入 URL、公开 Compose、浏览器存储或日志。使用前按部署工具的私有文件权限要求设置 0600，禁止关闭证书校验。

## 6. 验收真实指标

在 Exporter 所在宿主执行一次有界采样；完整指标写本地受控文件，不全部打印到终端或聊天：

```bash
umask 077
metrics_file=$(mktemp /tmp/motor-console-node-metrics.XXXXXX)
curl --noproxy '*' --fail --silent --show-error \
  --connect-timeout 3 --max-time 10 --max-filesize 5242880 \
  http://127.0.0.1:9100/metrics --output "$metrics_file"
wc -c "$metrics_file"
sha256sum "$metrics_file"
grep -E '^(node_os_info|node_uname_info|node_boot_time_seconds|node_scrape_collector_success|node_scrape_collector_duration_seconds|process_resident_memory_bytes|process_cpu_seconds_total)(\{| )' "$metrics_file"
cat /etc/os-release
uname -r
uname -m
hostname
findmnt -rn -o TARGET,FSTYPE,OPTIONS
```

逐项验收：

- `node_os_info` 的 `id`、`version_id`、`pretty_name` 与宿主 `/etc/os-release` 一致；没有该文件时核对 `/usr/lib/os-release`。不要展示 Exporter 容器发行版为宿主版本，也不要用浮点 `node_os_version` 代替完整发行版本。
- `node_uname_info` 的 `release`、`machine`、`nodename` 分别对应 `uname -r`、`uname -m`、`hostname`；内核版本不是操作系统发行版号。
- 十个启用 collector 均有 `node_scrape_collector_success{collector="..."} 1`。缺失、失败或过期是采集问题，不能当作 CPU、内存或磁盘占用为零。
- `node_boot_time_seconds` 与宿主启动时间合理一致；磁盘与网络设备集合符合宿主真实清单。对文件系统容量和只读标记与 `findmnt`、`df` 实际对照，尤其检查第 2 节已知问题。
- Prometheus 连续取得至少两次成功采样，`up{job="qinghuan-host"}=1`。这只证明抓取成功，还需核对标签、宿主身份和数值语义。查看 `scrape_duration_seconds`、collector 耗时与 `process_resident_memory_bytes`，没有实测前不承诺固定 CPU/内存开销。

CPU 累计时间不能直接显示为使用率。Prometheus 示例：

```promql
100 * (1 - sum by (instance) (rate(node_cpu_seconds_total{job="qinghuan-host",mode=~"idle|iowait"}[5m])) / sum by (instance) (rate(node_cpu_seconds_total{job="qinghuan-host",mode!~"guest|guest_nice"}[5m])))
100 * (1 - node_memory_MemAvailable_bytes{job="qinghuan-host"} / node_memory_MemTotal_bytes{job="qinghuan-host"})
time() - node_boot_time_seconds{job="qinghuan-host"}
```

上述依次为五分钟窗口 CPU 使用率、基于可用内存的占用率、启动后秒数。CPU 将 idle 和 iowait 归入空闲/等待，与控制台的忙碌口径保持一致；guest/guest_nice 不重复计入总量。界面使用相邻两次成功采样的短窗口差分，Prometheus 示例使用五分钟平滑窗口，**同一来源不代表同一时刻必须显示相同百分比**。

首次抓取、CPU 集合不完整、计数器复位或样本不足时保留“等待采样/采样不可用”，不能补零。非 Linux 或缺少 `MemAvailable` 的系统需单独定义内存口径，不直接套用。

## 7. 精确卸载与回退

仅卸载本手册**新建**的实例。先在安装目录获取完整 ID 并核对项目标签、镜像 ID：

```bash
cd /opt/motor-console/monitoring/node-exporter
container_id=$(docker compose --project-name qinghuan-host-monitoring --env-file node-exporter.env -f compose.yaml ps -q node-exporter)
test -n "$container_id"
test "$(docker inspect --format '{{index .Config.Labels "io.motor.installation"}}' "$container_id")" = qinghuan-host-monitoring
test "$(docker inspect --format '{{index .Config.Labels "io.motor.component"}}' "$container_id")" = host-node-exporter
test "$(docker inspect --format '{{.Image}}' "$container_id")" = "$(sed -n 's/^MOTOR_NODE_EXPORTER_IMAGE=//p' node-exporter.env)"
docker stop --time 15 "$container_id"
docker rm "$container_id"
```

先从 Prometheus 移除对应 job/target 并按既有流程加载，再停止采集。保留离线镜像包、环境文件、Compose、摘要和验收记录以便恢复；不要运行全局 `prune`、按模糊名称批量删除、卸载 systemd 旧实例或删除其他 Node/NPU Exporter。标签或镜像不匹配时停止卸载并人工核对。

## 来源与安装检查

依据：[官方 v1.12.1 Docker 部署说明与 collector 支持表](https://github.com/prometheus/node_exporter/blob/v1.12.1/README.md)、[启动参数](https://github.com/prometheus/node_exporter/blob/v1.12.1/node_exporter.go)、[宿主 OS 读取实现](https://github.com/prometheus/node_exporter/blob/v1.12.1/collector/os_release.go)、[Linux stat/启动时间](https://github.com/prometheus/node_exporter/blob/v1.12.1/collector/stat_linux.go)、[UTS namespace 配置](https://docs.docker.com/reference/compose-file/services/#uts)、[Docker 绑定挂载与递归只读限制](https://docs.docker.com/engine/storage/bind-mounts/)。

按第 3、6 节检查镜像参数、宿主身份、两次有效采样、Prometheus 抓取和资源成本。
