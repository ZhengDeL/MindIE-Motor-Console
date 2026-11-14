import { FileCheck2, FileJson, Layers3 } from "./icons";
import type { Json, NodeInfo, Plan } from "./api";
import { Copy, ErrorBox } from "./ui";

export default function ManifestReview({
  plan,
  nodes,
  extraCount,
  approved,
  onApprovalChange,
  extraManifests,
}: {
  plan: Plan;
  nodes: NodeInfo[];
  extraCount: number;
  approved: boolean;
  onApprovalChange: (approved: boolean) => void;
  extraManifests: Json[];
}) {
  const manifests = plan.manifests ?? [];
  if (!manifests.length && !extraCount) return null;
  return (
    <section className="manifest-review" aria-label="Kubernetes 清单审查">
      <div className="manifest-review-heading">
        <FileCheck2 size={18} />
        <div>
          <h3>Kubernetes 清单审查</h3>
          <p>核对原生渲染结果与本次附加资源，确认后再部署。</p>
        </div>
        {plan.demo && <span className="tag amber">模拟计划</span>}
      </div>
      {manifests.map((manifest, index) => (
        <details
          className="manifest-document"
          key={`${manifest.node_id}-${index}`}
          open={extraCount > 0}
        >
          <summary>
            <Layers3 size={15} />
            <span>
              {nodes.find((node) => node.id === manifest.node_id)?.name ??
                manifest.node_id}
            </span>
            <code>{manifest.namespace}</code>
            <em>{manifest.resources?.length ?? 0} 个资源</em>
          </summary>
          <div className="manifest-identities">
            {manifest.resources?.map((resource, i) => (
              <span key={`${resource.kind}-${resource.name}-${i}`}>
                <b>{resource.kind}</b>
                {resource.name}
              </span>
            ))}
          </div>
          <div className="manifest-code-title">
            <span>最终计划 · YAML</span>
            <Copy value={manifest.manifest_yaml} label="复制清单预览" />
          </div>
          <pre className="manifest-yaml" aria-label="最终清单 YAML">
            {manifest.manifest_yaml || "预检未返回清单内容"}
          </pre>
        </details>
      ))}
      {extraCount > 0 && (
        <>
          {(!plan.extra_manifest_sha256 || !plan.configuration_digest) && (
            <ErrorBox message="预检未返回完整清单与配置摘要，当前计划不能批准部署。请重新预检。" />
          )}
          <div className="manifest-approval">
            <p className="manifest-admin-scope">
              额外资源使用执行节点的管理员权限；Namespace 不会限制 hostPath
              挂载或 privileged 权限，请按资源实际权限审阅。
            </p>
            <details className="manifest-original">
              <summary>原始额外 JSON · {extraCount} 个资源</summary>
              <pre className="manifest-yaml" aria-label="原始额外清单 JSON">
                {JSON.stringify(extraManifests, null, 2)}
              </pre>
            </details>
            <div className="manifest-hash">
              <FileJson size={14} />
              <span>额外清单 SHA-256</span>
              <code>{plan.extra_manifest_sha256 ?? "尚未生成"}</code>
              {plan.extra_manifest_sha256 && (
                <Copy value={plan.extra_manifest_sha256} label="复制清单摘要" />
              )}
            </div>
            <div className="manifest-hash">
              <FileCheck2 size={14} />
              <span>已审阅配置摘要</span>
              <code>{plan.configuration_digest ?? "尚未生成"}</code>
              {plan.configuration_digest && (
                <Copy value={plan.configuration_digest} label="复制配置摘要" />
              )}
            </div>
            <label className="manifest-consent">
              <input
                type="checkbox"
                checked={approved}
                disabled={
                  !plan.extra_manifest_sha256 ||
                  !plan.configuration_digest ||
                  !manifests.length
                }
                onChange={(event) => onApprovalChange(event.target.checked)}
              />
              <span>
                我已审阅本次计划中的资源、权限与挂载，确认部署其中的{" "}
                <b>{extraCount} 个额外资源</b>。
              </span>
            </label>
            <p>
              确认绑定原始额外 JSON 与当前配置摘要，不是含自动时间标签的完整
              YAML 字节；修改配置或重新预检后，需要重新确认。
            </p>
          </div>
        </>
      )}
    </section>
  );
}
