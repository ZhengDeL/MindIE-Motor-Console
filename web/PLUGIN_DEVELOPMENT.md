# 社区插件开发指南

本文面向使用 Python 为 MindIE-Motor Console 开发可选能力的作者。社区插件可以提供受保护 HTTP API、声明式数据页、具名服务和有清理合同的后台工作。原生推理和部署仍由 MindIE-Motor 负责，插件不能绕过账号权限、资源准入或资源所有权。

本指南对应平台 `0.11.0` 起提供的插件 API 1、包清单 `schemaVersion: 1` 和 `status-table-v1` 页面。使用对应发布源码或经过审查的后续基线，保持文档、包工具及目标宿主配套。版本及获取方式见[文档入口](docs/README.md)。

在已登录会话读取 `GET /api/plugin-sdk`，确认 `features` 包含 `community-import-v1`、`http-routes-v1`、`status-table-v1`。不要只比较版本字符串或 API 版本后便上传包。

## 准备环境与源码

使用 Python 3.12。在源码仓库根执行本文的 `python -m web.backend...` 命令；容器内模块前缀是 `backend`。包构建和检查使用离线工具，不执行插件入口或安装钩子；运行插件工厂需要目标宿主已有的 FastAPI 等运行依赖，安装方法见 [Web 开发说明](README.md)。

检出文档入口所列的源码标签、分支或提交，并为自己的修改新建本地工作分支。以下显式使用 `mindie-motor-console` 作为本地目录；`SOURCE_REF` 与分支主题均需替换。向项目提交 PR 时，按[贡献指南](../CONTRIBUTING.md)先 Fork，再将克隆地址换为自己的 Fork：

```bash
git clone --no-checkout https://github.com/ZhengDeL/MindIE-Motor-Console.git mindie-motor-console
cd mindie-motor-console
git switch -c feat/YYYYMMDD-plugin-topic SOURCE_REF
git submodule update --init --recursive
```

## 构建完整示例

仓库提供 [community-status](plugins/examples/community-status/README.md)。它通过自己的 API 返回宿主公开名称、版本、模式及插件 API 版本，用于演示包、路由和页面合同，不读取节点或设备。

```text
community-status/
├── manifest.json
├── plugin.py
├── LICENSE
└── README.md
```

包必须包含作者、许可证和使用说明。构建目录只放需要发行的源码与文本资源，不放 `.env`、凭据、数据库、模型权重、节点日志、测试结果或符号链接。支持的文本类型为 `.py`、`.json`、`.md`、`.txt`、`.toml`、`.yaml`、`.yml`，以及根目录的 `LICENSE` / `NOTICE`。编译型扩展、wheel、任意二进制和前端脚本不属于此包格式。

在仓库根目录执行：

```bash
python -m web.backend.plugin_host.packages build web/plugins/examples/community-status --output community-status-1.0.0.qhplugin
python -m web.backend.plugin_host.packages inspect community-status-1.0.0.qhplugin
```

成功时输出 Manifest、文件列表和制品 SHA-256；`code_executed: false` 表示构建没有运行入口。输出文件已存在时拒绝覆盖，更新版本应使用新文件名。容器内对应命令为 `python -m backend.plugin_host.packages ...`。

主要限制如下；以目标 SDK 与实际验证器为准：

| 对象 | 限制 |
| --- | --- |
| 单个包 / 展开数据 / 单成员 | 8 MiB / 16 MiB / 2 MiB |
| ZIP 成员 | 最多128个，拒绝不安全路径、重复/大小写冲突、链接及异常压缩 |
| 包库存 / 启动选择 | 最多64个制品 / 32个插件 |
| 单插件配置 | 规范 JSON 不超过32 KiB；CLI配置输入文件也不得超过32 KiB |
| 页面导航 | 单插件最多8个；所有社区插件合计最多256个 |

## Manifest 合同

| 字段 | 含义 |
| --- | --- |
| `schemaVersion` / `api_version` | 包格式版本与宿主接口版本，当前均为 1。 |
| `id` | 以小写字母开头，仅含小写字母、数字及连字符，最多64字符；升级保持 ID，不占用宿主或内置插件 ID。 |
| `name` / `description` | 给用户看的名称和能力说明，明确数据来源及副作用。 |
| `version` | 当前插件的三段数字版本，可带预发布后缀；宿主/依赖兼容边界仅接受三段稳定数字版本。回退选择精确的已导入 SHA-256。 |
| `entrypoint` | 包内 Python 模块与同步工厂，例如 `plugin:setup`；不是 shell 命令。 |
| `license` / `author` | SPDX 形式的许可证标识及实际作者；包内仍需提供相应许可证文本。 |
| `minimum_host_version` | 接受的最早宿主版本；本仓示例为 `0.11.0`，不代替 `api_version` 与feature检查。 |
| `maximum_host_version_exclusive` | 可选的不兼容上界；存在时严格排除该版本及更高版本。 |
| `dependencies` | 直接依赖的插件 ID；缺失、循环或依赖已停用时拒绝启动。 |
| `dependency_versions` | 可选的依赖版本边界，按被依赖插件的版本校验。 |
| `routes` | 自己的 `/api/plugins/<id>` 或其下路径；`subtree` 默认 true，覆盖该路径及后代，为 false 时仅匹配该路径。无HTTP页面/接口的后台插件可用空数组。 |
| `access` | 必须显式指定 `viewer`、`operator` 或 `admin` 的最低访问角色。 |
| `navigation` | 可选的声明式页面入口，当前 `kind` 为 `status-table`；端点必须由本插件声明，导航权限不得低于API权限。 |
| `capabilities` | 可选的能力描述；声明不自动授予系统权限或安装运行依赖。 |

完整可运行字段见示例 `manifest.json`；字段错误、未知版本和重复声明会明确失败。不要通过降低权限声明来暴露管理数据。宿主会附加认证和角色检查，写操作仍遵守 CSRF、维护状态与审计边界。

## 工厂与上下文

```python
def setup(context):
    public = context.require_service("public_info")
    # 构造 APIRouter 后调用 context.mount_router(router)。
    # 如有后台组件，创建成功后立即 context.on_shutdown(component.close)。
```

社区插件按结构化 Context 合同编写，不导入 `backend.app` 等内部实现。允许的宿主服务如下，不直接提供 Store、Fleet、kubeconfig 或原生执行器：

| `require_service()` 名称 | 返回内容 |
| --- | --- |
| `public_info` | 只读公共元信息，字段见下表。 |
| `configuration` | 此次启动所选 JSON 配置的私有副本，顶层映射不可写；不随管理员后续选择自动更新。 |
| `resources` | `read_resource(path)` 函数，返回自己包内已校验文件的 bytes，不接受任意宿主路径。 |

`public_info` 字段：

| Key | 内容 |
| --- | --- |
| `product_name` | 构建内置的产品名称，不读取系统设置中的品牌覆盖。 |
| `version` | 当前宿主产品版本。 |
| `mode` | 当前控制台进程模式。 |
| `api_version` | 插件宿主 API 版本。 |

`provide(name, version, value)` 发布具名接口，依赖插件通过 `consume()` 读取精确接口版本。插件只能消费自己或显式直接依赖的提供者；不能覆盖同名接口。名字和接口版本独立于插件包版本，修改返回结构或行为时应提升接口版本并提供迁移说明。

`setup()` 应只装配资源，不在模块导入时启动线程或修改业务状态。清理函数必须是同步、有界、可重复处理关闭状态的函数；异步工厂和异步清理当前会被拒绝。线程、定时器及客户端创建后立即登记 `on_shutdown()`，保证后续装配失败也能清理。不得用无限重试、无超时等待或后台自重启掩盖失败。

Python 插件作为受信代码运行在控制面进程权限内，上下文接口不是操作系统沙箱。只在受控环境安装审查过的包，不能把角色校验或摘要检查描述为对任意第三方代码的隔离。

## 页面协议

页面导航只引用本插件 API，不接受外部脚本 URL。状态表格接口返回：

```json
{
  "title": "示例",
  "summary": "数据来源及范围",
  "columns": [{"key": "name", "label": "项目"}, {"key": "value", "label": "值"}],
  "rows": [{"name": "宿主插件 API", "value": "1"}],
  "updated_at": "2026-01-01T00:00:00+00:00"
}
```

单元格作为文本显示，不渲染 HTML，不执行表格中的 JavaScript。列 key 必须唯一，以字母开头，后续只用字母、数字、下划线、点或连字符。缺失单元格按 null 处理，界面显示“—”；不支持嵌套对象。空值、未知、陈旧、连接失败和确定的零值应分别表达，不能将监控不可用写成资源空闲。新增页面检查深浅主题、窄屏、长文本、键盘焦点、加载/失败和账号切换后的旧响应丢弃。

## 导入、选择与回退

在运行于 `control` 模式的实例中，以系统管理员登录“系统设置 → 插件管理”，导入 `.qhplugin` 文件，检查来源、作者、许可证和 SHA-256，再明确选择受信版本。导入只校验与暂存；选择只保存下次启动状态，均不会立即执行代码。演示环境只读，执行 Agent 不加载社区控制面代码。SHA-256 是内容标识，不是作者签名或代码安全证明。

CLI 具有相同的离线流程：

```bash
python -m web.backend.plugin_host.packages import community-status-1.0.0.qhplugin --directory /var/lib/motor-console/plugin-packages
python -m web.backend.plugin_host.packages list --directory /var/lib/motor-console/plugin-packages
```

检查输出中的 SHA-256 与当前 revision，然后由操作员明确选择受信代码。以下 `SHA256_FROM_INSPECTION` 和 `CURRENT_REVISION` 是必须替换的占位符：

```bash
python -m web.backend.plugin_host.packages activate community-status --directory /var/lib/motor-console/plugin-packages --digest SHA256_FROM_INSPECTION --expected-revision CURRENT_REVISION --trust-code-sha256 SHA256_FROM_INSPECTION
```

CLI目录与实例的 `MOTOR_PLUGIN_DIR` 必须指向同一存储。保存选择后，按该实例原来的 Compose 文件栈仅重启/重建控制面；不重建 Agent 或模型。重新登录检查目录的当前加载摘要、`pending_restart`、导航及实际API，再验证插件自身功能。文件系统CLI可能完成受控中断导入恢复，但不会执行插件代码。

发生 revision 冲突时重新读取、比较后再提交。界面保留当前弹窗内的JSON；关闭弹窗前自行复制需要保留的输入，不把旧修订自动覆盖到新状态。网络结果未知时先核对目录，而不是重复假定上次未成功。

默认目录为 `<MOTOR_DATA_DIR>/plugin-packages`。Compose 的 `MOTOR_PLUGIN_DIR` 是容器内路径，必须位于已有专用可写卷中；原生运行使用绝对本机路径。CLI 应与 API 使用同一 UID，不要让 root 创建的私有制品导致业务 UID 无法读取。该目录要求支持本机文件锁与安全硬链接发布；不要未经验证就放在分布式文件系统。

界面支持为新选择输入 JSON 配置，单个配置的规范 JSON 不超过32 KiB，准确限额也由包目录和 SDK 接口返回。已选同一摘要的配置编辑由 CLI 的 `--configuration-file` 完成，避免把界面空对象误覆盖为实际配置。配置是私有持久数据而非自动加密的凭据库，秘密应使用部署方受控的文件/引用；包和公共 README 不放密钥。备份应单独覆盖整个插件目录与私有选择状态，现有数据库备份不等于包含插件制品和配置。

`status-table-v1` 响应不超过 1 MiB；列数 1..32、行数不超过1000、字段名不超过64字符。单元格仅支持不超过4096字符的文本、有限且安全精度的数字、布尔值与 null；大整数应作为文本返回。标题/说明/更新时间分别不超过200/2000/100字符。`/api/plugin-sdk` 提供 Manifest 和页面响应的 JSON Schema；可参考 `backend/plugin_host/sdk.py` 的结构化类型，不依赖内部实现导入。页面不是远程 JS 或 HTML 执行入口。

宿主升级后多个旧插件同时不兼容时，普通逐个取消可能无法得到兼容的剩余选择。先备份插件目录，再按当前修订显式清空下次启动选择：

```bash
python -m web.backend.plugin_host.packages reset-selection --directory /var/lib/motor-console/plugin-packages --expected-revision CURRENT_REVISION --confirm-clear-selection
```

此操作先保存私有旧配置快照，再清空指针，不执行旧包、不删除制品、不卸载当前进程中的插件。快照达到32项或链接/内容异常时拒绝清空，需运维核对归档。中断导入会在下一次受控恢复时验证摘要和 inode，完整包仅恢复为已导入，残缺字节留在隔离目录；恢复不会自动选择代码。详见[包引擎恢复合同](backend/plugin_host/packages/README.md)。

回退时选择相同 ID 的已导入旧 SHA-256，使用新的当前 revision，再重启控制面。停用使用 `deactivate <id> --directory ... --expected-revision ...`；被其他启用插件依赖时，必须一并处理依赖关系。该流程不删除旧包、模型或业务数据库，也不自动回滚插件自己的数据迁移。需要迁移的插件必须提供单独的备份、恢复和向前/向后兼容说明。

## 提交社区贡献

1. 从维护者指定的插件主线新建分支，保持原生 Motor 固定版本；贡献独立插件通常只改自己的目录和相关文档。
2. 提交 Manifest、源码、README、LICENSE；说明数据来源、最低权限、依赖、网络/文件副作用及兼容范围。
3. 提供构建、检查、导入、下次启动选择、重启后真实 API、停用和旧版回退的实际结果。把模拟、演示、真实外部服务和 NPU 推理分开。
4. 本仓不接收测试源码、测试配置和生成证据；在本地完成合同、权限、失败清理、并发修订和界面验证，在 PR 中给出脱敏命令与结果摘要。
5. 不携带客户配置、账号、令牌、证书私钥、节点日志或模型权重；保留第三方版权和许可证，不重新许可上游代码。

源码包导入、依赖匹配和示例启动通过不等于任意插件达到生产可用。维护者按 [贡献约定](../CONTRIBUTING.md) 逐项审查；安全问题按 [安全报告约定](../SECURITY.md) 处理。
