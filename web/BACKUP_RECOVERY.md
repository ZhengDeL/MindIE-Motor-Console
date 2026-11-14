# MindIE-Motor Console 加密应用备份与离线恢复演练

本手册对应 0.11.1，说明单实例 Control 的应用归档、定时备份与新目录恢复演练。恢复包格式标识为 `MCBA`、产品身份为 `motor-console`，扩展名为 `.mcba`。不支持的产品或归档格式会被拒绝，不能通过修改扩展名或产品标识进行迁移。

此工具保护单实例 Console 的应用状态。它不更改原生 Motor，不操作业务容器，不实现控制面 HA，也不把文件存在或 SQL 可读等同于完整业务恢复。

## 内容和密钥

恢复包包含数据库插件产生的一致快照、稳定应用加密密钥、`data/secrets`、`data/client-secrets` 和 `data/config` 中允许的配置、生产 `.env.production`、所用受支持的 Compose 文件，以及 `data/profiles/<profile>/manifest.json` 逐文件列出的源码。SQLite 使用 online backup API；PostgreSQL 使用 repeatable-read 逻辑导出，保存到可独立校验的 SQLite 快照容器，不是 PostgreSQL 物理备份。归档清单记录实际后端、方法和成员。

配置文件扩展名采用白名单；符号链接、硬链接、设备文件、已知模型权重格式和非白名单材料均拒绝。外部身份源、Agent 运行状态、模型材料和社区插件包目录不在此归档范围。遇到未支持布局应修正备份设计，不能将失败或遗漏的依赖当作完整恢复材料。

默认只支持标准生产目录布局。自定义应用密钥须位于已收集的 secrets 路径，容器引用为 `/run/motor-secrets/<文件名>`；客户端 TLS 使用 `/run/motor-client` → `data/client-secrets`，配置使用 `/run/motor-config` → `data/config`。归档实际可否覆盖某个挂载，以白名单及创建结果为准，不因数据库内保存了文件路径就自动包含其内容。直接放在环境中的应用密钥随环境文件加密保存；环境和文件密钥冲突会拒绝。源配置、令牌、证书及 profile 在创建前后核对文件身份、时间、大小和内容；创建中轮换导致备份失败，不发布成功状态。

CLI 要求 Python 3.11+、所选 Console 版本锁定依赖，以及通过 WAL-reset 修复门禁的 SQLite。创建包主动检查该门槛，不能直接使用未核对的系统 Python/SQLite。建议使用与已验证生产镜像相同的受控环境；容器内执行时显式挂载项目、数据、独立密钥及目标目录，保留配置记录的相同绝对路径。定时样例中的解释器也必须满足要求，不能只创建链接到旧系统 Python 的 venv。

备份加密使用独立的外部 AES-256-GCM 密钥。该密钥文件必须在数据、项目和备份目标目录之外，Unix 权限仅允许拥有者读取，文件及父目录由当前运维账号或 root 控制，不能复用应用加密密钥。密钥不会进入包、命令输出或日志。密钥丢失不能恢复；轮换后须分别保留对应旧密钥的受控离线副本。本工具不提供密钥托管服务。

```bash
python3 web/scripts/backup_console.py keygen --key-file /etc/motor-console/backup.key
python3 web/scripts/backup_console.py create \
  --project-dir /opt/motor-console-project --data-dir /var/lib/motor-console \
  --destination /mnt/motor-console-backups/console \
  --key-file /etc/motor-console/backup.key \
  --remote-mount /mnt/motor-console-backups --require-remote
```

目录需由运维提前创建并授权；工具不会自行挂载、连接或配置远端。`--require-remote` 要求指定已挂载的网络文件系统，普通本地目录不能通过。识别支持 NFS、CIFS、Ceph、SSHFS 等已挂载文件系统；网络文件系统识别不能独立证明物理异机、异地或备份存储可靠性，仍需运维确认拓扑及服务端持久化。未指定远端挂载时可显式生成本地加密包，结果的 `off_host_verified` 为 false。此字段只记录本次已声明网络挂载的检查结果，不能替代异地灾备验收。

包以未压缩 tar 内容流式加密；默认上限为 512 MiB 材料、单文件 256 MiB、10000 文件和 4 MiB 清单。超过上限会拒绝，不截断。只有加密包解密认证、逐文件 SHA256、SQL 和应用材料检查都通过后才独占发布。旁车 JSON 及 freshness 记录只含时间、校验和、大小和安全状态。创建及校验期间有私有临时 SQL/恢复材料；目录权限为 0700、文件为 0600，处理结束自动移除。该目录及 swap/宿主磁盘仍需按敏感数据保护。

## 定时与新鲜度

`deploy/systemd` 提供motor-console-backup service、timer 和只含路径的环境示例。先安装本项目依赖的受控 Python 环境，按真实部署同步修改 service 中的解释器/项目路径、挂载条件及 `ReadWritePaths`，再安装单位文件；样例不会自动启用。默认每天 02:00 加随机延迟，错过的任务由 systemd `Persistent=true` 补执行。

该 unit 默认 `RestrictAddressFamilies=AF_UNIX`，适合本地 SQLite。远程 PostgreSQL 备份需由部署方在专用 unit 中按实际连接启用 `AF_INET`/`AF_INET6`、核对数据库 TLS/秘密文件路径和网络授权；不能不改样例就声称已支持远程数据库定时备份。更改后先运行 `systemd-analyze verify` 并执行一次真实备份与解密校验，再启用 timer。

```bash
systemctl daemon-reload
systemctl enable --now motor-console-backup.timer
systemctl status motor-console-backup.timer
python3 web/scripts/backup_console.py status --data-dir /var/lib/motor-console
```

默认 26 小时没有新成功包即为 stale；已记录的失败尝试会明确显示 failed，不用较旧成功结果掩盖错误。若状态目录本身不可写，命令仍非零退出，须结合 timer 日志告警。freshness 仅读取小型元数据，不会在健康请求内重新散列大文件；它不证明远端此刻在线或没有后来删除包。应另配运维监控检查非零退出码、备份年龄、远端存储容量和离线副本。当前不自动删除备份，存储保留周期和容量告警由运维配置，不能无限期忽略容量。

## 校验和恢复演练

```bash
python3 web/scripts/backup_console.py verify \
  --archive '/mnt/motor-console-backups/console/<恢复包>.mcba' \
  --key-file /etc/motor-console/backup.key --sha256 '<创建结果中的SHA256>'
python3 web/scripts/backup_console.py drill \
  --archive '/mnt/motor-console-backups/console/<恢复包>.mcba' \
  --key-file /etc/motor-console/backup.key \
  --target-dir /secure/motor-console/recovery-drill/new-control
```

目标目录必须尚不存在；已有目录即使为空也拒绝。Unix 目标祖先须由当前运维账号或 root 控制，拒绝不可信的共享写目录；长校验前后核对目录身份。工具不覆盖 live DB、不移动 WAL/SHM、不执行归档内命令或启动应用。解密认证在解析归档前完成；成员路径、重复条目、链接、类型、文件数、大小及逐文件 SHA256 独立校验，只提取明确允许的成员。失败后的目标可能保留部分已认证敏感材料，须按受控目录调查，不能作为成功恢复使用。

离线演练检查全表 SQL 完整性/外键/行数、账号表和角色、用包内稳定密钥真实解密已保存的节点令牌与网关 Key，并核对原生配置 JSON 的规范化 SHA256。解密值不会返回。它不验证用户密码登录、IdP、证书有效期或真实 Agent 连通；没有相应凭据时，检查数量为 0，不冒充已经验证。

## 社区插件包目录单独备份

0.11.0 正式支持 `community-import-v1` 社区插件包。本节适用于已导入包或保存选择的控制面；先在已登录会话查询 `/api/plugin-sdk` 核对实际宿主能力，见[插件宿主说明](backend/plugin_host/README.md)。未使用社区包时不需要凭空建立插件目录备份。

`MOTOR_PLUGIN_DIR` 默认位于 `/var/lib/motor-console/plugin-packages`；在标准数据卷上对应宿主 Control 数据目录的 `plugin-packages`。修改环境变量不会自动增加 Compose 挂载，外部目录必须由部署者另行绑定并核对权限。**数据库快照和 `.mcba` 应用归档均不包含这份目录。**

备份前先排空管理任务，停止唯一 Control 写者，保持 Agent 和模型独立运行；在无插件导入、删除或启停写入时，对整个实际目录制作受限、加密的文件备份，保留包文件、激活选择、恢复记录及原属主/权限，并记录其对应的 Control commit、镜像摘要和文件校验和。不要只复制一个 `.qhplugin` 或 `active.json` 就认定可以恢复全部插件状态。

恢复时先在兼容宿主的独立目录验证包摘要、宿主协议、文件权限及目录完整性；按当时匹配的数据库、应用密钥和部署配置成套恢复。不能把较新插件状态覆盖到仍在运行的旧宿主；验证失败保留原目录，恢复原配置后再启动。插件配置可能含私有连接参数，应按敏感数据处理。该文件备份不是可执行代码可信证明，恢复后仍须核对导入来源和实际插件状态。

## 完整恢复与业务切换

上线恢复还必须单独保留并核对：

- 外部备份解密密钥；包中配置所依赖的外部身份源、证书颁发/轮换设施和真实存储挂载。
- 兼容的 Console 镜像、签名发布物、部署源版本及宿主 Python/Compose/驱动环境；恢复后的 env 含旧绝对路径，须审查后适配新主机。
- 各 Agent 的数据库、归属 UID/标签、令牌、Motor 工作区和受管资源；本包只备份 Control，不保留 Agent 的执行状态。
- 支持社区包的构建对应的 `MOTOR_PLUGIN_DIR` 独立备份、宿主修订和摘要。
- 原生 Motor/引擎镜像、模型权重、NPU/驱动/固件、网络与 Kubernetes 状态、网关配置及上游业务依赖。
- 对应密钥下的实际账号登录、只读资源对账和业务验收；原主机隔离、写入租约及恢复切换需独立审批，不能同时运行两个控制面争用同一批资源。

恢复材料归执行演练的运维账号所有。上线前须按生产 Compose 中的 UID/GID 对新数据目录逐项授权；不要通过放宽整个数据目录权限绕过密钥或 profile 的访问控制。
