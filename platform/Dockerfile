ARG NODE_BUILD_IMAGE=node:22-bookworm-slim
ARG NODE_RUNTIME_IMAGE=node:22-bookworm-slim
FROM ${NODE_BUILD_IMAGE} AS build
WORKDIR /opt/console-platform
COPY platform/package.json platform/package-lock.json ./
RUN npm ci --ignore-scripts
COPY platform/tsconfig.json ./
COPY platform/src ./src
COPY platform/scripts ./scripts
COPY platform/examples/container-python-bridge.template.json ./examples/container-python-bridge.template.json
RUN npm run build && node dist/cli.js pin --input examples/container-python-bridge.template.json --output composition.json

FROM ${NODE_RUNTIME_IMAGE} AS runtime
ARG CONSOLE_VERSION=0.12.0
ARG VCS_REF=""
LABEL org.opencontainers.image.title="MindIE-Motor Console Plugin Host" \
    org.opencontainers.image.source="https://github.com/ZhengDeL/MindIE-Motor-Console" \
    org.opencontainers.image.version="${CONSOLE_VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    io.motor.product="motor-console" \
    io.motor.component="plugin-host"
WORKDIR /opt/console-platform
ENV NODE_ENV=production
COPY --from=build --chown=node:node /opt/console-platform/package.json ./package.json
COPY --from=build --chown=node:node /opt/console-platform/dist ./dist
COPY --from=build --chown=node:node /opt/console-platform/composition.json ./composition.json
USER node
EXPOSE 8000
CMD ["node", "dist/cli.js", "start", "--config", "/opt/console-platform/composition.json"]
