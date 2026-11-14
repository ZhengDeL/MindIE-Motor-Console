import { useBranding } from "./brandingState";
import type { ReactNode } from "react";
import { Server, ShieldCheck } from "./icons";
import { Badge, Button, Field, InfoBox, Spinner } from "./ui";
import type {
  AgentHostKey,
  AgentImageArtifact,
  AgentRolloutSpec,
  AgentSSHConnection,
  AgentSSHCredentials,
} from "./agentRollout";
type ChangeSpec = <K extends keyof AgentRolloutSpec>(
  key: K,
  value: AgentRolloutSpec[K],
) => void;
type ChangeConnection = <K extends keyof AgentSSHConnection>(
  key: K,
  value: AgentSSHConnection[K],
) => void;
export function CredentialFields({
  method,
  onMethod,
  epoch,
  keyFilename,
  onKey,
  onPassword,
  onPassphrase,
}: {
  method: AgentSSHCredentials["method"];
  onMethod: (value: AgentSSHCredentials["method"]) => void;
  epoch: number;
  keyFilename: string;
  onKey: (file?: File) => void;
  onPassword: (value: string) => void;
  onPassphrase: (value: string) => void;
}) {
  return (
    <>
      <h3>登录凭据</h3>
      <Field label="认证方式">
        <select
          value={method}
          onChange={(event) =>
            onMethod(event.target.value as AgentSSHCredentials["method"])
          }
        >
          <option value="password">密码</option>
          <option value="private_key">SSH 私钥文件</option>
        </select>
      </Field>
      <div key={epoch}>
        {method === "password" ? (
          <Field label="SSH 登录密码" required>
            <input
              type="password"
              autoComplete="new-password"
              maxLength={4096}
              onChange={(event) => onPassword(event.target.value)}
            />
          </Field>
        ) : (
          <>
            <Field
              label="私钥文件"
              required
              hint="最多 64 KiB；只保留于本窗口内存，不展示私钥内容。"
            >
              <input
                type="file"
                aria-label="选择 SSH 私钥文件"
                onChange={(event) => {
                  onKey(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
            </Field>
            {keyFilename && (
              <p className="rollout-key-filename">已选择：{keyFilename}</p>
            )}
            <Field label="私钥口令（可选）">
              <input
                type="password"
                autoComplete="new-password"
                maxLength={4096}
                onChange={(event) => onPassphrase(event.target.value)}
              />
            </Field>
          </>
        )}
      </div>
    </>
  );
}
export function IdentityFields({
  spec,
  connection,
  changeSpec,
  changeConnection,
  busy,
  locked,
  observedKey,
  fingerprintConfirmed,
  setFingerprintConfirmed,
  discover,
  children,
}: {
  spec: AgentRolloutSpec;
  connection: AgentSSHConnection;
  changeSpec: ChangeSpec;
  changeConnection: ChangeConnection;
  busy: string;
  locked: boolean;
  observedKey: AgentHostKey | null;
  fingerprintConfirmed: boolean;
  setFingerprintConfirmed: (value: boolean) => void;
  discover: () => Promise<void>;
  children: ReactNode;
}) {
  return (
    <div className="rollout-section">
      <InfoBox>
        执行主机需已有 Docker 和 Compose ≥2.20。Docker 每台宿主机一个
        Agent；Kubernetes 每集群一个执行 Agent，使用预先授权的 kubeconfig。
      </InfoBox>
      <h3>
        <Server size={17} />
        目标节点
      </h3>
      <div className="rollout-form-grid">
        <Field label="节点名称" required>
          <input
            value={spec.name}
            maxLength={100}
            onChange={(event) => changeSpec("name", event.target.value)}
            placeholder="npu-worker-01"
          />
        </Field>
        <Field label="管理 IPv4 地址" required>
          <input
            value={spec.address}
            onChange={(event) =>
              changeSpec("address", event.target.value.trim())
            }
            placeholder="192.0.2.10"
          />
        </Field>
        <Field
          label="SSH 主机"
          required
          hint="可使用管理 IP 或解析到目标节点的主机名。"
        >
          <input
            value={connection.host}
            onChange={(event) =>
              changeConnection("host", event.target.value.trim())
            }
            placeholder="192.0.2.10"
          />
        </Field>
        <Field label="SSH 端口" required>
          <input
            type="number"
            min={1}
            max={65535}
            value={connection.port}
            onChange={(event) =>
              changeConnection("port", Number(event.target.value))
            }
          />
        </Field>
        <Field label="SSH 用户" required>
          <input
            autoComplete="off"
            value={connection.username}
            onChange={(event) =>
              changeConnection("username", event.target.value)
            }
          />
        </Field>
        <Field label="执行权限">
          <select
            value={connection.privilege}
            onChange={(event) =>
              changeConnection(
                "privilege",
                event.target.value as AgentSSHConnection["privilege"],
              )
            }
          >
            <option value="root">root 环境直接执行</option>
            <option value="sudo_n">非 root，使用 sudo -n 免密提权</option>
          </select>
        </Field>
      </div>
      <h3>
        <ShieldCheck size={17} />
        SSH 主机身份
      </h3>
      <div className="rollout-fingerprint-discovery">
        <Button
          loading={busy === "host-key"}
          disabled={locked && busy !== "host-key"}
          onClick={() => void discover()}
        >
          仅探测主机指纹
        </Button>
        <span>此步骤不发送用户名、密码或私钥。</span>
      </div>
      {observedKey && (
        <div className="rollout-observed-key">
          <Badge status="warning">尚未验证</Badge>
          <span>{observedKey.key_type}</span>
          <code>{observedKey.fingerprint}</code>
        </div>
      )}
      <Field
        label="期望的 SSH 主机指纹"
        required
        hint="通过目标机控制台等独立渠道执行 ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub 核对；网络探测结果不能作为信任依据。"
      >
        <input
          className="mono"
          value={connection.host_key_sha256}
          autoComplete="off"
          onChange={(event) =>
            changeConnection("host_key_sha256", event.target.value.trim())
          }
          placeholder="SHA256:…"
        />
      </Field>
      <label className="rollout-checkbox">
        <input
          type="checkbox"
          checked={fingerprintConfirmed}
          onChange={(event) => setFingerprintConfirmed(event.target.checked)}
        />
        <span>我已通过独立渠道核对上述指纹，确认它属于目标主机。</span>
      </label>
      {children}
    </div>
  );
}

export function ConfigurationFields({
  spec,
  changeSpec,
  artifacts,
  artifactLoading,
  artifactIssues,
  labelsText,
  setLabelsText,
}: {
  spec: AgentRolloutSpec;
  changeSpec: ChangeSpec;
  artifacts: AgentImageArtifact[];
  artifactLoading: boolean;
  artifactIssues: boolean;
  labelsText: string;
  setLabelsText: (value: string) => void;
}) {
  const branding = useBranding();
  return (
    <div className="rollout-section">
      <div className="rollout-form-grid">
        <Field label="运行环境">
          <select
            value={spec.runtime}
            onChange={(event) => {
              changeSpec(
                "runtime",
                event.target.value as AgentRolloutSpec["runtime"],
              );
              changeSpec("kubeconfig_path", "");
            }}
          >
            <option value="docker">Docker 宿主机</option>
            <option value="kubernetes">Kubernetes 集群</option>
          </select>
        </Field>
        <Field
          label="安装标识"
          required
          hint="用于远端安装目录与容器项目标识；使用小写字母、数字和连字符。"
        >
          <input
            value={spec.installation_name}
            maxLength={63}
            onChange={(event) =>
              changeSpec("installation_name", event.target.value)
            }
          />
        </Field>
      </div>
      <h3>Agent 镜像</h3>
      <InfoBox>
        此处安装 {branding.name} 自有 Agent，请使用专用 Agent 镜像。
        推理镜像在创建推理服务时配置。
      </InfoBox>
      <Field label="镜像来源">
        <select
          value={spec.image_source}
          onChange={(event) => {
            changeSpec(
              "image_source",
              event.target.value as AgentRolloutSpec["image_source"],
            );
            changeSpec("image", "");
            changeSpec(
              "artifact_id",
              event.target.value === "artifact" ? (artifacts[0]?.id ?? "") : "",
            );
          }}
        >
          <option value="artifact">已准备的镜像包</option>
          <option value="registry">镜像仓库（固定摘要）</option>
        </select>
      </Field>
      {spec.image_source === "artifact" ? (
        artifactLoading ? (
          <Spinner label="读取 Agent 镜像包" />
        ) : artifacts.length ? (
          <>
            <Field label="Agent 镜像包" required>
              <select
                value={spec.artifact_id}
                onChange={(event) =>
                  changeSpec("artifact_id", event.target.value)
                }
              >
                {artifacts.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.filename} · {item.architecture} · Console{" "}
                    {item.console_version}
                  </option>
                ))}
              </select>
            </Field>
            {artifacts
              .filter((item) => item.id === spec.artifact_id)
              .map((item) => (
                <dl className="rollout-summary" key={item.id}>
                  <dt>文件摘要</dt>
                  <dd>
                    <code>sha256:{item.sha256}</code>
                  </dd>
                  <dt>大小 / 协议</dt>
                  <dd>
                    {(item.size_bytes / 1024 ** 2).toFixed(1)} MiB · Agent 协议{" "}
                    {item.agent_protocol}
                  </dd>
                </dl>
              ))}
          </>
        ) : (
          <InfoBox>
            尚无可用的 Agent 镜像包。请在有 Docker 的主机使用{" "}
            <code>web/scripts/package_agent_image.py</code>{" "}
            打包，并按部署文档放入控制台配置的镜像包目录；也可选择固定摘要的
            Agent 仓库镜像。
          </InfoBox>
        )
      ) : (
        <Field
          label="Agent 镜像摘要"
          required
          hint="填写包含 Agent 及固定 Motor 源码的控制台镜像，以不可变 SHA256 摘要固定版本。"
        >
          <input
            className="mono"
            value={spec.image}
            onChange={(event) => changeSpec("image", event.target.value.trim())}
            placeholder="registry.example.com/qinghuan-platform@sha256:…"
          />
        </Field>
      )}
      {artifactIssues && (
        <p className="rollout-muted">
          部分镜像包未通过读取或元数据检查，未列入可选项。
        </p>
      )}
      {spec.runtime === "kubernetes" && (
        <Field
          label="远端 kubeconfig 绝对路径"
          required
          hint="只使用预先授权的集群凭据；安装向导不会创建或提升集群角色。"
        >
          <input
            className="mono"
            value={spec.kubeconfig_path}
            onChange={(event) =>
              changeSpec("kubeconfig_path", event.target.value)
            }
            placeholder="/etc/qinghuan-platform/agent.kubeconfig"
          />
        </Field>
      )}
      <h3>Agent HTTPS 接入</h3>
      <div className="rollout-form-grid">
        <Field label="TLS 方式">
          <select
            value={spec.tls_mode}
            onChange={(event) => {
              changeSpec(
                "tls_mode",
                event.target.value as AgentRolloutSpec["tls_mode"],
              );
              changeSpec("https_proxy_confirmed", false);
            }}
          >
            <option value="managed_tls">自动签发与安装 HTTPS 身份</option>
            <option value="existing_https">使用现有 HTTPS 入口</option>
          </select>
        </Field>
        <Field label="Agent 监听端口" required>
          <input
            type="number"
            min={1024}
            max={65535}
            value={spec.agent_port}
            onChange={(event) =>
              changeSpec("agent_port", Number(event.target.value))
            }
          />
        </Field>
      </div>
      <Field
        label="控制台访问 Agent 的 HTTPS 地址"
        required
        hint={
          spec.tls_mode === "managed_tls"
            ? "按管理 IP 与监听端口生成，安装后执行实际连接验证。"
            : "填写已配置好的 HTTPS 反向代理入口。"
        }
      >
        <input
          className="mono"
          value={spec.agent_url}
          readOnly={spec.tls_mode === "managed_tls"}
          onChange={(event) =>
            changeSpec("agent_url", event.target.value.trim())
          }
        />
      </Field>
      {spec.tls_mode === "existing_https" && (
        <label className="rollout-checkbox">
          <input
            type="checkbox"
            checked={spec.https_proxy_confirmed}
            onChange={(event) =>
              changeSpec("https_proxy_confirmed", event.target.checked)
            }
          />
          <span>
            现有 HTTPS 入口已正确转发到此 Agent，且控制台可以验证它的证书。
          </span>
        </label>
      )}
      <details className="rollout-advanced">
        <summary>资源限额与节点标签</summary>
        <div className="rollout-form-grid">
          <Field label="CPU 上限（核）">
            <input
              value={spec.cpus}
              onChange={(event) => changeSpec("cpus", event.target.value)}
            />
          </Field>
          <Field label="内存上限">
            <input
              value={spec.memory}
              onChange={(event) => changeSpec("memory", event.target.value)}
              placeholder="1g"
            />
          </Field>
          <Field label="进程数上限">
            <input
              type="number"
              min={64}
              max={4096}
              value={spec.pids}
              onChange={(event) =>
                changeSpec("pids", Number(event.target.value))
              }
            />
          </Field>
          <Field label="原生部署任务并发">
            <input
              type="number"
              min={1}
              max={4}
              value={spec.native_concurrency}
              onChange={(event) =>
                changeSpec("native_concurrency", Number(event.target.value))
              }
            />
          </Field>
          <Field label="通信网卡（可选）">
            <input
              value={spec.nic_name}
              onChange={(event) => changeSpec("nic_name", event.target.value)}
              placeholder="bond0"
            />
          </Field>
          <Field label="节点标签" hint="使用逗号分隔。">
            <input
              value={labelsText}
              onChange={(event) => {
                setLabelsText(event.target.value);
                changeSpec(
                  "labels",
                  event.target.value
                    .split(/[,，]/)
                    .map((item) => item.trim())
                    .filter(Boolean),
                );
              }}
            />
          </Field>
        </div>
      </details>
    </div>
  );
}
