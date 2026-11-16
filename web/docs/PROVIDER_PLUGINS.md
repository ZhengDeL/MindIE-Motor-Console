# 可替换提供者与部署

本页对应 0.12.0。源码发行、镜像构建和服务器更新分别管理；本页不表示已有服务器完成升级或真实 NPU/Kubernetes 验收。模型的原生生命周期与推理流量保持独立。

## 1. 三个执行边界

| 边界 | 实际宿主与 SDK | 默认实现与替换方式 |
| --- | --- | --- |
| 进程与 HTTP 入口 | [独立 Node SDK](../../packages/node-sdk/README.md)、[Node 宿主指南](../../platform/README.md) | HTTP transport 和 Python bridge 都是普通 ESM 包；装配文件精确选择契约提供者，可替换成其他受信 HTTP 处理器。 |
| Python 业务与 Agent | [独立 Python SDK](../../packages/python-sdk/README.md)、[提供者宿主契约](../backend/provider_host/README.md) | 存储、安全、业务、资源、Motor/运行时与 API 模块从绑定解析，具体接口见[可执行目录](../backend/provider_host/catalogue.py)。 |
| 浏览器 | [独立 UI SDK](../../packages/ui-sdk/README.md)、[UI 插槽契约](../frontend/sdk/README.md) | 外壳、页面、组件与设计系统使用显式插槽；可导入独立 UI 制品。 |

这些合同分别面向 Node、Python 和浏览器，格式及版本不互换。默认与第三方在各自宿主中使用同一公开合同；Node 的原始 HTTP 转发不会把 Motor JSON 解析成 JavaScript 数值。

宿主保留制品校验、接口解析、装配顺序、生命周期、请求强制检查及诊断机制。Python 根 ASGI 装配属于宿主机制，不接受社区覆盖；具体的身份、会话、权限、审计和业务处理器可替换。没有身份/授权提供者不等于免认证。替换模块也不允许越过其他 API 合同的路由归属。

## 2. Python 提供者装配

`MOTOR_PROVIDER_ASSEMBLY_FILE` 是部署侧的绝对 JSON 文件路径，独立于选定数据库。省略时使用参考绑定；设置时先检查整个候选图，再打开存储。它不是一个任意 Python 搜索目录，也不自动扫描环境中的 entry points。

```json
{
  "schemaVersion": 1,
  "providers": [],
  "bindings": {}
}
```

空覆盖使用参考提供者。第三方使用自己的 ID，并在 `providers` 中填写真实导入描述符，随后在 `bindings` 为相应契约选择精确版本。不能只修改显示名称、冒用 `reference.*` ID，或向一份空数组示例填入虚构摘要。

可替换范围见[Python 公开契约目录](../PLUGIN_EXTENSION_POINTS.md#python-公开合同)，实际方法、属性、输入和可选性以 SDK 与可执行目录为准。存储实现须满足完整 Repository/UoW、条件写、审计原子性及备份恢复契约，不能仅提供 SQL 连接。数据迁移由持有数据的提供者声明；选择新数据库不会自动复制原数据库，也不自动获得高可用。

### 导入 `.mcprovider`

该有界格式包含 `manifest.json`、一个自包含的 `provider.py`、`README.md` 和 `LICENSE`。不运行安装钩子，不自动安装第三方依赖；依赖须经过审查后预装到运行镜像。它适合分发使用公开 Python SDK 的提供者；更复杂的代码分发可以通过已审查镜像提供，但入口模块及依赖仍由部署者负责固定。

在仓库根目录执行，输出选择已有且受控的私有目录：

```sh
python web/scripts/provider_packages.py build \
  web/plugins/examples/provider-maintenance /secure/maintenance.mcprovider
python web/scripts/provider_packages.py inspect /secure/maintenance.mcprovider
python web/scripts/provider_packages.py import /secure/maintenance.mcprovider \
  --directory /opt/motor-console/provider-artifacts
```

导入返回 `installed_not_selected: true`、制品摘要与提供者描述符，`trust_code` 默认为 false。审查来源、许可、声明及实际代码后，显式选择 true 并加入候选装配文件。容器内的 `module_file` 必须真实可读；只读挂载同一制品目录，不能使用另一台机器或 Windows 本机的路径。

标准 Control 的宿主 `${MOTOR_DATA_DIR}/config` 已只读挂载为 `/run/motor-config`。在部署宿主导入时，使用 `--runtime-root` 生成容器可读的模块路径，例如：

```sh
python web/scripts/provider_packages.py import /secure/maintenance.mcprovider \
  --directory /var/lib/motor-console/config/providers/artifacts \
  --runtime-root /run/motor-config/providers/artifacts
```

将审核后的装配文件保存在同一宿主配置目录的 `providers/assembly.json`，在环境文件设置 `MOTOR_CONTROL_PROVIDER_ASSEMBLY_FILE=/run/motor-config/providers/assembly.json`。Agent 使用自己的独立装配：标准 profiles 挂载中保存制品及 JSON，并设置 `MOTOR_AGENT_PROVIDER_ASSEMBLY_FILE=/opt/motor-profiles/providers/assembly.json`。不要把 Control 的代码选择自动下发为 Agent 的执行权限。

`--runtime-root` 只改变描述符的路径前缀，不增加挂载，也不修改导入文件。非标准 Compose 必须显式提供同样的只读挂载和进程读取权限。离线验证在容器内或路径一致的环境执行；宿主不存在 `/run/motor-config` 时，不应把宿主校验失败误判为代码兼容失败。

```sh
python web/scripts/provider_packages.py validate /secure/candidate.json
```

该命令只验证，不运行工厂。保存选择使用当前目标文件的真实 SHA256：

```sh
python web/scripts/provider_packages.py select /secure/candidate.json \
  --output /secure/assembly.json \
  --expected-sha256 REPLACE_WITH_CURRENT_FILE_SHA256 \
  --backup-file /secure/assembly.before-change.json
```

首次创建目标时，前置摘要是空字节的 SHA256；它不是任意占位符。目标已有时先计算实际字节摘要，不用旧值盲重试。备份必须是新文件。只有重启对应 API/Agent 才会采用新图；保存成功不代表已加载。

### 恢复与未决操作

保存旧制品、旧装配、独立密钥、数据库一致性备份及 Agent 账本。`provider-generation.json` 和 Agent 副作用回执不放进业务数据库：存在结果未知的操作或受管资源时，阻止盲目更换关键存储/执行提供者。任务记录实际提供者代次与执行绑定，重启后不自动重放。

更换 UI、普通 API 或其他非关键绑定不应误判为更换执行器；更换关键提供者仍需按原记录对账。原代码回退不撤销已经发往 Docker/K8s 的操作，也不会回滚插件自有的数据迁移。不要通过删除回执、复制另一安装的数据库或清空账本来绕过检查。

损坏的装配 JSON 仍可通过 `select` 的原始字节摘要前置条件替换成已验证的旧候选，并保留原字节备份。先停止对应管理进程，再离线检查与恢复；不要暴露一个免认证的恢复管理接口。

## 3. 完整 UI 制品

“系统设置 → 插件管理”提供 UI 制品导入、摘要/版本/插槽检查、显式信任及下次启动选择。一个包可以提供整个应用，也可以只替换一个页面、图标包或按钮/动作实现；不是每个按钮都要单独安装一个包。

可替换层次见[完整 UI 扩展目录](../PLUGIN_EXTENSION_POINTS.md#完整-ui-替换)，具体 props、动作与生命周期见[浏览器 UI 契约](../frontend/sdk/README.md)。

默认包也经过同一绑定和生命周期检查；完全未选择默认包时，不加载其 App、外观代码和业务样式。宿主只保留启动/失败诊断所需的极小界面。

构建可运行源码示例：

```sh
python web/scripts/ui_plugins.py build \
  web/frontend/sdk/examples/operations-ui /secure/operations.mcui
python web/scripts/ui_plugins.py inspect /secure/operations.mcui
```

`.mcui` 是有界 ZIP，包含 manifest、README、LICENSE、自包含根目录 ESM 入口及允许的样式/图片/字体。浏览器使用宿主 React 单例与公开 SDK，不从第三方 CDN 加载 React，不从包入口再导入任意模块。入口经 SHA256 校验后才执行；包内资源有固定摘要地址，SDK 可读取并校验资源。UI 代码属于明确受信的浏览器代码，不是沙箱。

浏览器操作之外也可离线导入和选择：

```sh
python web/scripts/ui_plugins.py import /secure/operations.mcui \
  --data-dir /var/lib/motor-console
python web/scripts/ui_plugins.py status --data-dir /var/lib/motor-console
python web/scripts/ui_plugins.py select --data-dir /var/lib/motor-console \
  --selection /secure/ui-selection.json --expected-revision 0 --trust-code
```

`--expected-revision` 必须使用实际查询值。选择文件是 `packages`（包 ID → 真实制品摘要）与 `bindings`（插槽 → 包 ID）两个对象；空对象恢复默认选择。导入不执行脚本，选择不热替换当前页面；重启 API 后才发布新启动图。

启动图与已选静态资源允许登录前只读访问，以便登录页本身也能替换；导入、库存和选择管理仍受权限保护，写操作还需要 CSRF。未选资源、配置文件和私密提供者路径不在公共资源接口中。

正在运行的页面固定一个代次。接口写请求带 UI/宿主/提供者身份；不匹配则拒绝，保留草稿并提示刷新。前端与 API 镜像必须来自同一源码构建，内置 UI buildDigest 不匹配时拒绝混用，不回退到另一套默认页面。

### UI 离线恢复

首先停止对应管理 API，保留原文件。有效状态可按实际修订恢复；若 JSON 损坏，使用原始字节摘要：

```sh
python web/scripts/ui_plugins.py selection-hash --data-dir /var/lib/motor-console
python web/scripts/ui_plugins.py recover-default --data-dir /var/lib/motor-console \
  --expected-sha256 REPLACE_WITH_ACTUAL_SHA256 \
  --backup-file /secure/ui-selection.damaged.json
```

该命令保留损坏的原字节再提交默认选择，不依赖损坏插件执行。它不重建数据库、停止模型或删除旧制品。离线 status 只表示文件检查结果，不是对运行中进程的加载证明。

## 4. 三组件容器部署

原有独立 Web/API 部署仍可使用 Python 提供者装配。需要统一 Node 插件入口时使用 [compose.plugins.yaml](../compose.plugins.yaml)：Web → Node control → 私有 Python worker，数据库目录仍只交给 worker。

```sh
docker build -f platform/Dockerfile \
  --build-arg NODE_BUILD_IMAGE=REPLACE_WITH_REVIEWED_NODE_IMAGE \
  --build-arg NODE_RUNTIME_IMAGE=REPLACE_WITH_REVIEWED_NODE_IMAGE \
  -t registry.example.test/motor-console/host:0.12.0 .
```

Node SDK 无运行期 npm 第三方依赖，工具链使用锁文件。生产安装记录各镜像的真实摘要，确认架构、许可证及漏洞检查。镜像已包含默认传输图；自定义 Node 插件导入/固定和离线回退见[宿主手册](../../platform/README.md)。

API/Web 按[安装指南](INSTALLATION.md)从同一源码构建。新安装生成配置时，在原参数之外提供 `--plugin-host-image`：

```sh
python web/scripts/prepare_production.py \
  --project-dir /opt/motor-console/source --data-dir /var/lib/motor-console \
  --image REPLACE_WITH_REVIEWED_API_DIGEST \
  --web-image REPLACE_WITH_REVIEWED_WEB_DIGEST \
  --plugin-host-image REPLACE_WITH_REVIEWED_HOST_DIGEST \
  --web-tls-dir /secure/tls --origin https://console.example.com:19443 \
  --release-public-key /secure/publisher.pub
```

工具只准备全新目录，不覆盖现有安装，不启动服务。读取生成环境中的 Compose 文件值，先执行 `config` 校验，核对目录、证书、权限和资源预算，再启动精确项目。更新已有安装时先备份并安排管理面切换，禁止同时启动两个写者；不要重建模型、驱动或已有 Exporter 来更新控制台。

Node 镜像只运行插件宿主，不挂载数据库、Docker socket 或 NPU。默认内存/CPU 是配置预算，不能当作实测占用。实际 Linux 镜像、存储、网络、Exporter、模型和集群均需目标环境独立验收。
