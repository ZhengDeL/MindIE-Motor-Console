import { UiElement, withUiSlot, UiText } from "./uiPluginSdk";
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
function DefaultCredentialFields({
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
      <UiElement as="h3"><UiText>{"登录凭据"}</UiText></UiElement>
      <Field label="认证方式">
        <UiElement as="select"
          value={method}
          onChange={(event) =>
            onMethod(event.target.value as AgentSSHCredentials["method"])
          }
        >
          <UiElement as="option" value="password"><UiText>{"密码"}</UiText></UiElement>
          <UiElement as="option" value="private_key"><UiText>{"SSH 私钥文件"}</UiText></UiElement>
        </UiElement>
      </Field>
      <UiElement as="div" key={epoch}>
        {method === "password" ? (
          <Field label="SSH 登录密码" required>
            <UiElement as="input"
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
              <UiElement as="input"
                type="file"
                aria-label="选择 SSH 私钥文件"
                onChange={(event) => {
                  onKey(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
            </Field>
            {keyFilename && (
              <UiElement as="p" className="rollout-key-filename"><UiText>{"已选择："}</UiText>{keyFilename}</UiElement>
            )}
            <Field label="私钥口令（可选）">
              <UiElement as="input"
                type="password"
                autoComplete="new-password"
                maxLength={4096}
                onChange={(event) => onPassphrase(event.target.value)}
              />
            </Field>
          </>
        )}
      </UiElement>
    </>
  );
}
function DefaultIdentityFields({
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
    <UiElement as="div" className="rollout-section">
      <InfoBox>
        <UiText>{"执行主机需已有 Docker 和 Compose ≥2.20。Docker 每台宿主机一个 Agent；Kubernetes 每集群一个执行 Agent，使用预先授权的 kubeconfig。"}</UiText>
      </InfoBox>
      <UiElement as="h3">
        <Server size={17} />
        <UiText>{"目标节点"}</UiText>
      </UiElement>
      <UiElement as="div" className="rollout-form-grid">
        <Field label="节点名称" required>
          <UiElement as="input"
            value={spec.name}
            maxLength={100}
            onChange={(event) => changeSpec("name", event.target.value)}
            placeholder="npu-worker-01"
          />
        </Field>
        <Field label="管理 IPv4 地址" required>
          <UiElement as="input"
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
          <UiElement as="input"
            value={connection.host}
            onChange={(event) =>
              changeConnection("host", event.target.value.trim())
            }
            placeholder="192.0.2.10"
          />
        </Field>
        <Field label="SSH 端口" required>
          <UiElement as="input"
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
          <UiElement as="input"
            autoComplete="off"
            value={connection.username}
            onChange={(event) =>
              changeConnection("username", event.target.value)
            }
          />
        </Field>
        <Field label="执行权限">
          <UiElement as="select"
            value={connection.privilege}
            onChange={(event) =>
              changeConnection(
                "privilege",
                event.target.value as AgentSSHConnection["privilege"],
              )
            }
          >
            <UiElement as="option" value="root"><UiText>{"root 环境直接执行"}</UiText></UiElement>
            <UiElement as="option" value="sudo_n"><UiText>{"非 root，使用 sudo -n 免密提权"}</UiText></UiElement>
          </UiElement>
        </Field>
      </UiElement>
      <UiElement as="h3">
        <ShieldCheck size={17} />
        <UiText>{"SSH 主机身份"}</UiText>
      </UiElement>
      <UiElement as="div" className="rollout-fingerprint-discovery">
        <Button actionId="action.AgentRolloutForms.001"
          loading={busy === "host-key"}
          disabled={locked && busy !== "host-key"}
          onClick={() => void discover()}
        >
          <UiText>{"仅探测主机指纹"}</UiText>
        </Button>
        <UiElement as="span"><UiText>{"此步骤不发送用户名、密码或私钥。"}</UiText></UiElement>
      </UiElement>
      {observedKey && (
        <UiElement as="div" className="rollout-observed-key">
          <Badge status="warning"><UiText>{"尚未验证"}</UiText></Badge>
          <UiElement as="span">{observedKey.key_type}</UiElement>
          <UiElement as="code">{observedKey.fingerprint}</UiElement>
        </UiElement>
      )}
      <Field
        label="期望的 SSH 主机指纹"
        required
        hint="通过目标机控制台等独立渠道执行 ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub 核对；网络探测结果不能作为信任依据。"
      >
        <UiElement as="input"
          className="mono"
          value={connection.host_key_sha256}
          autoComplete="off"
          onChange={(event) =>
            changeConnection("host_key_sha256", event.target.value.trim())
          }
          placeholder="SHA256:…"
        />
      </Field>
      <UiElement as="label" className="rollout-checkbox">
        <UiElement as="input"
          type="checkbox"
          checked={fingerprintConfirmed}
          onChange={(event) => setFingerprintConfirmed(event.target.checked)}
        />
        <UiElement as="span"><UiText>{"我已通过独立渠道核对上述指纹，确认它属于目标主机。"}</UiText></UiElement>
      </UiElement>
      {children}
    </UiElement>
  );
}

function DefaultConfigurationFields({
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
    <UiElement as="div" className="rollout-section">
      <UiElement as="div" className="rollout-form-grid">
        <Field label="运行环境">
          <UiElement as="select"
            value={spec.runtime}
            onChange={(event) => {
              changeSpec(
                "runtime",
                event.target.value as AgentRolloutSpec["runtime"],
              );
              changeSpec("kubeconfig_path", "");
            }}
          >
            <UiElement as="option" value="docker"><UiText>{"Docker 宿主机"}</UiText></UiElement>
            <UiElement as="option" value="kubernetes"><UiText>{"Kubernetes 集群"}</UiText></UiElement>
          </UiElement>
        </Field>
        <Field
          label="安装标识"
          required
          hint="用于远端安装目录与容器项目标识；使用小写字母、数字和连字符。"
        >
          <UiElement as="input"
            value={spec.installation_name}
            maxLength={63}
            onChange={(event) =>
              changeSpec("installation_name", event.target.value)
            }
          />
        </Field>
      </UiElement>
      <UiElement as="h3"><UiText>{"Agent 镜像"}</UiText></UiElement>
      <InfoBox>
        <UiText>{"此处安装"}</UiText> {branding.name} <UiText>{"自有 Agent，请使用专用 Agent 镜像。 推理镜像在创建推理服务时配置。"}</UiText>
      </InfoBox>
      <Field label="镜像来源">
        <UiElement as="select"
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
          <UiElement as="option" value="artifact"><UiText>{"已准备的镜像包"}</UiText></UiElement>
          <UiElement as="option" value="registry"><UiText>{"镜像仓库（固定摘要）"}</UiText></UiElement>
        </UiElement>
      </Field>
      {spec.image_source === "artifact" ? (
        artifactLoading ? (
          <Spinner label="读取 Agent 镜像包" />
        ) : artifacts.length ? (
          <>
            <Field label="Agent 镜像包" required>
              <UiElement as="select"
                value={spec.artifact_id}
                onChange={(event) =>
                  changeSpec("artifact_id", event.target.value)
                }
              >
                {artifacts.map((item) => (
                  <UiElement as="option" key={item.id} value={item.id}>
                    {item.filename} <UiText>{"·"}</UiText> {item.architecture} <UiText>{"· Console"}</UiText>{" "}
                    {item.console_version}
                  </UiElement>
                ))}
              </UiElement>
            </Field>
            {artifacts
              .filter((item) => item.id === spec.artifact_id)
              .map((item) => (
                <UiElement as="dl" className="rollout-summary" key={item.id}>
                  <UiElement as="dt"><UiText>{"文件摘要"}</UiText></UiElement>
                  <UiElement as="dd">
                    <UiElement as="code"><UiText>{"sha256:"}</UiText>{item.sha256}</UiElement>
                  </UiElement>
                  <UiElement as="dt"><UiText>{"大小 / 协议"}</UiText></UiElement>
                  <UiElement as="dd">
                    {(item.size_bytes / 1024 ** 2).toFixed(1)} <UiText>{"MiB · Agent 协议"}</UiText>{" "}
                    {item.agent_protocol}
                  </UiElement>
                </UiElement>
              ))}
          </>
        ) : (
          <InfoBox>
            <UiText>{"尚无可用的 Agent 镜像包。请在有 Docker 的主机使用"}</UiText>{" "}
            <UiElement as="code"><UiText>{"web/scripts/package_agent_image.py"}</UiText></UiElement>{" "}
            <UiText>{"打包，并按部署文档放入控制台配置的镜像包目录；也可选择固定摘要的 Agent 仓库镜像。"}</UiText>
          </InfoBox>
        )
      ) : (
        <Field
          label="Agent 镜像摘要"
          required
          hint="填写包含 Agent 及固定 Motor 源码的控制台镜像，以不可变 SHA256 摘要固定版本。"
        >
          <UiElement as="input"
            className="mono"
            value={spec.image}
            onChange={(event) => changeSpec("image", event.target.value.trim())}
            placeholder="registry.example.com/motor-console@sha256:…"
          />
        </Field>
      )}
      {artifactIssues && (
        <UiElement as="p" className="rollout-muted">
          <UiText>{"部分镜像包未通过读取或元数据检查，未列入可选项。"}</UiText>
        </UiElement>
      )}
      {spec.runtime === "kubernetes" && (
        <Field
          label="远端 kubeconfig 绝对路径"
          required
          hint="只使用预先授权的集群凭据；安装向导不会创建或提升集群角色。"
        >
          <UiElement as="input"
            className="mono"
            value={spec.kubeconfig_path}
            onChange={(event) =>
              changeSpec("kubeconfig_path", event.target.value)
            }
            placeholder="/etc/motor-console/agent.kubeconfig"
          />
        </Field>
      )}
      <UiElement as="h3"><UiText>{"Agent HTTPS 接入"}</UiText></UiElement>
      <UiElement as="div" className="rollout-form-grid">
        <Field label="TLS 方式">
          <UiElement as="select"
            value={spec.tls_mode}
            onChange={(event) => {
              changeSpec(
                "tls_mode",
                event.target.value as AgentRolloutSpec["tls_mode"],
              );
              changeSpec("https_proxy_confirmed", false);
            }}
          >
            <UiElement as="option" value="managed_tls"><UiText>{"自动签发与安装 HTTPS 身份"}</UiText></UiElement>
            <UiElement as="option" value="existing_https"><UiText>{"使用现有 HTTPS 入口"}</UiText></UiElement>
          </UiElement>
        </Field>
        <Field label="Agent 监听端口" required>
          <UiElement as="input"
            type="number"
            min={1024}
            max={65535}
            value={spec.agent_port}
            onChange={(event) =>
              changeSpec("agent_port", Number(event.target.value))
            }
          />
        </Field>
      </UiElement>
      <Field
        label="控制台访问 Agent 的 HTTPS 地址"
        required
        hint={
          spec.tls_mode === "managed_tls"
            ? "按管理 IP 与监听端口生成，安装后执行实际连接验证。"
            : "填写已配置好的 HTTPS 反向代理入口。"
        }
      >
        <UiElement as="input"
          className="mono"
          value={spec.agent_url}
          readOnly={spec.tls_mode === "managed_tls"}
          onChange={(event) =>
            changeSpec("agent_url", event.target.value.trim())
          }
        />
      </Field>
      {spec.tls_mode === "existing_https" && (
        <UiElement as="label" className="rollout-checkbox">
          <UiElement as="input"
            type="checkbox"
            checked={spec.https_proxy_confirmed}
            onChange={(event) =>
              changeSpec("https_proxy_confirmed", event.target.checked)
            }
          />
          <UiElement as="span">
            <UiText>{"现有 HTTPS 入口已正确转发到此 Agent，且控制台可以验证它的证书。"}</UiText>
          </UiElement>
        </UiElement>
      )}
      <UiElement as="details" className="rollout-advanced">
        <UiElement as="summary"><UiText>{"资源限额与节点标签"}</UiText></UiElement>
        <UiElement as="div" className="rollout-form-grid">
          <Field label="CPU 上限（核）">
            <UiElement as="input"
              value={spec.cpus}
              onChange={(event) => changeSpec("cpus", event.target.value)}
            />
          </Field>
          <Field label="内存上限">
            <UiElement as="input"
              value={spec.memory}
              onChange={(event) => changeSpec("memory", event.target.value)}
              placeholder="1g"
            />
          </Field>
          <Field label="进程数上限">
            <UiElement as="input"
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
            <UiElement as="input"
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
            <UiElement as="input"
              value={spec.nic_name}
              onChange={(event) => changeSpec("nic_name", event.target.value)}
              placeholder="bond0"
            />
          </Field>
          <Field label="节点标签" hint="使用逗号分隔。">
            <UiElement as="input"
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
        </UiElement>
      </UiElement>
    </UiElement>
  );
}

export const CredentialFields = withUiSlot("component.CredentialFields", DefaultCredentialFields);
export const IdentityFields = withUiSlot("component.IdentityFields", DefaultIdentityFields);
export const ConfigurationFields = withUiSlot("component.ConfigurationFields", DefaultConfigurationFields);

// Reference package implementation exports.
export { DefaultCredentialFields, DefaultIdentityFields, DefaultConfigurationFields };
