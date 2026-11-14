# Docker 节点 NPU 采集与官方 Exporter 离线安装

适用范围：MindIE-Motor Console 的 NPU 观测接口。命令均在 **Linux Bash** 中执行，安装目录示例为 `/opt/motor-console/monitoring`；不要直接粘贴到 Windows CMD。已有可用采集器优先复用，不因为升级平台重复安装 Exporter、驱动或容器运行时。

平台需要可信的设备观测，并不要求每台 Docker 节点安装一套 Prometheus。以下两条路径可独立选择：

| 路径 | 适用情况 | 能力与边界 |
| --- | --- | --- |
| 平台宿主采集器，推荐用于裸 Docker | 未装 NPU Exporter，或没有 CRI，又需要平台设备展示和部署准入 | 使用已有 Python 3 标准库和驱动的只读 `npu-smi`；无需增加镜像、K8s 或 Prometheus。输出供 Agent 读取的可信快照，不提供 Prometheus `/metrics`。 |
| 官方 NPU Exporter，可选增强 | 已有 Exporter，或需要接 Prometheus/Grafana | 提供利用率、HBM、健康、进程等指标；容器归属另依赖运行时接口。本文给出离线交付和 Docker 容器启动方法，明确标出无 CRI 时的限制。 |

Node Exporter 负责主机 CPU、内存、磁盘、网络等操作系统指标，**不负责 NPU**。其安装见 [Docker Node Exporter 手册](DOCKER_NODE_EXPORTER.md)。Kubernetes 环境的 Exporter 由集群侧按官方 DaemonSet/Helm 安装，平台只接入已有服务；不在每个集群节点再推一份 Docker Exporter。

## 先理解 `containerMode=docker`

官方 v26.2.0 的 `-containerMode=docker` 参数有效，但它选择的是 **Docker 相关 CRI + containerd gRPC**，不是直接使用 Docker REST API。默认会尝试 dockershim/cri-dockerd；`/var/run/docker.sock` 不能当作 CRI 地址。仅挂载 Docker socket 不会获得完整容器归属。

核对 v26.2.0 的启动参数、容器采集器和动态配置后，没有找到可靠的“关闭 CRI、只保留硬件采集”开关。不要编造 `-containerMode=none`、`-disableCRI`，也不要用 `-platform=Telegraf` 代替，它仍会初始化容器采集。[原生启动与参数](https://github.com/Ascend/mind-cluster/blob/6cb6683c8b73d1c26341bb81a2c1cefe12a855ca/component/npu-exporter/cmd/npu-exporter/main.go)、[运行时连接实现](https://github.com/Ascend/mind-cluster/blob/6cb6683c8b73d1c26341bb81a2c1cefe12a855ca/component/npu-exporter/collector/container/runtime_ops.go)。

无 CRI 时，源码在容器解析器初始化失败后仍继续硬件采集和 HTTP 服务启动，但这不等于容器采集链就绪；部分容器采集流程可能等待、重试或产生错误日志。**裸 Docker 默认采用宿主采集器；不要为了 Exporter 给业务机强装 K8s/cri-dockerd、伪造 socket，或者把日志级别提高当成问题解决。**

## 1. 安装前检查与已有实例复用

在目标节点只读执行：

```bash
uname -m
python3 --version
docker info --format '{{json .Runtimes}}'
docker ps -a --format '{{.ID}}\t{{.Names}}\t{{.Image}}'
ss -lntp 'sport = :8082'
systemctl list-unit-files --no-pager | grep -Ei 'npu.*(collector|exporter)|(collector|exporter).*npu' || true
```

还需由集群管理员确认是否已经由 K8s 管理 NPU Exporter。若有占用 8082 的服务、已有 exporter 容器、已有宿主 collector timer，先确认归属、健康和采样周期；不要覆盖、重命名、停止，或者换一个端口再起重复硬件采集器。组件名字不同也可能调用同一驱动。

已有 Exporter 可以先在该节点读取一次：

```bash
umask 077
npu_metrics_file=$(mktemp /tmp/motor-console-npu-metrics.XXXXXX)
curl --noproxy 127.0.0.1 --fail --silent --show-error \
  --connect-timeout 2 --max-time 5 \
  http://127.0.0.1:8082/metrics --output "$npu_metrics_file"
grep -E '^npu_chip_info_(utilization|hbm_total_memory|hbm_used_memory|health_status|process_info|process_info_num)(\{| )' \
  "$npu_metrics_file"
```

HTTP 200 仅证明端点可访问。还应核对卡数、型号、PCI/设备 ID 映射、指标是否新鲜、错误值及进程 PID 的宿主归属。只在授权卡范围内查询；不要为了验证监控运行压测或更改 HCCN/驱动配置。原始指标可能含业务 PID、容器名称，应仅留在受控位置。

## 2. 裸 Docker 推荐：安装宿主只读采集器

前提：现有 Ascend 驱动和 `npu-smi` 可用，Python 3 可用，已确认没有其他 timer/手动任务并行运行同类采集。**本流程不安装或升级驱动、固件、CANN、Docker，也不修改业务容器。** 驱动不满足条件时交由节点维护方处理，不自动执行驱动安装脚本。

从可信的本平台离线发布包取 `web/backend/host_npu_collector.py`，按发布 SHA256 清单验证。下面从发布包根目录执行；已有同用途 unit 或输出时先审查复用或迁移方案，不直接覆盖。

若原安装已有同用途的采集 timer/service，先核对归属、备份旧配置、停用旧 timer，等待旧 service 及其查询进程退出，再安装新的 `motor-npu-collector`。迁移失败先停新实例，再恢复旧配置。不同名称或输出目录不能阻止重复采集，见[资源管理](../RESOURCE_MANAGEMENT.md)。

```bash
set -eu
qhmon_root=/opt/motor-console/monitoring
test ! -e "$qhmon_root/collector/host_npu_collector.py"
test ! -e /etc/systemd/system/motor-npu-collector.service
test ! -e /etc/systemd/system/motor-npu-collector.timer
install -d -o root -g root -m 0700 "$qhmon_root/collector" "$qhmon_root/npu"
install -o root -g root -m 0500 web/backend/host_npu_collector.py \
  "$qhmon_root/collector/host_npu_collector.py"
```

创建以下两个 unit；如 Python 路径不是 `/usr/bin/python3`，先核对可信可执行文件的真实路径再调整，不能填写 shell 片段。

`/etc/systemd/system/motor-npu-collector.service`：

```ini
[Unit]
Description=MindIE-Motor Console read-only Ascend NPU observation
After=local-fs.target
RequiresMountsFor=/opt/motor-console/monitoring
ConditionPathExists=/opt/motor-console/monitoring/collector/host_npu_collector.py

[Service]
Type=oneshot
User=root
Group=root
UMask=0077
ExecStart=/usr/bin/python3 /opt/motor-console/monitoring/collector/host_npu_collector.py --output /opt/motor-console/monitoring/npu/observation.json --ttl-seconds 45
TimeoutStartSec=25
TimeoutStopSec=2
KillMode=control-group
NoNewPrivileges=true
CPUAccounting=yes
MemoryAccounting=yes
MemoryMax=128M
TasksMax=16
Nice=10
```

`/etc/systemd/system/motor-npu-collector.timer`：

```ini
[Unit]
Description=MindIE-Motor Console NPU observation timer

[Timer]
OnBootSec=20s
OnUnitInactiveSec=20s
AccuracySec=1s
Unit=motor-npu-collector.service

[Install]
WantedBy=timers.target
```

先校验，再启用新组件：

```bash
systemd-analyze verify /etc/systemd/system/motor-npu-collector.service \
  /etc/systemd/system/motor-npu-collector.timer
systemctl daemon-reload
systemctl enable --now motor-npu-collector.timer
systemctl status motor-npu-collector.timer --no-pager
journalctl -u motor-npu-collector.service -n 30 --no-pager
```

查看首个自然周期的输出，并确认后续周期的 `collected_at` 持续变化。成功时要逐卡核对 `mapping_status=verified`、健康和 `processes_status`，并核对 `host_identity`、`sample_clock`、`expires_at`。采样失败写入 error/unknown，不会借文件 mtime 或旧数值伪造成功。

```bash
python3 -m json.tool /opt/motor-console/monitoring/npu/observation.json
systemctl show motor-npu-collector.service \
  -p Result -p ExecMainStatus -p CPUUsageNSec -p MemoryCurrent -p MemoryPeak
```

该脚本只调用受信任路径的 `npu-smi info`、`npu-smi info -m`；不写卡、不 reset、不读取模型权重。命令有时间/输出上限，按输出文件加非阻塞采集锁。**不同目录的两套采集器不共享锁**：迁移必须先停旧 timer、等旧 oneshot/其子进程退出，再启新 timer。不要复制旧快照并改时间，也不要给它加高频外部轮询。

Agent Compose 已将宿主 `/` 只读挂载到 `/host`。在该 Agent 自己的受控环境文件中配置：

```dotenv
MOTOR_NPU_SNAPSHOT_FILE=/host/opt/motor-console/monitoring/npu/observation.json
MOTOR_NPU_METRICS_URL=
MOTOR_NPU_ADMISSION_MODE=observed
MOTOR_RESOURCE_CACHE_SECONDS=10
MOTOR_RESOURCE_TTL_SECONDS=45
MOTOR_AGENT_READ_ONLY=true
```

按该节点的正式 Compose 项目单独重建 **Agent** 使环境生效；不要对包含模型的其他项目执行 `down`。此操作不应重建模型容器，但会短暂停止管理/采样；先排空 Agent 的未决任务。再从平台“节点管理 → 连接探测/资源详情”核对新快照来源、设备型号、HBM、真实进程和时效。只读模式验收完成后，另经资源交接再授权 managed；不要通过改成 declarations-only 绕过准入。

## 3. 官方 Exporter 的版本与权限选择

新离线制品示例固定 **`v26.2.0-openeuler24.03`、`linux/arm64`**，不使用 `latest`。官方也发布 `v26.2.0-ubuntu22.04`；架构应与宿主一致，ARM64 对应 `aarch64`。选择镜像 OS 不等于允许任意驱动/固件组合：先按设备型号和官方版本配套表核验 DCMI/驱动，不能为了通过安装重新装驱动。[官方镜像说明](https://github.com/Ascend/mind-cluster/blob/c89d7fc01b23cd75bfbb97787191c06ed94952a8/docker/npu-exporter/OVERVIEW.zh.md)。

| 项目 | v26.0.0 | v26.1.0 / v26.2.0 |
| --- | --- | --- |
| 官方镜像标签 | `v26.0.0` | 带 OS，如 `v26.2.0-openeuler24.03` |
| 配置文件目录 | `/usr/local/` | `/user/mind-cluster/npu-exporter-config/` |
| 采集周期 | 旧全局周期/旧分组 | 支持每组 intervalSeconds；有效的 updateTime 优先覆盖，默认利用率组更频繁 |
| 状态失败 | 不应统一当成健康或零 | 26.1 起部分健康/网络状态以 `-1` 表示调用失败；平台仍须保留未知 |
| 网络分组 | 旧 network 等 | 26.2 拆分 network_bandwidth/network_link；不要同时启用重复旧组 |

不能直接把26.0配置挂到26.2旧路径；不要因标签相同就认定两台镜像内容一致，最终以实际 Image ID/发布记录为准。本文 v26.2.0 参数以 release 源码 commit `6cb6683c8b73d1c26341bb81a2c1cefe12a855ca` 为参考；这不证明下载镜像与源码可复现构建一致。[官方兼容性章节](https://github.com/Ascend/mind-cluster/blob/c89d7fc01b23cd75bfbb97787191c06ed94952a8/docs/zh/scheduling/05_developer_guide/00_installation_deployment/00_manual_installation/03_npu_exporter.md#兼容性说明)。

权限与运行时边界：

- 官方容器清单使用 **root、privileged、只读根文件系统**，只读挂载驱动/DCMI。它是高权限采集组件，不能从未验证来源下载后在 UI 中无审阅一键启动。把 socket 挂为 `ro` 也不能阻止通过 socket 调用写 API。
- DCMI 动态库及父目录必须 root 所有，group/other 不可写；路径深度和 `LD_LIBRARY_PATH` 长度遵守官方限制。不要以 `chmod -R 777` 修复加载失败。
- 下面硬件观测示例显式使用 Docker 已注册的普通 `runc`，与模型的 Ascend runtime 分离；DCMI 通过既有驱动挂载和特权设备访问。先用 `docker info` 确认 runc 存在，未注册时中止，不修改 daemon 默认 runtime 或重启 Docker。该组合需独立验证实际驱动配套和运行行为，不能仅凭容器启动推定支持。
- **不默认加 `--pid=host`**。官方通用 YAML 没有要求 hostPID，特权容器也不等于宿主 PID 命名空间；读取到的进程 PID 是否为 host PID 必须独立核验后才能填 `MOTOR_NPU_PROCESS_PID_SCOPE=host`。
- Docker/CRI 完整容器指标需要已存在、受支持的 CRI 与 containerd socket。Kubernetes 1.24及以上且使用Docker时，cri-dockerd 由集群侧维护。纯 Docker 不因本文自动安装它；不存在的 socket 不要用 `-v` 自动创建目录代替。
- Atlas 200I SoC、虚拟机、Atlas 850/950 的共享模式、systemd/UMDK 等条件不同，不套用本通用示例；按对应硬件官方安装章节单独审阅。[官方安装约束](https://github.com/Ascend/mind-cluster/blob/c89d7fc01b23cd75bfbb97787191c06ed94952a8/docs/zh/scheduling/05_developer_guide/00_installation_deployment/00_manual_installation/03_npu_exporter.md#使用约束)。

## 4. 联网获取与离线传递，固定真实 Image ID

在有联网权限且具备 Docker 的**交付准备机**执行。仓库若要求认证，使用既有凭据管理/交互 `docker login`，不要将密码放进命令、制品清单或平台模板；本文不要求新增账号。拉取失败先按官方镜像权限/网络排查，不使用不明第三方替代镜像。

```bash
set -eu
qhmon_image=swr.cn-south-1.myhuaweicloud.com/ascendhub/npu-exporter:v26.2.0-openeuler24.03
qhmon_bundle=npu-exporter-v26.2.0-linux-arm64.tar
docker pull --platform linux/arm64 "$qhmon_image"
docker image inspect --format '{{.Os}}/{{.Architecture}} {{.Id}} {{json .RepoDigests}}' "$qhmon_image"
test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$qhmon_image")" = linux/arm64
docker image inspect --format '{{.Id}}' "$qhmon_image" > "$qhmon_bundle.image-id"
docker save --output "$qhmon_bundle" "$qhmon_image"
sha256sum "$qhmon_bundle" "$qhmon_bundle.image-id" > "$qhmon_bundle.sha256"
```

将 tar、image-id、sha256 三个文件经已授权通道拷到离线节点，校验清单本身的来源和发布签名/审批记录；校验和不是来源可信证明。执行：

```bash
set -eu
qhmon_bundle=npu-exporter-v26.2.0-linux-arm64.tar
sha256sum --check "$qhmon_bundle.sha256"
docker load --input "$qhmon_bundle"
qhmon_image_id=$(cat "$qhmon_bundle.image-id")
printf '%s\n' "$qhmon_image_id" | grep -Eq '^sha256:[0-9a-f]{64}$'
test "$(docker image inspect --format '{{.Id}}' "$qhmon_image_id")" = "$qhmon_image_id"
test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$qhmon_image_id")" = linux/arm64
```

后续运行用这个**真实本地 Image ID**；不填写伪造的 `repo@sha256`。`docker save/load` 后 RepoDigests 可能不保留，Image ID 与 registry manifest digest 不能混用。制品存储和哈希验证方法见 [Docker save](https://docs.docker.com/reference/cli/docker/image/save/)、[Docker load](https://docs.docker.com/reference/cli/docker/image/load/)。

可在目标架构的准备机上核对二进制版本；该命令不挂驱动/设备、不启动硬件采集：

```bash
docker run --rm --pull never --runtime runc --user 0:0 --network none --read-only \
  --entrypoint /usr/local/bin/npu-exporter "$qhmon_image_id" -version
```

预期输出应与26.2.0和aarch64配套；不匹配则停止交付。不要使用 `docker export/import` 替代 save/load，它会丢失镜像配置与分层身份。

## 5. 无 K8 的可选硬件观测容器

**本节是将官方二进制路径、参数及 YAML 驱动/权限配置映射到 Docker CLI 的安装示例，不是官方无 CRI 完整部署认证。** 未安装 CRI 时不提供 Pod/namespace/container 标签保证，可能产生运行时初始化错误；不接受这个边界就使用第2节。不要在已有 Exporter 的节点执行。首次安装先保持 `restart=no`，通过字段、日志、成本及稳定性验收后才考虑改重启策略。

在前面完成离线 load 的同一个 Bash 会话中：

```bash
set -eu
qhmon_root=/opt/motor-console/monitoring
test -n "${qhmon_image_id:-}"
test -d /usr/local/Ascend/driver
test -d /usr/local/dcmi
test ! -e "$qhmon_root/exporter/container.id"
if docker container inspect qinghuan-npu-exporter >/dev/null 2>&1; then
  echo '同名组件已存在，请先审阅归属和复用方式' >&2
  exit 1
fi
if ss -H -lnt 'sport = :8082' | grep -q .; then
  echo '8082 已被占用，禁止重复安装' >&2
  exit 1
fi
docker info --format '{{json .Runtimes}}' | grep -q '"runc"'
install -d -o root -g root -m 0750 "$qhmon_root/exporter/logs"
docker run --detach --pull never \
  --name qinghuan-npu-exporter \
  --label io.motor.component=npu-exporter \
  --label io.motor.managed-by=manual-offline-guide \
  --cidfile "$qhmon_root/exporter/container.id" \
  --runtime runc --user 0:0 --privileged --network host \
  --read-only --restart no --cpus 1 --memory 1g --pids-limit 256 \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777 \
  --mount type=bind,src=/usr/local/Ascend/driver,dst=/usr/local/Ascend/driver,readonly \
  --mount type=bind,src=/usr/local/dcmi,dst=/usr/local/dcmi,readonly \
  --mount type=bind,src=/sys,dst=/sys,readonly \
  --mount type=bind,src=/etc/localtime,dst=/etc/localtime,readonly \
  --mount type=bind,src="$qhmon_root/exporter/logs",dst=/var/log/mindx-dl/npu-exporter \
  --log-driver local --log-opt max-size=10m --log-opt max-file=3 \
  --entrypoint /usr/local/bin/npu-exporter \
  "$qhmon_image_id" \
  -ip=127.0.0.1 -port=8082 -containerMode=docker -updateTime=10 \
  -logFile=/var/log/mindx-dl/npu-exporter/npu-exporter.log \
  -logLevel=0 -maxAge=7 -maxBackups=3
```

参数来源：官方 [v26.2.0 Dockerfile](https://github.com/Ascend/mind-cluster/blob/6cb6683c8b73d1c26341bb81a2c1cefe12a855ca/docker/npu-exporter/v26.2.0/Dockerfile.openeuler) 与 [组件 YAML](https://github.com/Ascend/mind-cluster/blob/6cb6683c8b73d1c26341bb81a2c1cefe12a855ca/component/npu-exporter/build/npu-exporter.yaml)。本例单独覆盖 entrypoint，是因为镜像默认入口还会执行协议展示 shell；组织应先完成官方软件许可审阅。日志目录、回环绑定、资源上限、10秒全局周期、runc 和 Docker 日志轮转是本平台的运行建议，需在目标机验收，不声称来自官方一条现成 `docker run`。

该示例不挂 Docker/CRI socket，仅用于硬件观测。需要完整运行时归属且宿主**已经具备并维护** CRI 时，由管理员先验证 CRI 和 OCI gRPC 地址及权限，再按官方模板增挂真实 socket 所在目录并显式设置 `-endpoint=unix:///实际CRI.sock`、`-containerd=unix:///实际containerd.sock`；不能把 Docker REST socket 填入任一参数。挂载目录会扩大容器权限，应单独审批。不要盲目同时挂载 `/run`、`/var/run` 或多个无关运行时目录。

host-network 下监听回环地址，适用于同宿主 host-network Agent。桥接网络中的 `127.0.0.1` 指向它自己的容器，不能直接照抄；需要另行设计受控网络或代理。Prometheus 在另一台主机时，应经已有受控 TLS 代理/网络策略开放监控入口，不把 `-ip=0.0.0.0` 当成默认。监听端口不会因为 `--privileged` 自动获得认证。

## 6. 验证字段、时效与实际开销

```bash
docker inspect --format '{{.State.Status}} {{.State.ExitCode}} {{.Image}}' qinghuan-npu-exporter
docker logs --tail 30 qinghuan-npu-exporter
tail -n 50 /opt/motor-console/monitoring/exporter/logs/npu-exporter.log
docker stats --no-stream qinghuan-npu-exporter
umask 077
npu_metrics_file=$(mktemp /tmp/motor-console-npu-metrics.XXXXXX)
curl --noproxy 127.0.0.1 --fail --silent --show-error \
  --connect-timeout 2 --max-time 5 http://127.0.0.1:8082/metrics \
  --output "$npu_metrics_file"
```

逐项验收：

| 数据 | 需要核对的证据 |
| --- | --- |
| 型号/卡数/身份 | `model_name`、`id`、PCI等身份与宿主授权设备一致；不能按发现顺序猜物理卡号 |
| 利用率 | `npu_chip_info_utilization` 在合理百分比范围；缺失不是0 |
| HBM | `npu_chip_info_hbm_total_memory` / `npu_chip_info_hbm_used_memory` 的单位、范围和设备映射；平台按适配字段解析，不把不同单位直接拼接 |
| 健康 | `npu_chip_info_health_status` 的实际枚举；`-1`/调用失败不可显示健康 |
| 进程 | `npu_chip_info_process_info` / `npu_chip_info_process_info_num`，核 PID 与宿主 `/proc`/容器归属；`process_id`、卡ID不能盲信命名空间 |
| 时效 | 两次自然采集周期后指标实际刷新，观察 Exporter 文件日志中的DCMI/CRI错误；HTTP返回时间不等于芯片采样时间 |
| 成本 | 稳态 CPU/RSS/日志增长、原模型健康与延迟；1CPU/1GiB是本例限制，不是实测使用量。无需故意制造业务负载或故障 |

没有任意一个字段时只展示已确认项或保留不可用状态，不把空值、陈旧样本填0。持续 CRI 错误/阻塞或采集异常时，不应靠关闭错误日志上线；停止**按本手册新装**的 Exporter 并回到宿主采集器路径。已有 Exporter 的变更需独立维护窗口，不能按本节删除。

## 7. Agent 接入已有 Exporter

若只用于观测、尚未确认设备映射和 PID 命名空间：

```dotenv
MOTOR_NPU_METRICS_URL=http://127.0.0.1:8082/metrics
MOTOR_NPU_METRICS_ALLOWED_HOSTS=127.0.0.1
MOTOR_NPU_DEVICE_MAP_JSON={}
MOTOR_NPU_PROCESS_PID_SCOPE=unknown
MOTOR_NPU_ADMISSION_MODE=observed
MOTOR_RESOURCE_CACHE_SECONDS=30
MOTOR_RESOURCE_TTL_SECONDS=45
MOTOR_AGENT_READ_ONLY=true
```

显式映射的键是 Exporter `id`，值是已验证的宿主物理设备ID；例如只有两张卡且核验后确实为同号，才使用 `{"0":0,"1":1}`。不要把本例当成通用A3映射，也不要因为容器用了host网络就把 `PID_SCOPE` 改为host。非回环地址必须加入精确 `MOTOR_NPU_METRICS_ALLOWED_HOSTS`；需要认证/TLS时通过现有秘密文件与CA配置，不把token写进URL或共享文档。

如果同时配置可信 `MOTOR_NPU_SNAPSHOT_FILE`，Agent 优先读取有效宿主快照，只有快照不可用时才尝试 Exporter；两者不会合并成一份虚假的完整采样。不要为每次网页刷新直接运行一遍 npu-smi：网页读取 Agent 缓存，采集器按完成后周期采集。

**部署准入还需要可靠的“空进程”证据。** 某些驱动/DCMI失败也可能表现为 Exporter进程数量0；平台有意将这种空结果保留为 unavailable。已有非空PID可辅助观测，并不证明后来清空后的零可信。宿主采集器只有在命令成功、全设备覆盖与输出格式验证通过时，才把空进程列表标为ready。卡被其他容器声明/实际进程占用、健康异常、映射未知、采样过期时继续拒绝部署，不切 declarations-only 绕过。

## 8. 仅卸载按本手册新装的组件

Exporter 安装命令用独立名称、标签和 `container.id` 记录身份。卸载前确认它就是本次安装的容器，不适用于已有 `npu-exporter` 或K8 DaemonSet：

```bash
set -eu
qhmon_root=/opt/motor-console/monitoring
qhmon_container_id=$(cat "$qhmon_root/exporter/container.id")
test "$(docker inspect --format '{{.Id}}' "$qhmon_container_id")" = "$qhmon_container_id"
test "$(docker inspect --format '{{index .Config.Labels "io.motor.managed-by"}}' "$qhmon_container_id")" = manual-offline-guide
test "$(docker inspect --format '{{.Name}}' "$qhmon_container_id")" = /qinghuan-npu-exporter
docker stop --time 20 "$qhmon_container_id"
docker rm "$qhmon_container_id"
```

保留日志、制品哈希、镜像和身份记录供复盘，不执行 `docker system prune`、`docker image prune` 或递归删除共享目录。Agent 若仍指向该 Exporter，应改为其他已验收来源或明确停用该观测项；保留观测未知，不能以缓存充当在线指标。

宿主采集器卸载也只针对本次安装的 `motor-npu-collector.timer/service`：确认归属后停止并disable timer，等待oneshot和其子进程退出，再停用其快照配置。默认保留unit、快照和日志证据；不按名称通配符停止其他 collector，不删除驱动，也不触碰原 Exporter。

## 资料与验收边界

- [官方手动安装文档](https://github.com/Ascend/mind-cluster/blob/c89d7fc01b23cd75bfbb97787191c06ed94952a8/docs/zh/scheduling/05_developer_guide/00_installation_deployment/00_manual_installation/03_npu_exporter.md)：驱动/DCMI、配置迁移、采集周期和部署限制。
- [官方组件 README](https://github.com/Ascend/mind-cluster/blob/c89d7fc01b23cd75bfbb97787191c06ed94952a8/component/npu-exporter/README.md)：硬件DCMI/hccn_tool与容器CRI的分层。
- [v26.2.0 发布源码](https://github.com/Ascend/mind-cluster/tree/6cb6683c8b73d1c26341bb81a2c1cefe12a855ca/component/npu-exporter)；[v26.0.0 对照源码](https://github.com/Ascend/mind-cluster/tree/2fa1c4f06738892c2cb8f9262bd30fb03405ffcd/component/npu-exporter)。官方 GitCode 原地址为相同仓库和相对路径；文中链接使用 Ascend 官方 GitHub 镜像固定 commit，避免上游分支更新使示例含义漂移。
- [Docker run 参数](https://docs.docker.com/reference/cli/docker/container/run/)：runtime、挂载、进程/网络命名空间和资源限制。

安装后检查实际指标、设备映射、PID 命名空间、runc 与驱动配套、无 CRI 时的日志和稳定性，以及采集资源成本。平台不提供跳过这些检查的高权限一键安装。
