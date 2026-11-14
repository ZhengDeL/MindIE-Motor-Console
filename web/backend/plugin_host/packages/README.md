# 离线源码插件包格式与存储

本文面向包工具和宿主维护者，对应平台 `0.11.0` 起提供的源码包工具与包清单 `schemaVersion: 1`。作者入门、环境准备与完整流程见[开发指南](../../../PLUGIN_DEVELOPMENT.md)。

`.qhplugin` 是有界 ZIP 源码制品。检查、导入、依赖验证和保存选择均不执行其中的 Python；宿主验证全部声明与配置后，在启动装配时执行已明确选择的工厂。包工具不安装PyPI依赖、不联网，不将包目录加入 `sys.path`；受信插件执行本身不是进程沙箱。

## 包内容与校验

根目录必须包含 `manifest.json`、非空 `LICENSE` 和 `README.md`，以及入口模块，例如 `plugin.py`。入口写作 `plugin:setup`；支持包内相对导入。源码在内存中的独占命名空间加载，不解压覆盖宿主目录。工厂和所需模块的生命周期清理由宿主 `ExitStack` 接管。

Manifest 的规范形状由 `manifest_schema()` 发布，与运行时共用字段限制及正则。以下规则仍需实际验证器检查，不能仅依靠 JSON Schema：插件 ID 不占宿主/内置 ID；API 路径属于 `/api/plugins/<id>`；导航 ID 唯一、端点有声明、权限不弱于 API；宿主及依赖版本满足完整选择图；显示文字不含 Unicode 控制或格式字符。

版本边界使用三段数字的稳定版本；不将预发布依赖版本视为同名正式版。`dependencies` 指插件 ID，不代表 PyPI 安装要求。插件使用宿主已具备的运行库；新库应在经审查的宿主构建中提供，并补充锁文件和许可证材料。

限制由 `limits.py` 定义：单包8 MiB、展开16 MiB、单成员2 MiB、128成员、64制品、32启动选择、单插件配置32 KiB、完整选择状态1 MiB。各项预算同时生效，并非保证可同时用满所有单项上限。

拒绝路径穿越、绝对路径、反斜杠及规范化前后不一致的成员名、大小写碰撞、重复成员、链接、特殊文件、加密、不支持压缩及过高压缩比。支持Python源码、声明数据和文档；Python按UTF-8进行语法编译检查，不执行入口。详细字段约束由SDK的 `manifest_schema()` 及实际验证器提供。

## 选择与存储

`PackageStore(directory)` 按 SHA-256 保存不可变包。导入先写本目录的唯一临时文件、flush/fsync，再通过原子 create-if-absent 硬链接发布，绝不覆盖已存在的制品。存储必须支持安全硬链接和进程间文件锁；不支持时明确失败，不降级为可见的半包复制。管理清单读取持有同一把锁，避免观察到发布过程的临时链接数。

`active.json` 是带修订号的下次启动选择。`activate()` / `deactivate()` 使用跨线程、跨进程锁和预期修订号比较；选择文件原子替换。选择仅修改下一次启动，不自动重启或热卸载。保留旧包，回退就是重新选择其完整摘要。包和选择文件拒绝符号链接、硬链接与异常身份；Windows 检查 reparse point。POSIX 对目录执行 fsync；Windows依赖已 flush 的文件及命名替换。

控制面启动或后续包目录访问会在同一独占锁下恢复中断发布，只识别本工具的精确 UUID 临时名。完整、正规、单链接的 stage 可恢复为**仅已导入**；发布后留下的双链接只有在本目录摘要目标与它同 inode、实际字节摘要相符时才会收尾。残缺 stage 的原字节保留到 `quarantine/<UUID>.partial`，不执行、不选择。`recovery.json` 保存至多128个恢复事件，quarantine 至多64个文件；达到限制会明确要求维护，不静默删除历史。未知临时名、符号链接、外部硬链接、其他 UID 或不一致身份不会被扫描清除。

默认启用恢复的 `PackageStore` 及相应CLI目录操作可能整理已验证的中断导入项，不能把 `list` 描述为文件系统绝对只读。`PackageStore(..., recover=False)` 不修复或移动 stage，演示环境采用此配置。CLI应与平台使用相同UID。发布恢复不改变 `active.json` 的选择；`reset-selection` 是单独的显式恢复操作。

管理列表的元数据缓存有界，并按 inode/设备号、大小、mtime、ctime 失效；每次仍检查链接状态。激活、启动和真正加载代码时始终重新读取并验证完整摘要。列表只显示配置键和规范 JSON 摘要，不回显配置值；配置文件仍是敏感本地运行状态，不应提交或作为公共目录内容。

## API 与工具

```python
from web.backend.plugin_host.packages import PackageStore

store = PackageStore("/var/lib/motor-console/plugin-packages")
record = store.import_bytes(package_bytes)  # 不执行代码
revision = store.list()["revision"]
store.activate("community-status", record["digest"],
               expected_revision=revision, trust_code=True, configuration={})
profiles = store.active_profiles()          # 不执行代码
# 宿主验证完整注册表后，由 mount 调用受信工厂；load_factory()只准备该回调。
```

`profile.manifest`、`profile.configuration` 返回副本；`profile.declaration()` 返回宿主声明，`profile.configuration_digest` 用于区分同版本的配置变化；`profile.read_resource()` 只读取包内已验证成员。`list()` 的 `selected-next-start` 不能被描述成当前进程已经加载；实际已加载状态由宿主补充。

以下命令从源码工作树根执行，要求Python3.12；容器中模块前缀改用 `backend.plugin_host.packages`。先构建与检查，再核对导入输出：

```bash
python -m web.backend.plugin_host.packages build web/plugins/examples/community-status --output /tmp/community-status.qhplugin
python -m web.backend.plugin_host.packages inspect /tmp/community-status.qhplugin
python -m web.backend.plugin_host.packages import /tmp/community-status.qhplugin --directory /var/lib/motor-console/plugin-packages
python -m web.backend.plugin_host.packages list --directory /var/lib/motor-console/plugin-packages
```

从检查结果复制完整摘要，从 `list` 复制当前 revision；下列占位符都必须替换：

```bash
python -m web.backend.plugin_host.packages activate community-status --directory /var/lib/motor-console/plugin-packages --digest SHA256_FROM_INSPECTION --expected-revision CURRENT_REVISION --trust-code-sha256 SHA256_FROM_INSPECTION
```

保存选择后按原部署方式重启控制面，再核对实际加载摘要与API。停用前重新读取revision，使用 `deactivate community-status --directory ... --expected-revision CURRENT_REVISION`；停用只影响下次启动。命令失败返回非零退出码及结构化错误，不自动重复写入或覆盖冲突修订。

宿主升级后若多个旧插件均不兼容，普通逐个停用可能因其余旧插件仍不兼容而失败。此时可以显式清空下次启动选择：

```bash
python -m web.backend.plugin_host.packages reset-selection --directory /var/lib/motor-console/plugin-packages --expected-revision CURRENT_REVISION --confirm-clear-selection
```

该命令仅验证当前 v1 选择文件结构及修订号，不读、导入或执行包代码。清空前，在同一把锁内以私有权限保留规范旧状态到 `selection-backups/<revision>-<SHA256>.json`，包含旧插件配置；重复同状态重试仅复用经过内容验证的快照。最多32个快照、每个不超过状态文件限制；遇到链接、额外文件、损坏或上限会拒绝，不清空状态也不自动删除旧备份。POSIX以0600创建文件；Windows依赖受控目录ACL。

快照核验后，清空指针、递增修订和恢复事件在一次原子写入中提交；事件仅记录快照文件名与摘要，配置值不进入HTTP或日志。插件制品保留，当前进程中的插件不会被卸载。恢复事件最多32项。此命令不会把模型或数据库退回旧状态，也不作为普通网页的一键清空功能；恢复旧选择/配置时仍应检查宿主兼容性，不能盲目覆盖运行状态。

容器中模块前缀为 `backend.plugin_host.packages`。可用 `--configuration-file` 传入本地 JSON 设置；不要把密码写入命令或公共包。构建源目录需保持纯源码和文档，不包含生成的 `__pycache__`、测试产物或密钥。构建和导入不会执行 `setup.py`、`pyproject` hooks、pip 或插件入口。
