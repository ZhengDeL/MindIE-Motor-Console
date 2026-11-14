import { useEffect, useRef, useState } from "react";
import { api, roleName, when } from "./api";
import type { Json, NodeInfo, Placement } from "./api";
import { Button, ErrorBox, Field, InfoBox, Modal, Select, Spinner } from "./ui";
import { observationTime, useObservationClock } from "./resourceObservation";
import { serverNow } from "./serverClock";
import "./placement-plan.css";
import { useRuntimeAccess } from "./consolePlugins";

type Strategy = "pack" | "spread";
type Settings = { strategy: Strategy; hbm: string; revision: number };
type PlacementPlan = {
  valid: boolean;
  placements: Placement[];
  errors: string[];
  warnings: string[];
  exclusions: { node_id: string; reason: string }[];
  evidence: {
    generated_at?: string;
    expires_at?: string;
    strategy?: Strategy;
    basis?: string;
    candidate_nodes?: number;
    candidate_devices?: number;
  };
};
type Preview = {
  plan: PlacementPlan;
  receivedAt: number;
  expiresAt: number;
  monotonicDeadline: number;
  settings: Settings;
};

const MAX_PREVIEW_AGE_MS = 30_000;
const roles = new Set([
  "coordinator_controller",
  "single_container",
  "prefill",
  "decode",
  "union",
  "kv_store",
]);
const portKeys = new Set(["node_manager", "rpc", "base", "kv"]);
const portLabels: Record<string, string> = {
  node_manager: "NodeManager",
  rpc: "DP RPC",
  base: "引擎起始",
  kv: "KV 起始",
};
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown, max = 2000): value is string =>
  typeof value === "string" && value.length <= max;
const messages = (value: unknown): value is string[] =>
  Array.isArray(value) &&
  value.length <= 512 &&
  value.every((item) => text(item));

/** Validate only the public planning contract, and preserve no arbitrary response payload. */
function parsePlan(value: unknown): PlacementPlan {
  if (
    !record(value) ||
    typeof value.valid !== "boolean" ||
    !Array.isArray(value.placements) ||
    value.placements.length > 512 ||
    !messages(value.errors) ||
    !messages(value.warnings) ||
    !Array.isArray(value.exclusions) ||
    value.exclusions.length > 512
  )
    throw Error("资源建议响应格式无效，请重新读取");
  const used = new Set<string>();
  const placements = value.placements.map((item): Placement => {
    if (
      !record(item) ||
      !text(item.node_id, 256) ||
      !item.node_id.trim() ||
      !text(item.role, 64) ||
      !roles.has(item.role) ||
      !text(item.instance_name, 128) ||
      !item.instance_name.trim() ||
      !Array.isArray(item.devices) ||
      item.devices.length > 256 ||
      item.devices.some((id) => !Number.isSafeInteger(id) || id < 0) ||
      new Set(item.devices).size !== item.devices.length ||
      (item.ports !== undefined &&
        (!record(item.ports) ||
          Object.entries(item.ports).some(
            ([key, port]) =>
              !portKeys.has(key) ||
              !Number.isSafeInteger(port) ||
              Number(port) < 1 ||
              Number(port) > 65535,
          )))
    )
      throw Error("资源建议包含无效的节点、设备或端口，请重新读取");
    for (const id of item.devices) {
      const identity = `${item.node_id}:${id}`;
      if (used.has(identity))
        throw Error("资源建议重复分配同一设备，未应用布局");
      used.add(identity);
    }
    return {
      node_id: item.node_id,
      role: item.role,
      instance_name: item.instance_name,
      devices: item.devices as number[],
      ports: (item.ports ?? {}) as Placement["ports"],
    };
  });
  const exclusions = value.exclusions.map((item) => {
    if (!record(item) || !text(item.node_id, 256) || !text(item.reason))
      throw Error("资源建议的排除说明无效");
    return { node_id: item.node_id, reason: item.reason };
  });
  const evidence = record(value.evidence) ? value.evidence : {};
  for (const field of ["generated_at", "expires_at"] as const)
    if (
      evidence[field] !== undefined &&
      observationTime(evidence[field]) === null
    )
      throw Error("资源建议的时间依据无效，请重新读取");
  return {
    valid: value.valid,
    placements,
    errors: value.errors,
    warnings: value.warnings,
    exclusions,
    evidence: {
      ...(text(evidence.generated_at, 64)
        ? { generated_at: evidence.generated_at }
        : {}),
      ...(text(evidence.expires_at, 64)
        ? { expires_at: evidence.expires_at }
        : {}),
      ...(evidence.strategy === "pack" || evidence.strategy === "spread"
        ? { strategy: evidence.strategy }
        : {}),
      ...(text(evidence.basis) ? { basis: evidence.basis } : {}),
      ...(Number.isSafeInteger(evidence.candidate_nodes) &&
      Number(evidence.candidate_nodes) >= 0
        ? { candidate_nodes: Number(evidence.candidate_nodes) }
        : {}),
      ...(Number.isSafeInteger(evidence.candidate_devices) &&
      Number(evidence.candidate_devices) >= 0
        ? { candidate_devices: Number(evidence.candidate_devices) }
        : {}),
    },
  };
}

function minimumHbm(value: string): number | undefined {
  if (!value.trim()) return undefined;
  if (!/^\d+(?:\.\d{1,6})?$/.test(value.trim()))
    throw Error("最低空闲 HBM 请输入非负数，最多 6 位小数");
  const bytes = Math.round(Number(value) * 1024 ** 3);
  if (!Number.isSafeInteger(bytes) || bytes < 0)
    throw Error("最低空闲 HBM 超出支持范围");
  return bytes;
}

export default function PlacementPlanDialog({
  configuration,
  nodes,
  existingCount,
  onClose,
  onApply,
}: {
  configuration: { user_config: Json; env: Json; motor_profile?: string };
  nodes: NodeInfo[];
  existingCount: number;
  onClose: () => void;
  onApply: (placements: Placement[]) => void;
}) {
  const runtimeAccess = useRuntimeAccess("docker");
  const [strategy, setStrategy] = useState<Strategy>("pack");
  const [hbm, setHbm] = useState("");
  const [settings, setSettings] = useState<Settings>({
    strategy: "pack",
    hbm: "",
    revision: 0,
  });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const now = useObservationClock();
  const changed = strategy !== settings.strategy || hbm !== settings.hbm;
  const expired =
    !!preview &&
    (preview.expiresAt <= now ||
      preview.monotonicDeadline <= performance.now());
  const unavailableNode = !!preview?.plan.placements.some((placement) => {
    const node = nodes.find((item) => item.id === placement.node_id);
    return (
      !node || node.docker_available === false || node.status === "offline"
    );
  });
  const canApply =
    runtimeAccess.enabled &&
    !!preview?.plan.valid &&
    preview.plan.placements.length > 0 &&
    !preview.plan.errors.length &&
    !changed &&
    preview.settings.revision === settings.revision &&
    !expired &&
    !unavailableNode &&
    !loading;

  useEffect(() => {
    if (!runtimeAccess.enabled) { setLoading(false); return; }
    const current = ++sequence.current;
    const controller = new AbortController();
    const requestedAt = performance.now();
    const timer = setTimeout(() => controller.abort(), 30_000);
    setLoading(true);
    setError("");
    setPreview(null);
    let bytes: number | undefined;
    try {
      bytes = minimumHbm(settings.hbm);
    } catch (reason) {
      setError((reason as Error).message);
      setLoading(false);
      clearTimeout(timer);
      return;
    }
    api<unknown>("/resources/placement-plan", {
      method: "POST",
      signal: controller.signal,
      body: JSON.stringify({
        ...configuration,
        strategy: settings.strategy,
        ...(bytes === undefined ? {} : { min_free_hbm_bytes: bytes }),
      }),
    })
      .then(parsePlan)
      .then((plan) => {
        if (sequence.current !== current || controller.signal.aborted) return;
        const declaredExpiry = observationTime(plan.evidence.expires_at);
        const receivedAt = serverNow();
        const remaining = Math.max(
          0,
          MAX_PREVIEW_AGE_MS - (performance.now() - requestedAt),
        );
        setPreview({
          plan,
          receivedAt,
          settings,
          monotonicDeadline: requestedAt + MAX_PREVIEW_AGE_MS,
          expiresAt: Math.min(
            receivedAt + remaining,
            declaredExpiry ?? Infinity,
          ),
        });
      })
      .catch((reason) => {
        if (sequence.current !== current) return;
        setError(
          controller.signal.aborted
            ? "读取资源建议超时，请检查节点与控制面连接后重新生成"
            : reason instanceof Error
              ? reason.message
              : "资源建议读取失败",
        );
      })
      .finally(() => {
        clearTimeout(timer);
        if (sequence.current === current) setLoading(false);
      });
    return () => {
      sequence.current++;
      controller.abort();
      clearTimeout(timer);
    };
  }, [configuration, settings, runtimeAccess.enabled]);

  const regenerate = () => {
    if (!runtimeAccess.enabled) { setError(runtimeAccess.reason); return; }
    try {
      minimumHbm(hbm);
      setError("");
      setSettings((value) => ({ strategy, hbm, revision: value.revision + 1 }));
    } catch (reason) {
      setError((reason as Error).message);
    }
  };
  const apply = () => {
    if (!canApply || !preview) return;
    if (
      serverNow() >= preview.expiresAt ||
      performance.now() >= preview.monotonicDeadline
    ) {
      setError("资源建议已过期，请重新生成后应用");
      return;
    }
    onApply(preview.plan.placements);
  };
  const nodeName = (id: string) =>
    nodes.find((node) => node.id === id)?.name || id;
  const plan = preview?.plan;
  return (
    <Modal
      title="Docker 资源建议预览"
      subtitle="可信观测生成建议，确认后应用布局"
      wide
      onClose={onClose}
      footer={
        <>
          <span className="placement-plan-footer-note">
            建议不会预留资源，部署前仍需执行预检。
          </span>
          <Button onClick={onClose}>取消</Button>
          <Button kind="primary" disabled={!canApply} onClick={apply}>
            应用到布局
          </Button>
        </>
      }
    >
      <div className="placement-plan-dialog">
        <div className="placement-plan-settings">
          <Field label="分配策略">
            <Select
              value={strategy}
              onChange={(value) => setStrategy(value as Strategy)}
              ariaLabel="资源建议分配策略"
            >
              <option value="pack">集中部署 · 优先减少宿主</option>
              <option value="spread">分散部署 · 尽量均衡宿主</option>
            </Select>
          </Field>
          <Field
            label="最低空闲 HBM（GiB / 设备）"
            hint="留空采用系统默认，不代表模型显存需求"
          >
            <input
              inputMode="decimal"
              value={hbm}
              maxLength={32}
              onChange={(event) => setHbm(event.target.value)}
              placeholder="采用系统默认"
              aria-label="最低空闲 HBM"
            />
          </Field>
          <Button loading={loading} onClick={regenerate} disabled={!runtimeAccess.enabled}>
            重新生成
          </Button>
        </div>
        {error && <ErrorBox message={error} />}
        {!runtimeAccess.enabled && <InfoBox>{runtimeAccess.reason}分配条件仍保留，原建议不会应用。</InfoBox>}
        {changed && (
          <div className="placement-plan-notice" role="status">
            分配条件已更改，请重新生成建议后应用。
          </div>
        )}
        {expired && (
          <div className="placement-plan-notice" role="status">
            资源建议已过期，请重新生成以读取新的资源观测。
          </div>
        )}
        {unavailableNode && (
          <div className="placement-plan-notice" role="status">
            建议中的节点已移除、离线或不再支持 Docker，请重新生成。
          </div>
        )}
        {loading ? (
          <Spinner label="正在读取可信资源并生成建议" />
        ) : (
          plan && (
            <>
              <div
                className={`placement-plan-result ${plan.valid && plan.placements.length && !plan.errors.length ? "is-valid" : "is-unavailable"}`}
                role="status"
              >
                <strong>
                  {plan.valid && plan.placements.length && !plan.errors.length
                    ? "已生成可供核对的布局建议"
                    : "当前没有满足条件的完整布局"}
                </strong>
                <span>
                  {plan.valid && plan.placements.length
                    ? "确认节点、设备与端口后，再应用到服务。"
                    : "请查看排除原因，检查节点通信配置、采集状态和可用设备后重试；也可返回手工配置并执行预检。"}
                </span>
              </div>
              {plan.errors.length > 0 && (
                <ul className="placement-plan-messages is-error">
                  {plan.errors.map((message, index) => (
                    <li key={index}>{message}</li>
                  ))}
                </ul>
              )}
              {plan.warnings.length > 0 && (
                <ul className="placement-plan-messages">
                  {plan.warnings.map((message, index) => (
                    <li key={index}>{message}</li>
                  ))}
                </ul>
              )}
              {!!plan.placements.length && (
                <>
                  <div className="placement-plan-summary">
                    <span>
                      <b>{plan.placements.length}</b> 个角色布局
                    </span>
                    <span>
                      <b>
                        {
                          new Set(plan.placements.map((item) => item.node_id))
                            .size
                        }
                      </b>{" "}
                      个执行节点
                    </span>
                    <span>
                      <b>
                        {plan.placements.reduce(
                          (count, item) => count + item.devices.length,
                          0,
                        )}
                      </b>{" "}
                      个 NPU 设备声明
                    </span>
                  </div>
                  <p className="placement-plan-scroll-hint">
                    左右滑动查看设备与端口。
                  </p>
                  <div
                    className="table-wrap"
                    role="region"
                    aria-label="建议布局明细"
                    tabIndex={0}
                  >
                    <table>
                      <thead>
                        <tr>
                          <th>角色 / 实例</th>
                          <th>节点</th>
                          <th>NPU 设备</th>
                          <th>端口</th>
                        </tr>
                      </thead>
                      <tbody>
                        {plan.placements.map((placement, index) => (
                          <tr
                            key={`${placement.node_id}-${placement.role}-${placement.instance_name}-${index}`}
                          >
                            <td>
                              <b>{roleName(placement.role)}</b>
                              <span className="cell-secondary mono">
                                {placement.instance_name}
                              </span>
                            </td>
                            <td>
                              {nodeName(placement.node_id)}
                              <span className="cell-secondary tabular">
                                {nodes.find(
                                  (node) => node.id === placement.node_id,
                                )?.address || placement.node_id}
                              </span>
                            </td>
                            <td>
                              {placement.devices.length
                                ? placement.devices
                                    .map((id) => `davinci${id}`)
                                    .join("、")
                                : "不分配 NPU"}
                            </td>
                            <td>
                              {Object.entries(placement.ports).length
                                ? Object.entries(placement.ports).map(
                                    ([key, port]) => (
                                      <span
                                        className="placement-plan-port"
                                        key={key}
                                      >
                                        {portLabels[key] || key}：{port}
                                      </span>
                                    ),
                                  )
                                : "使用原生配置"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {existingCount > 0 && (
                    <InfoBox>
                      应用后将替换当前 {existingCount}{" "}
                      个角色布局；服务配置与环境变量保持不变。
                    </InfoBox>
                  )}
                </>
              )}
              <div className="placement-plan-evidence">
                <span>
                  策略：
                  {preview?.settings.strategy === "spread"
                    ? "分散部署"
                    : "集中部署"}
                </span>
                <span>
                  建议读取于{" "}
                  {when(
                    plan.evidence.generated_at ||
                      new Date(preview!.receivedAt).toISOString(),
                  )}
                </span>
                <span>核对期最长 30 秒，实际部署将重新检查资源。</span>
              </div>
              {plan.evidence.basis && (
                <p className="placement-plan-basis">
                  候选依据：{plan.evidence.basis}
                </p>
              )}
              {plan.evidence.candidate_nodes !== undefined &&
                plan.evidence.candidate_devices !== undefined && (
                  <p className="placement-plan-basis">
                    已核验候选：{plan.evidence.candidate_nodes} 个节点 /{" "}
                    {plan.evidence.candidate_devices} 个设备。
                  </p>
                )}
              {!!plan.exclusions.length && (
                <details
                  className="placement-plan-exclusions"
                  open={!plan.valid}
                >
                  <summary>
                    未纳入候选的节点（{plan.exclusions.length}）
                  </summary>
                  <ul>
                    {plan.exclusions.map((item, index) => (
                      <li key={`${item.node_id}-${index}`}>
                        <b>{nodeName(item.node_id)}</b>
                        <span>{item.reason}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )
        )}
      </div>
    </Modal>
  );
}
