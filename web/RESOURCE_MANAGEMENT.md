# 擎寰平台资源管理

本手册对应 0.11.0。平台将 Docker 设备声明、硬件观测和受管分配账本分开。设备声明不是利用率，健康不等于资源可分配，Kubernetes allocatable 不是实时空闲。原生 Motor 的启动配置、实例注册和推理路由保持原有语义；本文说明平台的观测与执行边界，设备配套和性能须在目标环境验证。

## 独立安装与后续升级

首次安装使用独立 Control 和 Agent data，不通过复制其他安装的数据库、凭据或账本认领模型容器。已有 Agent 升级前停止接收新任务，确认没有运行中或结果未明操作，保留 `agent-runtime.sqlite3`、凭据、profile 与工作区；仅更新对应 Agent，不自动停止或重建模型。重新接入不等于资源归属迁移。

## 安装只读 NPU 采集

推荐在每台 Docker NPU 宿主机运行独立采集定时器。它仅执行固定的 `npu-smi info` 与 `npu-smi info -m` 查询，原子更新私有快照；不监听端口，不重置设备，不修改驱动或模型进程。Agent 通过已有的只读宿主目录读取快照，不增加 NPU 设备访问权限。Ascend 26.x 会核对调用方命名空间，采集服务保留宿主命名空间；不要自行加入 `PrivateTmp`、`ProtectSystem` 等会创建挂载命名空间的选项。采集命令固定，使用 `NoNewPrivileges`、私有文件权限、进程和内存上限约束。

从本版发布目录执行，先确认本机 `python3`、`npu-smi` 及驱动查询正常。采集器自动检查 `/usr/local/sbin/npu-smi` 与 `/usr/local/Ascend/driver/tools/npu-smi`；若实际安装位置为 `/usr/local/bin/npu-smi` 等其他路径，安装 service 前在其 `ExecStart` 末尾追加 `--binary /实际绝对路径/npu-smi`。采集器会再次核验可执行文件及其祖先目录的 root 所有权和写权限，不直接信任 shell PATH。

```sh
sudo install -d -m 0755 /opt/motor-console/collector
sudo install -m 0644 web/backend/host_npu_collector.py /opt/motor-console/collector/host_npu_collector.py
sudo install -m 0644 web/deploy/systemd/motor-npu-collector.service /etc/systemd/system/
sudo install -m 0644 web/deploy/systemd/motor-npu-collector.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start motor-npu-collector.service
sudo systemctl status motor-npu-collector.service --no-pager
sudo systemctl enable --now motor-npu-collector.timer
```

这些命令仅用于经预检确认没有同用途组件的新安装。先查看已有 unit、输出路径及归属，首次单次采集成功且快照包含当前有效数据后再启用 timer；失败时先核对路径、输出 profile 和设备身份，避免定时重复失败。

从已有同用途的采集 timer/service 迁移时，先确认其归属和用途，备份原脚本/unit，停止并禁用旧 timer，等待旧 oneshot 及其查询子进程退出后再安装新配置。名字不同的两套采集器不共享 systemd 单实例保护；不要同时启用。若新采集失败，先停新 timer，再按保留的旧配置恢复，不能让两份交替写快照。不要按通配符停止其他 Exporter。

需要将所有平台材料放在 `/opt/motor-console` 时，改用[统一目录采集 unit](deploy/qinghuan/motor-npu-collector.service)，脚本在 `/opt/motor-console/collector`，快照在 `/opt/motor-console/npu/observation.json`。这是默认 unit 的替代配置，不是第二套采集；两者使用同一正式 unit 名并择一安装到 `/etc/systemd/system`。

在 Agent 环境文件设置后，只更新 Agent 容器；使用上述专用目录时，将下面快照改为`/host/opt/motor-console/npu/observation.json`：

```dotenv
MOTOR_NPU_SNAPSHOT_FILE=/host/var/lib/motor-console/npu/observation.json
MOTOR_NPU_ADMISSION_MODE=observed
MOTOR_RESOURCE_CACHE_SECONDS=10
MOTOR_RESOURCE_TTL_SECONDS=45
```

快照格式、命令输出结构、设备身份、宿主身份和时效均须通过校验。未支持的驱动输出会报采集错误，禁止猜测解析。显式提供 Chip Phy-ID 的格式使用该字段作为 `/dev/davinciN` 的 N；Chip Logic ID 单独保留，不能假定两者相同。另有下述经过限定的单芯片兼容 profile。NPU/Chip 组合、物理设备和已报告的 PCI 信息不得冲突。明确且完整的进程查询成功才可证明“本次未发现进程”；命令失败、字段缺失都保留未知。

采集失败会写入明确的错误快照；控制面上次成功值只作为历史观测显示。排查使用 `journalctl -u motor-npu-collector.service`，不要手工改时间或数值伪造采样。卸载时先禁用本项目 timer，等待查询退出，再恢复 Agent 原环境文件并保留历史文件。

### 驱动输出 profile 与独立采集器修订

采集器按明确的输出结构识别 profile，不把所有驱动版本或 Ascend 型号合并为一种格式：

| profile | 物理设备映射与限制 |
| --- | --- |
| 显式 Chip Phy-ID 格式 | 保留原有解析和校验；使用报告中的 Chip Phy-ID，独立保留 Chip Logic ID，不增加宿主 metadata 查询 |
| `ascend910b3-25.5.2-single-chip` | 仅接受工具与驱动回显均为 25.5.2、计算型号为 910B3、四语义列映射表及已核实 MCU 行、每个 NPU 恰有一个计算 Chip 0 的格式；最多16个计算芯片。物理号取 NPU ID，逻辑号取独立的 Chip Logic ID |

旧格式的 NPU ID 使用官方 A2 物理编号语义，不从逻辑号推算，也不按行号压缩非连续设备号。[官方 ToolBox 指南](https://www.hiascend.com/doc_center/source/zh/mindcluster/70rc1/toolbox/toolboxug/MindCluster%207.0.RC1%20ToolBox%20%E7%94%A8%E6%88%B7%E6%8C%87%E5%8D%97.pdf)区分这两种编号；[官方 DCMI 参考的2.89–2.92节](https://www.hiascend.com/doc_center/source/zh/Atlas%20200I%20A2/24.1.0/re/api/Atlas%20A2%20%E6%99%BA%E8%83%BD%E8%BE%B9%E7%A1%AC%E4%BB%B6%2024.1.0%20DCMI%20API%E5%8F%82%E8%80%83.pdf)也分别定义物理/逻辑转换及 davinci 设备命名。本采集器不调用额外 DCMI 接口。

该单芯片 profile 还逐个核对真实宿主 `/dev/davinciN` 字符设备的唯一 major/minor，以及对应 `/sys/dev/char` 内核设备的 `dev`、`uevent` 名称和编号。只读每芯片两个身份小文件，每个最多4KiB，不打开字符设备、不新增 `npu-smi` 命令；major值、minor值和设备节点的用户权限不硬编码或修改。验证前后必须属于同一 boot。缺节点、普通文件或软链接伪装、重复内核身份、设备名称/编号冲突、未知型号/版本、混合或多计算芯片结构均拒绝采集。

快照可附带有界的 `collector_profile` 和 `mapping_provenance`，说明物理映射依据是官方 A2 语义及内核字符设备身份。某些驱动把字符设备注册在 sysfs 的 virtual class 目录，这不表示设备已做虚拟切分；没有 PCIe 父链时不宣称已独立交叉验证 PCIe 绑定。既有 Agent 仍按原有 schema 2 字段读取快照，不依赖这些附加说明。健康、HBM、进程覆盖、时效和资源准入继续独立校验；AICore瞬时为0不表示已释放进程或HBM占用，未知字段不会补0。

宿主采集器可以按独立脚本修订和 SHA256 交付，保持库存 schema 2 与年龄证明 schema 1 不变。升级时须分别记录 Console commit/镜像摘要、镜像内采集源码摘要、实际宿主采集脚本摘要和启用 profile；先按已审核流程验证一次真实采集，再启用定时器。单独更新宿主脚本不代表原有 Console 镜像已经包含新源码，也不改变原生 Motor 的部署逻辑。

### 采集开销与执行边界

定时器使用 `OnUnitInactiveSec=15s`：上一轮结束后再等待约 15 秒，不是每 15 秒强行启动一轮。相同 service 不会由 timer 重叠启动；同输出目录的独立 CLI 调用另有非阻塞文件锁，已有采集运行时跳过，不排队、不覆盖快照。生产环境每台宿主只部署这一份定时器，不另建并行采集目录。

| 环节 | 默认频率与预算 |
| --- | --- |
| 宿主 NPU 查询 | 每轮顺序执行一次 `npu-smi info` 和一次 `npu-smi info -m`；每条命令等待上限 10 秒，另有有界终止/管道回收时间；每条 stdout/stderr 合计最多 1 MiB |
| systemd 外层保护 | 整轮启动超时 25 秒、停止宽限 2 秒；仅清理该采集 service 的进程组；`MemoryMax=128M`、`TasksMax=16`、`Nice=10`，开启 CPU/内存记账；未配置低 CPU 配额强行节流 |
| 宿主 CPU/内存/OS | 由 Node Exporter 采集，Agent 有界读取其允许地址的指标并复用缓存；CPU 需要相邻有效样本差分。未配置、失败或缺测时不读取宿主 `/proc` 补算，也不回退到容器 OS。机器身份文件只用于 NPU 快照归属绑定 |
| 硬件快照与时效 | 原子替换的 JSON 最多 1 MiB，默认有效期 45 秒；Agent 优先读私有快照；页面刷新不额外启动宿主 NPU 命令 |

这些限制约束的是采集进程，不是模型容器。命令超时或其包装进程退出但后代仍持有输出管道时，只终止本次查询自建的进程组；不会使用采集到的业务 PID 发送信号。未知输出格式、截断输出、超时和权限失败均不能解释成 NPU 空闲或占用为 0。

`npu-smi info` 是厂商的[基本信息查询命令](https://www.hiascend.com/document/detail/zh/Atlas%20200I%20A2/24.1.0/re/npu/npusmi_007.html)，本采集器没有复位、配置、压测或模型 API 调用。查询仍会消耗少量 CPU、内存及驱动管理接口时间；只读不等于零开销，也不能保证任意故障驱动都可被用户态准时中断。上线和驱动升级后应读取该 service 的 CPU 使用量、cgroup 实际峰值内存、单轮时长及快照时效，确认与现有业务共存；没有取得读数时应记为未知，不能报告 CPU/RSS 为 0。`MemoryMax` 是资源上限，不是实测占用，响应大小也不是 Python 进程内存大小。

定时器的结束后间隔和进程组清理语义分别见 [systemd.timer](https://www.freedesktop.org/software/systemd/man/255/systemd.timer.html) 与 [systemd.kill](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)。保持现有宿主命名空间兼容性，不为增加沙箱选项而改变驱动查询行为。

## 对接官方 NPU Exporter

也可对接已部署并受信任的 Ascend NPU Exporter，版本、Docker/CRI 边界及固定官方源码参见 [NPU 采集手册](docs/DOCKER_NPU_EXPORTER.md)。在 Agent 环境设置 `MOTOR_NPU_METRICS_URL`，需要专用 CA 时设置 `MOTOR_NPU_METRICS_CA_FILE` 并只读挂载证书。配置由部署者维护，普通 API 请求不能提供任意抓取地址。

平台只读取允许的指标，限制响应大小、请求时间、标签、进程数量、单位和数值范围；保留来源与采集时间。Exporter 的设备 ID 不能直接当作 davinci 索引，需要用 `MOTOR_NPU_DEVICE_MAP_JSON` 显式映射到已核实的物理设备号。PID 命名空间未确认时显示未知。

Exporter 单次响应上限 2 MiB；连接超时 2 秒、逐次 I/O 超时 3 秒，调用方另有默认 6 秒总预算。共享有界网络循环在截止时间取消请求，慢速分块响应不会因持续有数据而不断续期；DNS/证书准备等辅助工作受固定线程容量约束，底层阻塞工作不能由 Python 强制终止。默认缓存避免随浏览器刷新重复抓取。Exporter 自己的硬件采集周期由其配置决定，不能把平台的抓取周期当作驱动查询周期。

官方 Exporter 在部分底层查询错误时可能输出空进程记录，因此其进程数 0 不构成已确认空闲。严格准入遇到缺测时会拒绝新增占用。优先使用经过核实的宿主快照，或补齐可信数据源。

## 页面、汇总与时效

控制面的后台与人工探测共享默认 3 个总采集并发（可配置 1–3），同一个节点同一时刻只允许一轮采集，不为点击刷新额外排队。人工探测最小间隔 2 秒，成功后该节点下一轮后台采集延至默认 15 秒后；列表刷新只读取已校验快照，不会每次触发全体远端查询。连接版本与快照比较防止旧响应覆盖新凭据或新观测。

本地预算已满或 Agent 返回 `429` 表示采集繁忙，不据此将节点标离线或清空上次观测；旧值仍按原有效期过期，不会因繁忙被续期。后台遇到繁忙等待 2 秒再试，不累计失败次数；真实采集失败才按节点指数退避，最长 300 秒。界面中的“在线”不能替代指标自身的新鲜度校验。

Agent 的完整库存默认缓存 10 秒、失败后间隔 3 秒再尝试；同源采集不重复执行。忙时已有快照保留原时间戳返回，没有快照则返回 `429`。部署、重启等写操作会使库存缓存失效，写入前启动的采集不能重新发布旧结果。采集可更新控制面观测和 Agent 本地资源账本；它不修改模型配置、驱动状态或业务进程。

| Agent 采集路径 | 预算与完整性保护 |
| --- | --- |
| Docker 声明与账本 | 使用独立 SDK 会话，单次 I/O 超时 2 秒；整段 8 秒软预算在每次调用前后及结束时检查。最多 4,096 个容器，使用 4,097 条摘要探测超限；仅详细读取仍存活、账本引用或摘要无法确定状态的容器，每个 ID 最多一次 inspect，跳过与当前分配无关的已停止历史容器。保留设备声明和必要归属字段，不保留无关环境变量或挂载。读取账本的 SQLite 忙等待 1 秒，不改变写操作原有等待配置；只有分配记录仍与选取查询目标时完全一致，读侧对账才能写回 |
| Kubernetes 节点列表 | 默认缓存 10 秒、失败间隔 3 秒；kubeconfig 文件状态变化使缓存失效。仅使用固定的只读 NodeList/version 请求，API 请求超时 4 秒、子进程等待 6 秒、总清理预算另计最多 2 秒，stdout/stderr 合计最多 2 MiB；同一 Agent 进程最多一条只读命令。无法回收自己的查询进程时占住名额，不继续累积新查询；完整列表最多 500 个节点，不以截断列表报告成功 |

Docker SDK 的 I/O 超时和调用间预算不能硬取消正在进行的底层系统调用，不能承诺所有异常都在绝对 8 秒内返回；缓存和单轮采集保护用于限制重复工作。Kubernetes 原有部署、启停等执行入口保持独立，不复用观测缓存或缩短原执行超时。这些措施控制采集开销，不等于已经验证模型吞吐或时延完全不受影响。

浏览器按同源控制面响应校准资源观测时钟，再用单调时间推进，避免客户端时钟偏差将新鲜指标误判为过期；这不会放宽 Agent 与控制面的后台校验，也不修改宿主时钟。离线、过期、旧协议、身份冲突和无效数据不会参与当前资源汇总；上次成功值单独展示。有效的 0 与未知不同。CPU、内存均缺测时隐藏对应列。Docker 按宿主和 daemon 身份关联去重，不把同宿主的多个接入记录重复相加。

节点列表的 Docker 资源栏展示当前有效硬件采样中的原始设备型号；混合型号分别列出已识别设备数量，不把芯片索引数量表述为整卡张数。型号缺失、采样过期或节点离线时不补造型号，也不使用历史快照替代当前采样。展示复用已有采集字段，不增加硬件查询。

Kubernetes 的 capacity/allocatable 是集群调度资源份额，不是瞬时空闲或利用率。一个 K8S 执行 Agent 的宿主硬件观测不能代表整个集群；本版不宣称已经采集全部 worker 的硬件指标。界面集群快照过期后禁止按旧状态选择节点。

### Agent 与控制面存在时钟偏差

库存GET支持请求绑定的数据年龄证明。控制面为每次请求生成随机 nonce；Agent 在返回时绑定同一 nonce，并从实际采集开始的单调计时锚点计算各组件年龄。控制面加入完整请求耗时后，只在接收时换算一次本地有效期。库存、CPU/内存、NPU 当前值、上次成功值和资源账本分别到期，缓存命中、繁忙回退和数据库读取均不会刷新采样年龄。原始来源时间单独保留，账本条目的历史事件时间不会改写。

Linux 使用包含休眠时间的 `CLOCK_BOOTTIME`。独立宿主采集器在查询前记录 boot ID 和 uptime；Agent 仅在同一启动身份、非未来 uptime、原有效期内接受持久快照。重启后旧快照不能冒充新数据。该机制不修改服务器时间、不延长 TTL、不增加硬件查询，也不从 HTTP `Date` 猜测采样时刻。

版本升级须在无未决操作时执行并保留各自数据库；Agent 必须通过 `motor-console` 产品、协议和最低版本校验。缺少年龄证明时使用严格 UTC 校验，时钟偏差可能使观测不可用，不会绕过。升级独立采集脚本后应先确认一次真实查询成功，再启用原定时器。外部 Exporter 显式提供的事件时间仍按原始时钟校验；本协议不能证明其时间同步。

年龄证明依赖已认证的 Agent 和受保护的宿主采集文件；它防止缓存重放与时钟偏差造成错误续期，不是硬件数据的密码学背书。设备身份、健康、进程、来源和严格资源准入校验继续独立生效。

## 准入、账本与回执

控制面按 Agent 组织部署步骤：同 Agent 顺序提交，不同 Agent 最多八组并行；同组前序失败或结果未确认时，余下步骤标记未执行，不再提交。容器内模型继续按 Motor 原生方式启动加载。Docker 创建与重启都在同一 Agent 的临界区内，先核验资源再执行。重启核对整个目标容器集合与其他运行资源，不会因目标组自身占用而产生假冲突，也不会跳过其他服务占用。原来的最终预检与部署锁保留。

默认 `observed` 模式要求新鲜硬件身份、设备健康、HBM/进程等可靠观测，不能把查询失败当作空闲。只有部署者明确选择 `declarations` 才使用声明兼容模式；该模式的覆盖范围会提示，不能据此声称排除了宿主进程或其他运行时占用。

持久账本记录 pending、active、uncertain、released 以及操作、服务、节点、设备、端口和容器身份。未知结果不会因超时自动释放。回执身份与分配历史各有 20,000 条保护上限，到达后拒绝新写入，需先审查证据与容量；当前不提供删除历史身份的自动清理，禁止删除 Agent 数据库绕过限制。观察到停止也不等于业务已卸载；释放必须符合实际资源核验规则。账本的排他范围是单 Agent / Docker daemon，并不锁住宿主 root 或其他控制面。

未确认启动的容器处于 `created` 或 `exited` 时，不能据此清除未决创建记录或释放资源。对账绑定实际容器 ID，结合运行证据、已完成回执、明确的停止/移除结果或该 ID 已删除的证据处理；同名重建的容器不能冒充原容器完成确认。部署和重启的最终准入仍使用独立的新鲜核验，不以观察缓存替代。

每个 Docker 写操作有稳定 `operation_id`。Agent 在执行前持久受理，同一标识与相同指纹重放只读取原结果；不同内容复用标识会冲突。进程中断后的未决操作保留为 interrupted，禁止自动重放。控制面遇到响应丢失只查询原回执，并将未知结果留作中断任务。

任务窗口的“查询回执”只读原操作；“资源对账”只检查当前受管资源。二者都不自动把模型判为可用，也不自动重复部署。真实推理需要单独验证。

## 资源布局建议

Docker 创建向导可预览集中或分散策略与每卡最低空闲 HBM 要求。候选只包含新鲜、身份无冲突、已映射、健康且进程观测可靠的设备，并扣除运行容器声明及 pending/active/uncertain 账本项。无足够可信候选时明确拒绝建议，仍允许手工审查布局。

建议保留原始 JSON，用固定 Motor 原生校验核对实例、角色、容器和端口。点击“应用到布局”才替换现有配置；预览过期须重新生成。建议不预留设备，实际部署再次准入。

这是可解释的启发式推荐，尚不是验证 HCCS/RoCE 链路、完整模型内存与运行时性能的全局最优调度器。平台不改变 Motor 自身的 rank、路由、弹性或恢复逻辑。
