# 擎寰平台 Agent 推送与离线镜像包

Agent 是本平台的节点执行组件；Motor 模型镜像和权重另行准备。每台 Docker 执行主机通常一个 Agent，每个 Kubernetes 集群通常一个执行 Agent。Control 不挂 Docker socket。本手册的离线包是 Agent 镜像包，不是供插件管理页面导入的 `.qhplugin` 社区插件包。

推送只负责新的独立安装与接入；不承担已有 Agent 的覆盖升级。安装器预检、容器启动、HTTPS 接入、资源授权和模型请求分别验证，不能将某个阶段成功当成全部完成。

## 安装合同

目标机已具备 Linux、Python3.9+、Docker 和 Compose2.20+。推送器不安装驱动、Docker 或集群，不修改 daemon，不提升 Kubernetes RBAC。可用root或已明确授权的非交互sudo -n执行身份；不接受任意用户shell或自动拼接sudo su流程。

所有安装使用全新独立目录。发现已有平台 Agent、冲突端口、安装标记或非空目标时拒绝，不停止或覆盖。已有 Agent 应按[运维手册](AGENT_OPERATIONS.md)维护；不得让两个独立账本同时控制同一 Docker daemon。

控制面 `MOTOR_AGENT_ROLLOUT_ROOT` 决定目标机安装根，例如 `/opt/motor-console/agents`；安装器创建 `<根>/<安装标识>/package`、`data`、`agent.env`、`workspace` 和操作归属标记。通用默认为 `/var/lib/motor-console-agents`。各安装不共享数据、私钥或工作区，实际目录以预检结果为准。

## 操作流程

1. 管理员进入节点管理的推送入口，填写管理地址、节点名、安装标识、SSH身份和Docker/Kubernetes运行环境。
2. 从目标控制台或可信资产系统独立核对SSH SHA256指纹，再提供本次认证材料；首次网络探测不是可信指纹来源。
3. 选择已审查的仓库镜像digest或已登记离线包。Kubernetes需已有专用kubeconfig；默认Docker Agent端口19082，Kubernetes19083。
4. 保留独立HTTPS，核对管理IP和防火墙，仅允许Control来源。默认资源上限1CPU/1GiB/256PID，原生并发1。
5. 重新做只读预检、核对身份和冲突、按节点名称确认安装。
6. 从任务进度/推送记录核对“安装”与“接入”结果；容器Running而TLS不可达属于已安装未接入，不得报告成功上线。

SSH指纹不符在认证前停止。认证材料只供本次进程使用，不写DB/日志/包；进程中断需要重新提供。Agent服务端私钥在目标生成，公共CA通过已核验SSH渠道交给Control；Control验证CA、IP/DNS、产品身份、协议及回执/账本/准入三能力。不能用关闭TLS检查替代信任。

## 准备离线镜像

在匹配架构的 Linux Docker 构建主机，从仓库根运行标准库打包工具。Agent API 镜像必须已存在且固定 Image ID，输出目录须私有且为空：

```bash
install -d -m 0700 /srv/motor-console-agent-package
python3 web/scripts/package_agent_image.py \
  --image 'sha256:<64位实际ImageID>' \
  --output-dir /srv/motor-console-agent-package \
  --artifact-id motor-agent-arm64-0.11.0
```

工具仅用本机Docker socket，不拉镜像、不登录仓库、不SSH。短命候选容器无网络、无宿主挂载/设备/socket、只读、非root、删除capability并有资源上限；按随机名称/标签/镜像核对后仅清理本次候选。

当前兼容检查要求 `product_id=motor-console`、Agent 版本至少 `0.10.1`、Agent/操作协议均为 1、回执/账本/准入三项能力为 true、固定 Motor commit 的源码完整性通过，以及准备/TLS 脚本存在。以实际 Console 版本的最低 Agent 合同为准；较大版本号不能绕过产品或协议校验。

同一Image ID导出gzip，分块SHA256，未压缩流/成品各最多2GiB，默认超时900秒。默认不允许已有非空目录，追加其他ID需显式`--allow-existing-directory`，仍不覆盖同名文件。清单最后独占发布，未完成`.part`保留供核对，不冒充可用包。

清单schema1恰含11项：

```json
{
  "schema": 1,
  "id": "motor-agent-arm64-0.11.0",
  "filename": "motor-agent-arm64-0.11.0.tar.gz",
  "sha256": "<成品SHA256>",
  "size_bytes": 123456789,
  "image_id": "sha256:<实际ImageID>",
  "architecture": "arm64",
  "console_version": "0.11.0",
  "agent_protocol": 1,
  "motor_commit": "37d615f232a36b8dba8630ffc3a01a20d289c65e",
  "product_id": "motor-console"
}
```

管理员把两个成品放到Control data/rollout-artifacts，目录0700、文件0600，属主交给Control实际UID/GID；不能宽松权限或全卷chown。库存拒绝链接和任意路径。列表只核元数据，安装前重新完整SHA，发送/加载后再核远端摘要与Image ID。SHA清单是管理员提供的材料，不是上游签名认证。Image ID与仓库manifest digest不是同一身份。

## 中断与恢复

任何阶段只处理本次operation归属。上传失败保留未完成文件；加载或准备中断不能假定“未执行”。恢复核对只读归属标记、TLS回执和容器身份，不重复生成密钥、不执行up、不删资源。Agent正确运行后可重试连接。

`.active-rollout.json`可能在中断后保留，禁止新装。必须先确认原进程和Docker动作已结束，核对完整operation/目录/容器归属，再按维护流程处理，不能只凭SSH断线就删锁。新装流程不承担已有Agent的覆盖升级。

真实打包、远程安装、HTTPS接入、运行时授权与模型推理分别验收；本地mock或候选容器校验不能代替这些结果。
