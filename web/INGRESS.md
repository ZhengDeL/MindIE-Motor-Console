# MindIE-Motor Console 独立 HTTPS 入口

本手册对应 0.11.1，用于 bundled 兼容拓扑的可选独立 TLS 代理。推荐模块化部署已包含独立 Web/TLS 入口，见[生产手册](PRODUCTION.md)，通常不再增加此代理。它是平台的 HTTPS 访问组件，不是原生 Motor 部署要求，也不转发模型推理流量。

[compose.ingress.host-network.yaml](compose.ingress.host-network.yaml) 使用独立 Nginx 容器，将 `https://192.0.2.10:19443` 转发到同机 `127.0.0.1:19080` 控制面。入口容器独立升级和重启，使用 host 网络、`runc`、非 root UID/GID `101:101`，无需 Docker socket。控制面需只监听 loopback，并仅信任 loopback 转发代理；`MOTOR_TRUSTED_ORIGINS` 填写包含端口的准确 HTTPS 来源。

## 准备文件

在项目部署根内建立独立入口目录，例如 `/var/lib/motor-console-ingress/config` 和 `/var/lib/motor-console-ingress/tls`。将[兼容入口配置示例](deploy/nginx/motor-console.conf.example)复制为 `config/nginx.conf`，核对上游端口与 `listen`。示例固定公开端口 `19443`、上游 `19080`，修改后同步核对 Compose 健康检查。先确认所需端口未被占用，配置和证书文件均由部署方审阅。

证书目录仅放部署方提供的三个文件：`server.crt`（叶证书及必要中间链）、`server.key`（无交互解密的服务端私钥）、`ca.pem`（健康检查信任链）。不要放 CA 私钥、控制台加密密钥或初始化凭据。证书 SAN 必须包含浏览器实际访问的 IP/DNS 名；内部 CA 的信任分发由部署方管理。

入口需要读取这些文件。将 TLS 目录设置为 `101:101`、权限 `0700`，三个文件设为 `101:101`、`0400`；配置目录及 `nginx.conf` 可由 root 拥有并提供 UID 101 的只读访问。使用目录挂载，证书原子替换后仍能在容器内看到新文件。不要修改其他服务正在使用的证书目录。

统一部署目录可将 config/tls 设为 `/opt/motor-console/ingress/config` 和 `/opt/motor-console/tls/control`，示例见[入口环境配置](deploy/motor-console/ingress.env.example)。以下命令从仓库根执行，先创建私有 `ingress.env`：

```dotenv
MOTOR_INGRESS_IMAGE=registry.example.test/library/nginx@sha256:<64位镜像摘要>
MOTOR_INGRESS_CONFIG_DIR=/var/lib/motor-console-ingress/config
MOTOR_INGRESS_TLS_DIR=/var/lib/motor-console-ingress/tls
MOTOR_INGRESS_HEALTH_HOST=192.0.2.10
```

选用经过目标架构验证、带 `curl` 的 Nginx 镜像，并固定仓库摘要。健康检查访问本机对应的证书 SAN 名/IP，使用 `ca.pem` 校验证书及主机名；它不使用 `-k`。入口为非 root 进程，镜像必须允许 UID 101 运行 Nginx；该文件绕过镜像默认 entrypoint 的可写配置初始化。

## 检查与启动

在有 Console Python 依赖的环境检查叶证书及 CA 的剩余有效期：

```bash
python web/scripts/check_tls_expiry.py --cert /var/lib/motor-console-ingress/tls/server.crt --cert /var/lib/motor-console-ingress/tls/ca.pem --minimum-days 30
docker compose --env-file ingress.env -f web/compose.ingress.host-network.yaml config --quiet
docker compose --env-file ingress.env -f web/compose.ingress.host-network.yaml run --rm --no-deps --entrypoint nginx proxy -t -c /etc/motor-console/nginx.conf
docker compose --env-file ingress.env -f web/compose.ingress.host-network.yaml up -d
```

有效期检查退出码 `0` 表示所有证书超过阈值且当前有效，`1` 表示过期、即将过期或尚未生效，`2` 表示输入无效。该脚本只读，不验证证书链、主机名、吊销状态或私钥配对；这些需要独立核验。将其加入已有监控或每日调度，并在阈值触发时续签，不能依赖容器重启自动续签。

端口 19443 已被占用时，选择独立端口并同步更新证书健康检查、Nginx、origin 和防火墙，不停止未知服务。Compose 不会替操作者停止冲突进程。切换平台入口无需重建 Agent 或模型容器。

## 运行与续签

默认容器名为 `motor-console-ingress-proxy-1`。容器根目录只读，临时目录为有界 tmpfs，限制 `0.5 CPU / 128 MiB / 128 PID`，日志最多三段、每段 5 MiB。关闭访问日志；启动和 TLS 诊断写到标准错误，代理请求错误不写原始 URI，业务请求证据使用控制台结构化日志。代理保留 Host 端口及 Origin，重建转发来源头；写请求不自动重试，不缓存响应，关闭响应缓冲以兼容流式传输。

续签前保留原证书，检查 SAN、链、密钥配对及有效期；原子替换文件并恢复属主/权限，再执行上述 `nginx -t`。已运行容器通过以下命令无中断重载：

```bash
docker compose --env-file ingress.env -f web/compose.ingress.host-network.yaml exec proxy nginx -t -c /etc/motor-console/nginx.conf
docker compose --env-file ingress.env -f web/compose.ingress.host-network.yaml exec proxy nginx -s reload -c /etc/motor-console/nginx.conf
```

随后重新验证严格 HTTPS、`/ready` 和实际账号登录。配置校验失败时保留当前进程并恢复原文件；入口健康不代表模型已经完成推理验收。

配置依据：[Nginx HTTP 代理](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)、[TLS](https://nginx.org/en/docs/http/ngx_http_ssl_module.html)。
