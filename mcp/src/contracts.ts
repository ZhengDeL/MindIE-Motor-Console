export * from "../../packages/mcp-sdk/dist/index.js";
import type { McpContractMap } from "../../packages/mcp-sdk/dist/index.js";
declare module "../../packages/node-sdk/dist/index.js" { interface ContractMap extends McpContractMap {} }
