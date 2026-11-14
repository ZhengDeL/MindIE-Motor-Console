# syntax=docker/dockerfile:1
ARG NODE_BUILD_IMAGE=node:22-alpine
ARG PYTHON_BUILD_IMAGE=python:3.12-slim-bookworm
ARG NGINX_BUILD_IMAGE=nginx:stable-alpine
FROM ${NODE_BUILD_IMAGE} AS frontend-build
ARG ASCEND_VISIBLE_DEVICES=void
WORKDIR /build
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/frontend/ ./
RUN npm run build

FROM ${PYTHON_BUILD_IMAGE} AS sqlite-build
ARG ASCEND_VISIBLE_DEVICES=void
ARG DEBIAN_MIRROR=https://deb.debian.org
# Official SQLite source and SHA3-256 from sqlite.org/download.html (2026-11-02).
ARG SQLITE_ARCHIVE=sqlite-autoconf-3540100
ARG SQLITE_SHA3_256=b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fL --retry 3 "https://www.sqlite.org/2026/${SQLITE_ARCHIVE}.tar.gz" -o /tmp/sqlite.tar.gz \
    && python -c "import hashlib,pathlib; assert hashlib.sha3_256(pathlib.Path('/tmp/sqlite.tar.gz').read_bytes()).hexdigest() == '${SQLITE_SHA3_256}'" \
    && mkdir /tmp/sqlite-src && tar -xzf /tmp/sqlite.tar.gz -C /tmp/sqlite-src --strip-components=1 \
    && cd /tmp/sqlite-src && ./configure --prefix=/opt/sqlite --disable-static \
    && make -j2 && make install

FROM ${PYTHON_BUILD_IMAGE} AS runtime
ARG ASCEND_VISIBLE_DEVICES=void
ARG CONSOLE_VERSION=0.11.2
ARG VCS_REF=""
LABEL org.opencontainers.image.title="MindIE-Motor Console" \
    io.motor.product="motor-console" \
    org.opencontainers.image.version="${CONSOLE_VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.source="https://github.com/ZhengDeL/MindIE-Motor-Console"
ARG DEBIAN_MIRROR=https://deb.debian.org
ARG TARGETARCH
ARG INSTALL_KUBECTL=true
ARG KUBECTL_VERSION=v1.34.1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LD_LIBRARY_PATH=/opt/sqlite/lib \
    MOTOR_MODE=control MOTOR_DATA_DIR=/var/lib/motor-console \
    MOTOR_UPSTREAM_DIR=/opt/motor/upstream MOTOR_STATIC_DIR=/app/frontend/dist \
    MOTOR_HOST_WORKSPACE_DIR=/opt/motor-console/workspace MOTOR_HOST_ROOT=/host \
    MOTOR_AGENT_BIND_IP=127.0.0.1 MOTOR_AGENT_PORT=19082
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sqlite-build /opt/sqlite/lib/ /opt/sqlite/lib/
RUN python -c "import sqlite3; assert sqlite3.sqlite_version_info >= (3,51,3), sqlite3.sqlite_version"
RUN if [ "$INSTALL_KUBECTL" = true ]; then \
      arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
      curl -fL --retry 3 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${arch}/kubectl" -o /usr/local/bin/kubectl \
      && curl -fL --retry 3 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${arch}/kubectl.sha256" -o /tmp/kubectl.sha256 \
      && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum --check \
      && chmod 0755 /usr/local/bin/kubectl && rm /tmp/kubectl.sha256; \
    fi
WORKDIR /app
COPY LICENSE NOTICE.md /usr/share/doc/motor-console/
COPY web/requirements.lock /app/requirements.lock
ARG PYPI_INDEX_URL=https://pypi.org/simple
RUN pip install --index-url "${PYPI_INDEX_URL}" --no-cache-dir --require-hashes -r /app/requirements.lock
COPY mindie-motor/upstream /opt/motor/upstream
RUN chmod -R a-w /opt/motor/upstream
COPY web/backend /app/backend
COPY web/scripts /app/scripts
COPY web/compose.docker-agent.yaml web/compose.kubernetes-agent.yaml \
     web/compose.docker-kubernetes-agent.yaml web/compose.agent-kubectl-client.yaml /app/rollout-assets/
RUN chmod 0755 /app/scripts/container-entrypoint.sh
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python /app/scripts/healthcheck.py
ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/container-entrypoint.sh"]

FROM runtime AS api
ENV MOTOR_STATIC_ENABLED=false

FROM ${NGINX_BUILD_IMAGE} AS web
ARG ASCEND_VISIBLE_DEVICES=void
ARG CONSOLE_VERSION=0.11.2
ARG VCS_REF=""
LABEL org.opencontainers.image.title="MindIE-Motor Console Web" \
    io.motor.product="motor-console" \
    org.opencontainers.image.version="${CONSOLE_VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.source="https://github.com/ZhengDeL/MindIE-Motor-Console"
COPY --from=frontend-build /build/dist /usr/share/nginx/html
COPY web/deploy/nginx/web.conf /etc/nginx/conf.d/default.conf
# One event-loop worker matches the console's 0.5 CPU budget. Host CPU-based
# auto workers can exhaust the 64-PID limit on large accelerator servers.
RUN sed -i -E 's@^[[:space:]]*pid[[:space:]]+[^;]+;@pid /tmp/nginx.pid;@;s@^[[:space:]]*worker_processes[[:space:]]+[^;]+;@worker_processes 1;@;/^[[:space:]]*user[[:space:]]/d' /etc/nginx/nginx.conf
USER 101:101
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/health
ENTRYPOINT ["nginx", "-g", "daemon off;"]

FROM runtime AS bundled
COPY --from=frontend-build /build/dist /app/frontend/dist
