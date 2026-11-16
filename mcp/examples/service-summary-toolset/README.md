# Service configuration summary toolset

This independent reference contribution adds `service_configuration_summary`
using the public `@mindie-motor/host-sdk` and `@mindie-motor/mcp-sdk` contracts.
It imports no Console business implementation and needs no build step. Its
JSDoc types can be checked in an external developer project with the public SDK
tarballs installed.

Inspect and import this directory with the existing platform CLI, then add its
exact imported package record to a candidate MCP composition. Select its ID and
append it to the `console.mcp.toolsets` binding. Pin and validate the complete
candidate before switching the next-start configuration. See the MCP plugin
development guide for the complete import and recovery procedure.

The tool requires `services:read` and reads one service through the selected
Control client. Backend authorization and target restrictions still apply.
Unknown fields are not guessed: unavailable summary fields are returned as
`null`. The result describes saved Control state and makes no runtime-health or
deployment-success claim.

The plugin is trusted code. Importing its directory does not execute it;
selecting and starting it does. Review source, license and digest before use.
