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

export default function ScaleDialog({
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
          <Button onClick={onClose}>取消</Button>
          <Button onClick={preview} loading={busy} disabled={!runtimeAccess.enabled}>
            <Layers3 size={14} />
            {plan ? "重新预览" : "预览调整计划"}
          </Button>
          <Button
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
            确认调整实例数
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {!runtimeAccess.enabled && <InfoBox>{runtimeAccess.reason}已填写的目标实例数仍保留。</InfoBox>}
      <div className="scale-service-summary">
        <div className="service-icon">
          <Layers3 size={21} />
        </div>
        <div>
          <b>{service.name}</b>
          <p>Kubernetes · {config.deploy_mode ?? "原生部署"}</p>
        </div>
        <Badge status={service.status} />
      </div>
      <div className="scale-count-inputs">
        {Object.keys(current).map((key) => (
          <Field
            key={key}
            label={names[key]}
            required
            hint={`当前目标 ${current[key as keyof ScaleCounts]} 个`}
          >
            <input
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
      </div>
      <InfoBox>
        在线调整仅适用于已部署的原生 InferServiceSet。HPA、scalingPolicy
        等已有控制策略由后端检查，避免多个控制器同时改写规模。预览不会修改当前配置；执行成功后保留新的配置修订。
      </InfoBox>
      {plan && (
        <section className="scale-plan" aria-label="实例数调整计划">
          <div className="section-heading">
            <h3>
              <CheckCircle2 size={15} />
              目标规模预览
            </h3>
            {plan.demo && <span className="tag amber">模拟计划</span>}
          </div>
          <div className="scale-diff">
            {Object.entries(plan.counts).map(([key, value]) => (
              <div key={key}>
                <span>{names[key] ?? key}</span>
                <b>{current[key as keyof ScaleCounts] ?? "—"}</b>
                <ArrowRight size={15} />
                <strong>{value}</strong>
                <small>个实例</small>
              </div>
            ))}
          </div>
          {plan.message && <p className="scale-plan-message">{plan.message}</p>}
          {plan.user_config && (
            <details className="command-preview">
              <summary>查看目标原生配置</summary>
              <pre>{JSON.stringify(plan.user_config, null, 2)}</pre>
            </details>
          )}
          <label className="scale-consent">
            <input
              type="checkbox"
              checked={approved}
              disabled={!plan.valid || busy}
              onChange={(event) => setApproved(event.target.checked)}
            />
            <span>我已核对目标规模，确认按此计划调整实例数。</span>
          </label>
        </section>
      )}
    </Modal>
  );
}
