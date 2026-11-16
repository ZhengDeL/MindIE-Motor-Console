/** @typedef {import('@mindie-motor/mcp-sdk').Toolset} Toolset */
/** @typedef {import('@mindie-motor/host-sdk').Plugin} Plugin */

/** A self-contained contribution using only the public tool and Control contracts. @type {Toolset} */
const toolset = {
  tools: [{
    name: "service_configuration_summary",
    description: "Summarize a persisted service configuration. This reports Control state, not a fresh runtime probe or proof of model readiness.",
    requiredScopes: ["services:read"],
    annotations: { readOnlyHint: true, idempotentHint: true, destructiveHint: false, openWorldHint: false },
    inputSchema: {
      type: "object",
      properties: { service_id: { type: "string", pattern: "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$" } },
      required: ["service_id"], additionalProperties: false,
    },
    async execute(context, args) {
      if (typeof args.service_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(args.service_id)) {
        throw new Error("A valid service_id is required");
      }
      const service = await context.control.request(context, { method: "GET", path: `/api/services/${encodeURIComponent(args.service_id)}` });
      if (!service || typeof service !== "object" || Array.isArray(service)) throw new Error("Control returned no service record");
      const summary = {
        service_id: args.service_id,
        name: typeof service.name === "string" ? service.name : null,
        runtime: typeof service.runtime === "string" ? service.runtime : null,
        control_status: typeof service.status === "string" ? service.status : null,
        configuration_revision: typeof service.config_revision === "number" ? service.config_revision : null,
        needs_rebuild: typeof service.needs_rebuild === "boolean" ? service.needs_rebuild : null,
        resources_may_exist: typeof service.resources_may_exist === "boolean" ? service.resources_may_exist : null,
        evidence: { source: "Control service record", runtime_health_verified: false },
      };
      return { content: [{ type: "text", text: JSON.stringify(summary) }], structuredContent: summary };
    },
  }],
};

/** @type {Plugin} */
export default {
  activate(context) {
    context.consume("console.mcp.control");
    context.provide("console.mcp.toolsets", toolset);
  },
};
