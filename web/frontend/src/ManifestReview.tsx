import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { FileCheck2, FileJson, Layers3 } from "./icons";
import type { Json, NodeInfo, Plan } from "./api";
import { Copy, ErrorBox } from "./ui";

function DefaultManifestReview({
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
    <UiRegion name="layout.panel" as="section" className="manifest-review" aria-label="Kubernetes 清单审查">
      <UiElement as="div" className="manifest-review-heading">
        <FileCheck2 size={18} />
        <UiElement as="div">
          <UiElement as="h3"><UiText>{"Kubernetes 清单审查"}</UiText></UiElement>
          <UiElement as="p"><UiText>{"核对原生渲染结果与本次附加资源，确认后再部署。"}</UiText></UiElement>
        </UiElement>
        {plan.demo && <UiElement as="span" className="tag amber"><UiText>{"模拟计划"}</UiText></UiElement>}
      </UiElement>
      {manifests.map((manifest, index) => (
        <UiElement as="details"
          className="manifest-document"
          key={`${manifest.node_id}-${index}`}
          open={extraCount > 0}
        >
          <UiElement as="summary">
            <Layers3 size={15} />
            <UiElement as="span">
              {nodes.find((node) => node.id === manifest.node_id)?.name ??
                manifest.node_id}
            </UiElement>
            <UiElement as="code">{manifest.namespace}</UiElement>
            <UiElement as="em">{manifest.resources?.length ?? 0} <UiText>{"个资源"}</UiText></UiElement>
          </UiElement>
          <UiElement as="div" className="manifest-identities">
            {manifest.resources?.map((resource, i) => (
              <UiElement as="span" key={`${resource.kind}-${resource.name}-${i}`}>
                <UiElement as="b">{resource.kind}</UiElement>
                {resource.name}
              </UiElement>
            ))}
          </UiElement>
          <UiElement as="div" className="manifest-code-title">
            <UiElement as="span"><UiText>{"最终计划 · YAML"}</UiText></UiElement>
            <Copy value={manifest.manifest_yaml} label="复制清单预览" />
          </UiElement>
          <UiElement as="pre" className="manifest-yaml" aria-label="最终清单 YAML">
            {manifest.manifest_yaml || "预检未返回清单内容"}
          </UiElement>
        </UiElement>
      ))}
      {extraCount > 0 && (
        <>
          {(!plan.extra_manifest_sha256 || !plan.configuration_digest) && (
            <ErrorBox message="预检未返回完整清单与配置摘要，当前计划不能批准部署。请重新预检。" />
          )}
          <UiElement as="div" className="manifest-approval">
            <UiElement as="p" className="manifest-admin-scope">
              <UiText>{"额外资源使用执行节点的管理员权限；Namespace 不会限制 hostPath 挂载或 privileged 权限，请按资源实际权限审阅。"}</UiText>
            </UiElement>
            <UiElement as="details" className="manifest-original">
              <UiElement as="summary"><UiText>{"原始额外 JSON ·"}</UiText> {extraCount} <UiText>{"个资源"}</UiText></UiElement>
              <UiElement as="pre" className="manifest-yaml" aria-label="原始额外清单 JSON">
                {JSON.stringify(extraManifests, null, 2)}
              </UiElement>
            </UiElement>
            <UiElement as="div" className="manifest-hash">
              <FileJson size={14} />
              <UiElement as="span"><UiText>{"额外清单 SHA-256"}</UiText></UiElement>
              <UiElement as="code">{plan.extra_manifest_sha256 ?? "尚未生成"}</UiElement>
              {plan.extra_manifest_sha256 && (
                <Copy value={plan.extra_manifest_sha256} label="复制清单摘要" />
              )}
            </UiElement>
            <UiElement as="div" className="manifest-hash">
              <FileCheck2 size={14} />
              <UiElement as="span"><UiText>{"已审阅配置摘要"}</UiText></UiElement>
              <UiElement as="code">{plan.configuration_digest ?? "尚未生成"}</UiElement>
              {plan.configuration_digest && (
                <Copy value={plan.configuration_digest} label="复制配置摘要" />
              )}
            </UiElement>
            <UiElement as="label" className="manifest-consent">
              <UiElement as="input"
                type="checkbox"
                checked={approved}
                disabled={
                  !plan.extra_manifest_sha256 ||
                  !plan.configuration_digest ||
                  !manifests.length
                }
                onChange={(event) => onApprovalChange(event.target.checked)}
              />
              <UiElement as="span">
                <UiText>{"我已审阅本次计划中的资源、权限与挂载，确认部署其中的"}</UiText>{" "}
                <UiElement as="b">{extraCount} <UiText>{"个额外资源"}</UiText></UiElement><UiText>{"。"}</UiText>
              </UiElement>
            </UiElement>
            <UiElement as="p">
              <UiText>{"确认绑定原始额外 JSON 与当前配置摘要，不是含自动时间标签的完整 YAML 字节；修改配置或重新预检后，需要重新确认。"}</UiText>
            </UiElement>
          </UiElement>
        </>
      )}
    </UiRegion>
  );
}

const ManifestReview = withUiSlot("component.ManifestReview", DefaultManifestReview);
export default ManifestReview;

// Reference package implementation exports.
export { DefaultManifestReview };
