import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  Layers3,
  SlidersHorizontal,
} from "./icons";
import { post } from "./api";
import type { Json, Service } from "./api";
import { Badge, Button, ErrorBox, Field, InfoBox, Modal } from "./ui";
import { useRuntimeAccess } from "./consolePlugins";

export type ScaleCounts = Partial<
  Record<"p_instances_num" | "d_instances_num" | "hybrid_instances_num", number>
>;
type ScalePlan = {
  valid: boolean;
  user_config: Json;
  counts: ScaleCounts;
  message?: string;
  demo?: boolean;
};
const names: Record<string, string> = {
  p_instances_num: "Prefill 实例",
  d_instances_num: "Decode 实例",
  hybrid_instances_num: "PD 混部实例",
};

function DefaultScaleDialog({
  service,
  onClose,
  onScale,
}: {
  service: Service;
  onClose: () => void;
  onScale: (service: Service, counts: ScaleCounts) => Promise<void>;
}) {
  const runtimeAccess = useRuntimeAccess(service.runtime);
  const config = service.user_config.motor_deploy_config ?? {};
  const current: ScaleCounts = service.user_config.motor_engine_union_config
    ? {
        hybrid_instances_num: Number(
          config.hybrid_instances_num ?? service.replicas.union ?? 1,
        ),
      }
    : {
        p_instances_num: Number(
          config.p_instances_num ?? service.replicas.prefill ?? 1,
        ),
        d_instances_num: Number(
          config.d_instances_num ?? service.replicas.decode ?? 1,
        ),
      };
  const [counts, setCounts] = useState<ScaleCounts>(current);
  const [plan, setPlan] = useState<ScalePlan | null>(null);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const preview = async () => {
    if (!runtimeAccess.enabled) { setError(runtimeAccess.reason); return; }
    setError("");
    setApproved(false);
    setPlan(null);
    if (
      Object.values(counts).some(
        (v) => !Number.isInteger(v) || Number(v) < 1 || Number(v) > 16,
      )
    ) {
      setError("目标实例数必须为 1–16 的整数");
      return;
    }
    if (
      Object.entries(counts).every(
        ([key, value]) => value === current[key as keyof ScaleCounts],
      )
    ) {
      setError("请至少调整一个角色的实例数");
      return;
    }
    setBusy(true);
    try {
      setPlan(
        await post<ScalePlan>(`/services/${service.id}/scale/plan`, { counts }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`调整实例数 · ${service.name}`}
      subtitle="通过原生 InferServiceSet 更新目标规模，先预览再执行"
      onClose={onClose}
      footer={
        <>
          <Button actionId="action.ScaleDialog.001" onClick={onClose}><UiText>{"取消"}</UiText></Button>
          <Button actionId="action.ScaleDialog.002" onClick={preview} loading={busy} disabled={!runtimeAccess.enabled}>
            <Layers3 size={14} />
            {plan ? "重新预览" : "预览调整计划"}
          </Button>
          <Button actionId="action.ScaleDialog.003"
            kind="primary"
            disabled={!runtimeAccess.enabled || !plan?.valid || !approved}
            loading={busy}
            onClick={async () => {
              if (!runtimeAccess.enabled) { setError(runtimeAccess.reason); return; }
              if (!plan) return;
              setBusy(true);
              setError("");
              try {
                await onScale(service, counts);
                onClose();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <SlidersHorizontal size={14} />
            <UiText>{"确认调整实例数"}</UiText>
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {!runtimeAccess.enabled && <InfoBox>{runtimeAccess.reason}<UiText>{"已填写的目标实例数仍保留。"}</UiText></InfoBox>}
      <UiElement as="div" className="scale-service-summary">
        <UiElement as="div" className="service-icon">
          <Layers3 size={21} />
        </UiElement>
        <UiElement as="div">
          <UiElement as="b">{service.name}</UiElement>
          <UiElement as="p"><UiText>{"Kubernetes ·"}</UiText> {config.deploy_mode ?? "原生部署"}</UiElement>
        </UiElement>
        <Badge status={service.status} />
      </UiElement>
      <UiElement as="div" className="scale-count-inputs">
        {Object.keys(current).map((key) => (
          <Field
            key={key}
            label={names[key]}
            required
            hint={`当前目标 ${current[key as keyof ScaleCounts]} 个`}
          >
            <UiElement as="input"
              aria-label={`${names[key]}目标数`}
              type="number"
              min="1"
              max="16"
              step="1"
              value={counts[key as keyof ScaleCounts] ?? ""}
              disabled={busy}
              onChange={(event) => {
                setCounts((values) => ({
                  ...values,
                  [key]: Number(event.target.value),
                }));
                setPlan(null);
                setApproved(false);
              }}
            />
          </Field>
        ))}
      </UiElement>
      <InfoBox>
        <UiText>{"在线调整仅适用于已部署的原生 InferServiceSet。HPA、scalingPolicy 等已有控制策略由后端检查，避免多个控制器同时改写规模。预览不会修改当前配置；执行成功后保留新的配置修订。"}</UiText>
      </InfoBox>
      {plan && (
        <UiRegion name="layout.panel" as="section" className="scale-plan" aria-label="实例数调整计划">
          <UiElement as="div" className="section-heading">
            <UiElement as="h3">
              <CheckCircle2 size={15} />
              <UiText>{"目标规模预览"}</UiText>
            </UiElement>
            {plan.demo && <UiElement as="span" className="tag amber"><UiText>{"模拟计划"}</UiText></UiElement>}
          </UiElement>
          <UiElement as="div" className="scale-diff">
            {Object.entries(plan.counts).map(([key, value]) => (
              <UiElement as="div" key={key}>
                <UiElement as="span">{names[key] ?? key}</UiElement>
                <UiElement as="b">{current[key as keyof ScaleCounts] ?? "—"}</UiElement>
                <ArrowRight size={15} />
                <UiElement as="strong">{value}</UiElement>
                <UiElement as="small"><UiText>{"个实例"}</UiText></UiElement>
              </UiElement>
            ))}
          </UiElement>
          {plan.message && <UiElement as="p" className="scale-plan-message">{plan.message}</UiElement>}
          {plan.user_config && (
            <UiElement as="details" className="command-preview">
              <UiElement as="summary"><UiText>{"查看目标原生配置"}</UiText></UiElement>
              <UiElement as="pre">{JSON.stringify(plan.user_config, null, 2)}</UiElement>
            </UiElement>
          )}
          <UiElement as="label" className="scale-consent">
            <UiElement as="input"
              type="checkbox"
              checked={approved}
              disabled={!plan.valid || busy}
              onChange={(event) => setApproved(event.target.checked)}
            />
            <UiElement as="span"><UiText>{"我已核对目标规模，确认按此计划调整实例数。"}</UiText></UiElement>
          </UiElement>
        </UiRegion>
      )}
    </Modal>
  );
}

const ScaleDialog = withUiSlot("component.ScaleDialog", DefaultScaleDialog);
export default ScaleDialog;

// Reference package implementation exports.
export { DefaultScaleDialog };
