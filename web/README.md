# Web 开发与本地预览

本指南用于本地界面体验和应用开发。正式安装见[安装指南](docs/INSTALLATION.md)；插件开发见[公开 SDK 指南](PLUGIN_DEVELOPMENT.md)。

## 环境与源码

- Python 3.12、独立虚拟环境；Linux 容器是生产部署目标，Windows 可进行本地界面与接口开发。
- Node.js 22 与 npm：与 Dockerfile 前端构建基线一致；依赖按 `package-lock.json` 安装。
- 已初始化的固定 `mindie-motor/upstream` 子模块。不需要为了演示页面安装 Docker、驱动或加载模型。

从仓库根目录执行：

```bash
git submodule update --init --recursive
python -m venv .venv
```

激活虚拟环境后安装开发依赖：

```bash
# Bash
source .venv/bin/activate
python -m pip install -r web/requirements.txt
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
python -m pip install -r web/requirements.txt
```

PowerShell 若禁止脚本激活，可直接用 `.\.venv\Scripts\python.exe` 替代后面的 `python`，无需修改全局执行策略。开发依赖范围见 `requirements.txt`，正式镜像使用带哈希的 `requirements.lock`。

## 启动独立演示环境

先选择一个空闲的本地数据目录，使用绝对路径，避免与已有控制面数据库混用。以下两个示例择一执行；启动脚本会切换工作目录，因此不要传相对数据路径。

```bash
# Bash，仓库根目录；.local/demo-data 是本次开发专用目录
export MOTOR_DATA_DIR="$(pwd)/.local/demo-data"
python web/scripts/serve.py --mode demo --host 127.0.0.1 --port 19080
```

```powershell
# PowerShell，仓库根目录
$env:MOTOR_DATA_DIR = Join-Path (Get-Location).Path '.local/demo-data'
python web/scripts/serve.py --mode demo --host 127.0.0.1 --port 19080
```

另开终端：

```bash
cd web/frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开 `http://127.0.0.1:5174`。成功标志是页面加载、节点/服务等演示列表可读，浏览器 `/api` 请求由 Vite 转发至 `127.0.0.1:19080`。演示模式提供模拟数据和有限的本地操作，不执行宿主部署、不加载上传插件，不能作为真实硬件、模型或生产安全验收。

停止时在两个终端分别按 `Ctrl+C`。演示状态保留在设置的数据目录；再次使用同一路径会继续读取这些状态。

## 构建与预览

在 `web/frontend` 执行：

```bash
npm run build
npm run preview -- --host 127.0.0.1
```

构建输出在 `web/frontend/dist`，预览默认 `http://127.0.0.1:4174`。当前 Vite 配置让预览继承开发 `/api` 代理，因此仍需后端监听 `19080`。预览只用于检查构建产物；生产静态文件通过同源反向代理访问 API，不使用 Vite 开发或预览服务器。单独打开 `dist/index.html` 不能验证 API。

需要本地容器演示时，在仓库根目录使用 Bash：

```bash
docker build -f web/Dockerfile --target bundled \
  --build-arg CONSOLE_VERSION=0.12.0 -t motor-console:local .
MOTOR_CONSOLE_IMAGE=motor-console:local \
  docker compose -f web/compose.yaml --profile demo up -d demo
```

PowerShell 可先设置 `$env:MOTOR_CONSOLE_IMAGE='motor-console:local'`，再运行单行 Compose 命令。演示容器仅发布本机 `19000` 端口；结束时执行同一文件栈的 `docker compose -f web/compose.yaml --profile demo stop demo`。上面的构建参数是显示版本，不证明制品来自固定标签；发布构建还应记录实际 Git 提交和镜像摘要。

## 常见问题

| 现象 | 检查 |
| --- | --- |
| 页面能开，API 失败 | 后端是否监听 `19080`；开发/预览配置的 API 代理是否仍指向该端口。 |
| 端口已占用 | 不停止不明进程；选择其他端口，同时调整 `vite.config.ts` 开发代理。 |
| 数据库拒绝启动 | 确认使用新目录、绝对路径和单进程；不要把旧生产库当作演示数据。 |
| 缺少原生模板/源码 | 从仓库根目录初始化子模块，核对记录的提交，不切换 upstream 到最新分支。 |
| 插件管理接口不存在 | 对照源码身份和 `/api/plugin-sdk`；旧部署不一定含社区包加载器，更新源码不会更新正在运行的容器。 |
| 需要真实部署或登录 | 转到[部署入口](DEPLOYMENT.md)，使用独立 Control、凭据和受信 Agent；演示模式不替代正式环境。 |

## 代码入口与后续阅读

| 入口 | 职责 |
| --- | --- |
| `frontend/src` | 工作台、表单、主题、查询生命周期和页面权限展示。 |
| `backend/app.py` | Control 应用装配与管理 API；服务端权限为准。 |
| `backend/node_agent.py` | 执行 Agent 协议，不是推理流量代理。 |
| `backend/motor_adapter.py` | 固定原生 deployer/helper 适配及原始配置保留。 |
| `backend/plugin_host` | 显式插件合同、依赖校验、生命周期与社区包管理。 |

生产推荐独立 API/Web 镜像和 `compose.modular.yaml`。Web 不挂载数据库、节点凭据或 Docker socket；存储、原生执行及外部观测的职责见[系统架构](ARCHITECTURE.md)。

继续阅读：[平台品牌](docs/BRANDING.md)、[观测接入](observability/README.md)、[数据库](STORAGE.md)、[插件开发](PLUGIN_DEVELOPMENT.md)、[贡献流程](../CONTRIBUTING.md)。运行数据库、环境文件、令牌、测试及私有验收材料不提交 Git。
