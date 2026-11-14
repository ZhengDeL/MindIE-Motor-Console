# 插件宿主 API 1

本文面向维护插件宿主和内置提供者的开发者，说明声明、依赖、路由归属、接口和生命周期。社区包作者使用[公开开发指南](../../PLUGIN_DEVELOPMENT.md)及[最小示例](../../plugins/examples/community-status/README.md)，无需导入宿主内部模块。

内置插件随平台制品安装。社区源码包由控制面管理员导入并明确选择，重启后才装配；导入不执行代码，不自动发现或执行任意已安装包入口。宿主持续负责认证、授权、审计、资源所有权和部署准入。

## 启动配置

`build_registry()` 读取逗号分隔的 `MOTOR_DISABLED_PLUGINS`。未知 ID、重复 ID、循环或缺失依赖、停用当前存储驱动、启用插件依赖已停用插件，均使启动失败。不会静默启用插件、自动级联停用或切换数据库。

例如只关闭 Grafana 连接入口与品牌配置：

```text
MOTOR_DISABLED_PLUGINS=grafana,branding
```

关闭 Docker 运行时也必须关闭依赖它的镜像管理：

```text
MOTOR_DISABLED_PLUGINS=motor-docker,images
```

重启控制台使装配配置生效。改变插件配置不等于停止已有模型或删除业务数据；停用运行时后，应先恢复插件再管理该运行时已有资源。

## 清单与运行状态

`describe()` 返回 `schemaVersion: 1`、`api_version: 1`、确定性的 `digest` 及清单。摘要由不含摘要字段的规范 JSON 计算；清单按 ID 排序。`enabled` 表示当前进程允许装配该插件，不表示外部 Grafana、CCAE、数据库服务或模型通过验收。

`active_storage_plugin` 单独标明选中的数据库驱动。没有选中的数据库驱动不会因显示在清单中而被建立连接。存储健康与 schema 版本仍使用现有存储合同检查。

清单的 `pages` 使用已有页面 ID，系统设置是宿主保留的恢复入口。关闭操作审计页面不会停止审计写入；关闭账号管理页面不会关闭登录、会话和权限校验。插件不得声明接管这些宿主保留端点。

## 内置提供者装配

以下为宿主装配片段，`application`、`store` 和 `mutation_lock` 由应用创建；不是可直接执行的独立插件模板。容器中源码模块前缀由 `web.backend` 改为 `backend`。

```python
from contextlib import ExitStack
from web.backend.plugin_host import build_registry, PluginContext
from web.backend.plugin_host.providers import mount_branding, default_branding

plugins = build_registry(storage_backend="sqlite")
with ExitStack() as application_cleanup:
    context = PluginContext(application=application,
        services={"store": store, "mutation_lock": mutation_lock},
        registry=plugins, cleanup=application_cleanup)
    # 工厂来自已审查的宿主装配代码，不能接受 HTTP 提供的模块名。
    manager = plugins.mount("branding", mount_branding, context, application_cleanup)
    public_branding = manager.public() if manager is not None else default_branding()
    plugins.seal()
    # 把 application_cleanup 的存活期交给应用 lifespan。
```

工厂接受宿主提供的 Context，可返回普通值、同步 context manager 或 `PluginInstance(value, close)`。当前API只接受同步装配和清理，异步工厂、异步 context manager 与异步清理会明确拒绝。停用插件不执行工厂；同一插件不能重复装配。工厂异常时启动失败并清理已创建资源，不自动重复未知副作用；`seal()` 后禁止继续装配。

内置 `PluginContext.services` 是宿主显式注入的只读映射，不向前端导出，也不等同社区 Context 的公开服务列表。内置工厂接收的 Context 自动绑定当前插件 ID：

- `require_service("store")` 取得明确的宿主服务；缺失或指定类型不符使装配失败。
- `provide("branding.manager", 1, manager)` 注册版本化接口；禁止重名覆盖和不同插件争用同名接口。
- `consume("branding.manager", 1)` 精确匹配接口版本；业务插件只能消费自己或直接声明依赖的插件所提供的接口。宿主可以汇总已提供的接口，不进行静默版本降级。
- `mount_router(router)` 验证所有路由属于当前插件；不能通过追加前缀改挂到身份、宿主或别的插件路径。
- `on_shutdown(worker.close)` 把线程、连接等清理注册给宿主 `ExitStack`；后续装配失败和应用正常退出都执行清理。任务应自行实现有界终止，不依赖守护线程自动消失。

`providers.mount_branding` 是实际业务工厂：创建原有 `BrandingManager`、挂载真实品牌 API，并提供 `branding.manager` v1。关闭该插件不读已保存品牌，宿主 `default_branding()` 返回默认名称、副标题、空图标和修订号 0，不删除原有配置。

宿主内部新增插件可通过受信启动代码向 `extra_manifests` 显式传入，或直接构造 `PluginRegistry`。社区插件通过独立包管理器选择的清单合并到同一依赖图，完成版本、ID、路径和依赖检查后才按拓扑导入受信工厂。新增数据库声明还必须有现有 `storage.registry` 中注册的实际驱动；普通社区源码包并不自动获得替换当前数据库的能力，声明也不是安装动作或连接验证。

## 社区包合同

公开的开发流程和完整示例见 [PLUGIN_DEVELOPMENT.md](../../PLUGIN_DEVELOPMENT.md)。包包含 `manifest.json`、源码、`README.md` 与 `LICENSE`；`entrypoint` 指定包内的同步工厂。工厂不依赖内部后端模块，外部上下文公开只读 `public_info`、本次启动的私有 `configuration` 副本，以及读取自己包内资源的 `resources` 函数，通过受保护的自有 API 和 `status-table` 页面贡献能力。这些服务不包含宿主数据库、Docker socket 或任意文件访问；完整范围见[扩展面](../../PLUGIN_EXTENSION_POINTS.md)。

真实控制面管理员的导入仅校验并保存按 SHA-256 标识的不可变制品；选择操作带当前 revision 和明确的代码信任确认，写入下次启动配置。当前加载与下次启动选择分别显示。旧摘要保留供回退，停用不删除模型和数据库。演示模式不执行外部插件代码。

社区包合同自平台 `0.11.0` 起提供。检查目标宿主时，应在已登录会话读取 `/api/plugin-sdk`，确认 `features` 包含 `community-import-v1`、`http-routes-v1`、`status-table-v1`。宿主 API、软件版本和包的兼容边界是独立维度；不自动放宽第三方包的最高宿主版本限制。

包摘要校验不是发布者签名，受信 Python 代码也不是沙箱。只有被维护者审查并由部署侧明确选择的代码才应装配；包自己的数据迁移、外部服务、线程终止和升级回退仍需单独验证。

## API 归属

`owner_for_path()` 使用完整路径段匹配和受控占位符；更具体的声明优先。例如服务日志归 `runtime-logs`，而服务重启归 `services`。不能用字符串前缀把 `/api/nodes-extra` 误判为 `/api/nodes`。重复声明和同等优先级歧义在启动时拒绝。

归属用于装配和停用检查，不能代替角色校验。HTTP 中间件还必须执行认证、授权、CSRF 和审计；服务操作必须依据服务自己的 runtime 执行 `require("motor-docker")` 或 `require("motor-kubernetes")`，不能仅根据 URL 推断运行时。

`/agent/inventory` 是宿主跨运行时采集入口，不归 Docker 插件独占；Kubernetes 单运行时部署仍需要该入口。`/api/extensions` 是宿主的集成目录，按已启用插件过滤。CCAE 当前使用原生配置通道，不虚构独立控制端点。

部分服务与系统接口由宿主协调后注册。声明存在不代表提供者可独立发行或被进程隔离；新增提供者需要实际装配、调用方及相应运行验证，参见[扩展接口设计](../../PLUGIN_EXTENSION_POINTS.md)。
