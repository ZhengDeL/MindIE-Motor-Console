# 插件 JSON Schema 与类型合同

- [`manifest.schema.json`](manifest.schema.json)：离线源码包的 Manifest，和 `backend.plugin_host.packages.manifest_schema()` 同步。
- [`status-table.schema.json`](status-table.schema.json)：声明式页面响应，和 `backend.plugin_host.sdk.status_table_schema()` 同步。

上述 Python 模块名适用于运行容器；从仓库根运行源码时，增加 `web.` 前缀。Python 工厂按结构化 Context 合同编写，可在类型检查阶段参考 [`CommunityPluginContextV1`](../../backend/plugin_host/sdk.py)，不需要在运行时导入宿主内部模块。

Schema 用于编辑器及离线结构检查。路径归属、保留 ID、版本/依赖图、导航与路由关联、列名唯一、总字节预算等跨字段规则仍由宿主和渲染器校验，不能把 JSON Schema 通过当成代码安全或完整兼容证明。

本公开合同自平台 `0.11.0` 起提供。检查入口是已登录会话下的 `/api/plugin-sdk`，会返回 API 版本、能力标识、可用服务和 Schema。先确认所需的 `community-import-v1`、`http-routes-v1`、`status-table-v1`，再依据[开发指南](../../PLUGIN_DEVELOPMENT.md)构建、检查和导入[完整示例](../examples/community-status/README.md)。包的最低/最高宿主范围独立校验，不只按 API 1 或软件版本字符串判断。不依赖在线 Schema 引用解析，所有包字段定义均在本地文件中。

维护者变更合同后需比较静态文件与运行时函数的输出，并分别验证 Schema 结构和实际包/页面验证器。新扩展不能通过添加未知字段绕过 API 版本或兼容性协商。
