# 社区插件状态示例

这是一个完整的最小源码插件：`manifest.json` 描述身份、兼容版本、权限、API 和导航；`plugin.py` 的 `setup(context)` 注册实际页面数据接口。它不读取数据库、节点凭据、原生 Motor 配置或设备，不启动后台线程。

许可证为目录中的 `LICENSE`（Apache-2.0）。复制修改示例时保留原有许可证及适用版权通知，并注明修改来源；为自己的新增代码选择兼容的许可证。使用独立实现时填写其实际许可证。另需替换插件 ID、名称、作者、API 前缀、导航 endpoint 和提供的接口名。使用平台 SDK 不自动改变独立插件的许可证。

使用 Python 3.12，在平台 `0.11.0` 或对应后续基线的源码仓库根目录构建并检查。本样例插件版本保持 `1.0.0`，最低宿主版本为 `0.11.0`。目标基线的选取方法见[开发指南](../../../PLUGIN_DEVELOPMENT.md)，容器内命令模块前缀为 `backend.plugin_host.packages`：

```bash
python -m web.backend.plugin_host.packages build web/plugins/examples/community-status --output community-status-1.0.0.qhplugin
python -m web.backend.plugin_host.packages inspect community-status-1.0.0.qhplugin
```

包导入仅校验并暂存内容，不运行 `setup()`。由真实控制面管理员查看元信息和 SHA-256、明确信任代码后，选择下次启动的版本；重启控制面才会按依赖顺序装配。演示模式不执行外部插件代码。不要把首次导入、下次启动选择和当前已加载混为一谈。

目标宿主需先在已登录会话读取 `/api/plugin-sdk`，确认 `features` 包含 `community-import-v1`、`http-routes-v1`、`status-table-v1`。Manifest 的最低软件版本只是数值边界，还需满足 API 和依赖合同；不能单靠版本字符串认定兼容。

`public_info` 是宿主提供的只读映射，包含 `product_name`、`version`、`mode`、`api_version`。其中产品名称来自构建内置值，不读取可编辑的品牌覆盖。`context.mount_router()` 由宿主附加鉴权和此插件声明的访问角色；插件不能覆盖宿主认证或其他插件路径。此示例使用 `viewer` 读取权限，因此登录后的只读用户也可查看。

页面使用内置 `status-table` 渲染器。返回纯文本表格数据，不能通过此协议注入 HTML、远程 JavaScript 或任意 React 组件。成功装配后，具有访问权限的用户可在侧栏“社区插件”下打开“插件状态示例”；仅导入或仅保存下次启动选择时，当前进程不会新增此页面。

完整开发、导入、回退和贡献约定见 [插件开发指南](../../../PLUGIN_DEVELOPMENT.md)。
