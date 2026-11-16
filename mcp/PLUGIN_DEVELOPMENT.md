# MCP 插件开发与替换

MCP 是独立进程层。公开业务合同由 `@mindie-motor/mcp-sdk` 发布，插件生命周期由 `@mindie-motor/host-sdk` 发布，装配由原有 `@mindie/console-platform/host` 实现。参考工具集只使用这些公开 SDK，不导入 Python、Web、数据库或宿主私有实现。

| 合同 | 基数 | 责任 |
| --- | --- | --- |
| `console.mcp.server` 1.0.0 | one | 传输、官方 SDK 协议、发现、取消与有界执行 |
| `console.mcp.auth` 1.0.0 | one | 验证当前主体，检查 role/scopes/目标，读取独立下游凭据 |
| `console.mcp.control` 1.0.0 | one | 固定 Control origin、每次请求身份核对、超时/大小/重定向限制 |
| `console.mcp.toolsets` 1.0.0 | many | 显式工具元数据与业务调用 |

参考包分开装配：`reference.mcp.inventory`、`reference.mcp.services` 为 query 域，`reference.mcp.configuration` 为配置域，`reference.mcp.operations` 为运行操作域。移除某域时同时调整 `selected` 与 `bindings["console.mcp.toolsets"]`；仅导入或列在 `packages` 中的包不会执行。重复工具名、依赖缺失、版本冲突或摘要不匹配会拒绝启动，没有隐式参考实现兜底。

可运行的独立示例见 [service-summary-toolset](examples/service-summary-toolset/README.md)，它只使用公开合同读取指定服务摘要。独立工具集入口可写为：

```ts
import type { Plugin } from '@mindie-motor/host-sdk';
import type { ControlClient, Toolset } from '@mindie-motor/mcp-sdk';

const plugin: Plugin = {
  activate(context) {
    context.consume('console.mcp.control') as ControlClient;
    const toolset: Toolset = { tools: [{
      name: 'example_list_nodes',
      description: 'Read registered node inventory; no runtime mutation.',
      requiredScopes: ['nodes:read'],
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      annotations: { readOnlyHint: true, idempotentHint: true },
      async execute(request) {
        const data = await request.control.request(request, { method: 'GET', path: '/api/nodes' });
        const output = { data };
        return { content: [{ type: 'text', text: JSON.stringify(output) }], structuredContent: output };
      }
    }] };
    context.provide('console.mcp.toolsets', toolset);
  }
};
export default plugin;
```

该插件的 `manifest.json` 声明 `schemaVersion:1`、独立 `id` 与精确 `version`、`sdk:"^1.0.0"`、`entry:"index.js"`，提供 `console.mcp.toolsets` 1.0.0 并依赖 `console.mcp.control` `^1.0.0`；包目录还需包含 `package.json`（`type:"module"`）。编译产物必须自含业务代码；不能从未选择的参考包中相对导入实现。

`requiredScopes` 用于发现和调用授权；注解只是客户端提示。当前参考鉴权还限制 viewer 无配置/运行写权限。后端再次检查实际 role、scopes 和服务/节点目标，工具元数据不能授予原本没有的权限。受限制列表不自动过滤：后端可能直接拒绝集合查询，客户端不能把本地过滤称为后端 ACL。

每个工具使用 `request.principal`、`request.signal` 和 `request.deadline`，禁止捕获全局主体或共享 Control token。不要把 MCP Bearer token、浏览器 Cookie、调用者自填 actor 或 `requestId` 当作下游身份。参考 Control client 每次读取该主体独立 backend token，并通过 `/api/plugins/mcp/identity` 对比 id、subject、role、scopes 与目标集合；两侧登记不一致立即拒绝。

工具输出的 `structuredContent` 为 JSON 对象，与 text 表示一致。响应中的敏感值会脱敏；保存配置时原始 `user_config`、`env`、placements、额外清单的未知 JSON 字段完整透传。不要将输出中的 `[REDACTED]` 写回密钥。非有限数、unsafe integer、超出层数/字节预算的文档应拒绝，不能默默舍入或裁剪。

新运行操作必须通过后端计划与 apply 扩展。计划绑定主体、服务修订、动作/参数、提供者 generation 与有效期；apply 使用同一计划的固定幂等键。丢失回复可能代表操作已提交，错误结果中的 `outcomeUnknown` 与 `reconciliation` 必须保留，并先通过计划/job 对账。禁止自动重试写请求，也不能把任务接收当成模型 ready。

制品审查、导入与配置切换使用现有平台 CLI；MCP CLI 负责专用校验与启动，避免另建一套导入机制：

```bash
node platform/dist/cli.js inspect --source /reviewed/example-toolset
node platform/dist/cli.js import --source /reviewed/example-toolset --store /private/motor-mcp/artifacts
# 用返回的精确 id/version/root/sha256 建立新的 candidate composition。
node mcp/dist/cli.js validate --config /private/motor-mcp/candidate.json
# 先停止进程；使用当前 generation 做比较并保留新建备份。
node platform/dist/cli.js switch --config /private/motor-mcp/composition.json --candidate /private/motor-mcp/candidate.json --expected-generation CURRENT_SHA256 --backup /private/motor-mcp/before-change.json
node mcp/dist/cli.js start --config /private/motor-mcp/composition.json
```

导入只校验和暂存，切换仅改变下次启动选择。保留旧制品与配置备份；恢复也需检查当前 generation，并显式选择旧配置。插件是受信代码，不是恶意代码沙箱；替换鉴权、Control client 或 Python Session 时尤其需要审查权限边界。

扩展不要求更新 `mindie-motor/upstream`。如需改变原生适配，应先阅读固定 pin 的实际源码并单独验证；MCP 协议可用与真实 NPU/Kubernetes 部署验收是不同证据。测试、私有验收夹具和生成材料放在仓库外，不随插件提交。
