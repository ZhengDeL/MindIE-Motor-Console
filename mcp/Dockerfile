# syntax=docker/dockerfile:1
# Required: supply reviewed immutable base images when building for distribution.
ARG NODE_BUILD_IMAGE
ARG NODE_RUNTIME_IMAGE
FROM ${NODE_BUILD_IMAGE} AS build
WORKDIR /opt/project
COPY packages/node-sdk/package.json packages/node-sdk/package-lock.json ./packages/node-sdk/
RUN npm --prefix packages/node-sdk ci --ignore-scripts --no-audit --no-fund
COPY packages/node-sdk/ ./packages/node-sdk/
COPY packages/mcp-sdk/package.json packages/mcp-sdk/package-lock.json ./packages/mcp-sdk/
RUN npm --prefix packages/mcp-sdk ci --ignore-scripts --no-audit --no-fund
COPY packages/mcp-sdk/ ./packages/mcp-sdk/
COPY platform/package.json platform/package-lock.json ./platform/
RUN npm --prefix platform ci --ignore-scripts --no-audit --no-fund
COPY platform/src ./platform/src
COPY platform/scripts ./platform/scripts
COPY platform/tsconfig.json ./platform/tsconfig.json
COPY mcp/package.json mcp/package-lock.json ./mcp/
RUN npm --prefix mcp ci --ignore-scripts --no-audit --no-fund
COPY mcp/src ./mcp/src
COPY mcp/scripts ./mcp/scripts
COPY mcp/examples ./mcp/examples
COPY mcp/tsconfig.json ./mcp/tsconfig.json
RUN npm --prefix mcp run build && npm --prefix mcp prune --omit=dev --ignore-scripts --no-audit --no-fund

FROM ${NODE_RUNTIME_IMAGE} AS runtime
ARG CONSOLE_VERSION=0.12.0
ARG VCS_REF=""
LABEL org.opencontainers.image.title="MindIE-Motor Console MCP" \
    org.opencontainers.image.source="https://github.com/ZhengDeL/MindIE-Motor-Console" \
    org.opencontainers.image.version="${CONSOLE_VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    io.motor.product="motor-console" io.motor.component="mcp"
WORKDIR /opt/project/mcp
ENV NODE_ENV=production NODE_TLS_REJECT_UNAUTHORIZED=1
COPY LICENSE NOTICE.md /usr/share/doc/motor-console/
COPY --from=build /opt/project/packages/node-sdk/package.json /opt/project/packages/node-sdk/package.json
COPY --from=build /opt/project/packages/node-sdk/dist /opt/project/packages/node-sdk/dist
COPY --from=build /opt/project/packages/mcp-sdk/package.json /opt/project/packages/mcp-sdk/package.json
COPY --from=build /opt/project/packages/mcp-sdk/dist /opt/project/packages/mcp-sdk/dist
COPY --from=build /opt/project/platform/package.json /opt/project/platform/package.json
COPY --from=build /opt/project/platform/dist /opt/project/platform/dist
COPY --from=build /opt/project/mcp/package.json ./package.json
COPY --from=build /opt/project/mcp/node_modules ./node_modules
COPY --from=build /opt/project/mcp/dist ./dist
COPY --from=build /opt/project/mcp/scripts ./scripts
COPY --from=build /opt/project/mcp/examples ./examples
# Match the preparation CLI and private-file ownership in compose.headless.yaml.
USER 10001:10001
EXPOSE 8443
CMD ["node", "dist/cli.js", "start", "--config", "/run/motor-mcp/config/composition.json"]
