# MindIE-Motor Console 存储

控制主库通过版本化数据库插件提供 Repository 和 Unit of Work。页面只使用业务 API；账号、节点、服务、模板、任务与审计不直接接触 SQL 或驱动连接。Agent 操作账本和请求日志索引有各自生命周期，仍使用节点本地 SQLite。

## 数据库配置

默认配置无需额外数据库服务：

```dotenv
MOTOR_DATABASE_BACKEND=sqlite
MOTOR_DATA_DIR=/var/lib/motor-console
```

SQLite 文件为数据目录中的 `console.sqlite3`。使用可靠的本地持久存储，不能将同一 SQLite 文件放到网络共享上让多个控制实例同时写入。

PostgreSQL 使用独立数据库和明确的连接配置：

```dotenv
MOTOR_DATABASE_BACKEND=postgresql
MOTOR_DATABASE_URL_FILE=/run/motor-secrets/database.url
```

密钥文件存放 SQLAlchemy/psycopg 连接 URL，例如：

```text
postgresql+psycopg://motor_console:CHANGE_ME@database.example.test:5432/motor_console?sslmode=verify-full
```

示例密码和地址必须替换，特殊字符须按 URL 规则编码。部署时按实际数据库配置 CA、服务端身份和访问来源。也可使用 `MOTOR_DATABASE_URL`；统一只选内联 URL 或秘密文件之一，维护 CLI 拒绝双重配置，API 也拒绝两者值不一致。生产建议使用受限密钥文件。主机维护工具支持 `MOTOR_DATABASE_URL_HOST_FILE`，或将容器的 `/run/motor-secrets/<文件>` 映射为数据目录的 `secrets/<文件>`。

后端、驱动或连接配置不可用时启动失败，不回退到空 SQLite。数据库连接 URL 不出现在状态接口中。后端切换是维护操作，不能在运行中只改环境变量就认为数据已经迁移。

## 一致性与单写者

默认是单控制写者。SQLite 在第一条数据库写入和迁移前取得独立的内核文件锁；PostgreSQL 在专用会话上取得 advisory lock，事务也在同一连接执行。丢失会话会同时失去锁并中止对应事务，插件拒绝继续写入。选用 PostgreSQL 本身不构成控制台高可用。

资源变更、修订和关键审计使用同一 UoW。服务配置使用 `config_revision`；旧编辑提交返回冲突，后台旧快照不能覆盖较新的配置。外部 Agent/网络调用不放在数据库事务内，结果不确定的动作按原操作 ID 对账。

正式产品数据库标识为 `0x4D43534C`，当前 schema 为 2。兼容的正式 SQLite schema 1 会先做一致备份，再在租约内迁移；备份记录留在 `migration-backups`。PostgreSQL 从 schema 2 引入，不识别未发布的 PostgreSQL schema 1。产品身份不一致的数据库不会自动认领。保持原数据目录和加密密钥，不能靠改应用标识强行导入。

## 备份和恢复

SQLite 使用在线备份 API。PostgreSQL 使用 repeatable-read 一致逻辑导出，写入可独立验证的 SQLite 快照容器；快照 manifest 明确记录 `source_backend` 和实际 `method`。此容器是逻辑恢复材料，不是 PostgreSQL 物理备份。

校验包括哈希、长度、schema/产品身份、表计数及外键完整性。控制主库、应用加密密钥、部署配置和 Agent 账本分别保留；只有数据库快照不能独立恢复加密节点凭据。

全应用归档包含受控挂载的 `data/secrets`、`data/client-secrets` 和 `data/config`。容器内 `/run/motor-config` 对应 `data/config`，控制台连接 Agent 的 mTLS CA、证书与私钥也按此映射验证归档完整性；未打包或不在允许目录内的文件引用仍拒绝。

数据库命令同样使用插件。在仓库根目录、已安装项目依赖的受控环境中使用 `python -m web.backend.maintenance backup --data-dir /var/lib/motor-console --destination /backups --env-file /path/to/deployment.env`；镜像内模块名为 `backend.maintenance`。也可明确指定 `--backend postgresql --database-url-file /path/to/database.url`；不会将 PostgreSQL 配置降级为读取本地 SQLite。目标路径须提前准备并限制访问，输出快照按敏感数据保管。

`backup_console.py` 提供 AES-256-GCM 全应用归档、校验和新目录恢复演练，使用独立的备份加密密钥。正式归档扩展名为 `.mcba`。恢复演练不启动应用、Agent 或模型。生产 SQLite 以及主机 SQLite 升级工具必须使用包含 WAL-reset 修复的版本，不能跳过版本门禁。

社区插件包目录不属于数据库，也不在应用归档白名单中。使用社区导入的安装需要单独保留 `MOTOR_PLUGIN_DIR` 整体状态、包文件及匹配宿主修订，详见[独立插件备份步骤](BACKUP_RECOVERY.md#社区插件包目录单独备份)。数据库驱动插件与社区 HTTP 插件使用不同扩展合同；当前社区包导入不能直接替换数据库 bootstrap。

升级工具停机阶段通过插件写入；控制台启动后的终态先形成受控本机 HMAC 回执，由控制写者验证并原子同步计划、消费记录和审计。回执绑定产品、计划、操作、发布摘要、执行日志和实际运行版本，支持幂等消费；同步未完成时保持 `pending_sync`，不绕开写者租约。

实时恢复必须处于明确维护状态，并使用匹配版本的备份与加密密钥。当前 PostgreSQL 逻辑导入支持本版本 schema；不支持的跨 schema 降级保留维护状态并拒绝执行。不能让旧二进制直接读更新后的数据库，也不能用“容器 Running”代替恢复验收。

## 扩展数据库实现

内置实现位于 `backend/storage`，公开契约位于 `backend/ports/storage.py`。可信实现需要提供版本描述、UoW、健康、schema 检查、一致备份、恢复和关闭接口，并通过同一套事务、CAS、唯一性、审计与恢复合同。注册只允许在首次打开数据库之前完成；不从网页上传或自动安装数据库代码。

数据库插件是受信任程序，不是安全沙箱。权限、资源归属和准入继续由平台核心控制；新增数据库支持需单独发布和验收，不能仅凭驱动能连接就列为支持。
