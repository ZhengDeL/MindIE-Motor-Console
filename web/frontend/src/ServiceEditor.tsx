import { assertProductImport, isNativeMotorConfig } from "./productImports";
import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Box,
  Check,
  CheckCircle2,
  FileJson,
  Layers3,
  Plus,
  Rocket,
  Settings2,
  ShieldCheck,
  Trash2,
  Upload,
} from "./icons";
import {
  api,
  isReadOnlyAgent,
  parseStrictJson,
  readJsonFile,
  post,
  roleName,
  validateJsonNumbers,
} from "./api";
import type {
  DeploymentApproval,
  Json,
  NodeInfo,
  Placement,
  Plan,
  Service,
  Template,
  TemplateInput,
} from "./api";
import { TemplateEditor, templateInput, templateSource } from "./TemplatesPage";
import ManifestReview from "./ManifestReview";
import ClusterNodesPanel, { runtimeAvailable } from "./ClusterNodes";
import { usePluginEnabled, useRuntimeAccess } from "./consolePlugins";
import type { SchedulingChoice } from "./ClusterNodes";
import NativeAigwEditor, { applyNativeAigwField } from "./IntegrationAigw";
import ImageContractPanel from "./IntegrationImages";
import { readNotice, type ReadState } from "./readState";
import PlacementPlanDialog from "./PlacementPlanDialog";
import { useUnsavedChanges } from "./unsavedChanges";
import {
  Badge,
  Button,
  Confirm,
  Empty,
  ErrorBox,
  Field,
  InfoBox,
  JsonEditor,
  Modal,
  Select,
  Spinner,
} from "./ui";

type Row = Placement & { devices_text: string };
const baseConfig = {
  version: "v2.0",
  motor_deploy_config: {
    deploy_mode: "infer_service_set",
    image_name: "",
    hardware_type: "800I_A3",
    weight_mount_path: "/mnt/weight",
    hybrid_instances_num: 1,
    single_hybrid_instance_pod_num: 1,
    hybrid_pod_npu_num: 1,
  },
  motor_controller_config: {},
  motor_coordinator_config: {},
  motor_engine_union_config: {
    engine_type: "vllm",
    engine_config: {
      model: "/mnt/weight/model",
      served_model_name: "model",
      tensor_parallel_size: 1,
      data_parallel_size: 1,
    },
  },
};
const initialPorts = (i: number) => ({
  node_manager: 4026 + i * 100,
  rpc: 9000 + i * 100,
  base: 10000 + i * 1000,
  kv: 21000 + i * 1000,
});
const toRow = (p: Placement): Row => ({
  ...p,
  ports: p.ports ?? {},
  devices_text: (p.devices ?? []).join(","),
});
const steps = [
  { title: "选择模板", sub: "运行环境与模型", icon: Box },
  { title: "服务配置", sub: "原生配置与环境变量", icon: FileJson },
  { title: "资源布局", sub: "节点、角色与设备", icon: Layers3 },
  { title: "检查与部署", sub: "预检与命令预览", icon: ShieldCheck },
];

export default function ServiceEditor({
  nodes,
  nodesRead,
  original,
  initialTemplate,
  onClose,
  onSaved,
  onDeploy,
}: {
  nodes: NodeInfo[];
  nodesRead: ReadState;
  original?: Service;
  initialTemplate?: Template;
  onClose: () => void;
  onSaved: () => void;
  onDeploy: (service: Service, approval?: DeploymentApproval) => Promise<void>;
}) {
  const templatesEnabled = usePluginEnabled("templates");
  const imagesEnabled = usePluginEnabled("images");
  const gatewaysEnabled = usePluginEnabled("gateways");
  const dockerEnabled = usePluginEnabled("motor-docker");
  const kubernetesEnabled = usePluginEnabled("motor-kubernetes");
  const [step, setStep] = useState(original || initialTemplate ? 1 : 0);
  const [runtime, setRuntime] = useState<"docker" | "kubernetes">(
    original?.runtime ?? initialTemplate?.runtime ?? (dockerEnabled ? "docker" : "kubernetes"),
  );
  const runtimeAccess = useRuntimeAccess(runtime);
  const submissionAllowed = useRef(runtimeAccess.enabled);
  submissionAllowed.current = runtimeAccess.enabled;
  const unavailableDraftMessage = "所选运行时插件当前不可用，已暂停保存、预检和部署。本地输入仍保留；可以复制配置，或选择已启用的运行时后继续。";
  const [name, setName] = useState(original?.name ?? "");
  const [motorProfile, setMotorProfile] = useState(
    original?.motor_profile ?? initialTemplate?.motor_profile ?? "",
  );
  const [userText, setUserText] = useState(
    JSON.stringify(
      original?.user_config ?? initialTemplate?.user_config ?? baseConfig,
      null,
      2,
    ),
  );
  const [envText, setEnvText] = useState(
    JSON.stringify(
      original?.env ??
        initialTemplate?.env ?? {
          version: "2.0.0",
          motor_common_env: { CANN_INSTALL_PATH: "/usr/local/Ascend" },
          motor_engine_union_env: {},
        },
      null,
      2,
    ),
  );
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templatesLoaded, setTemplatesLoaded] = useState(false);
  const [extraText, setExtraText] = useState(
    JSON.stringify(
      original?.extra_manifests ?? initialTemplate?.extra_manifests ?? [],
      null,
      2,
    ),
  );
  const [extraApproved, setExtraApproved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(initialTemplate?.id ?? "");
  const [templateSourceFilter, setTemplateSourceFilter] = useState(
    initialTemplate ? templateSource(initialTemplate) : "all",
  );
  const [templateSeed, setTemplateSeed] = useState<TemplateInput | null>(null);
  const [templateSavedName, setTemplateSavedName] = useState("");
  const [configTab, setConfigTab] = useState("user");
  const [error, setError] = useState("");
  const [rows, setRows] = useState<Row[]>(
    (original?.placements ?? []).map(toRow),
  );
  const [resourceProposal, setResourceProposal] = useState<{
    user_config: Json;
    env: Json;
    motor_profile?: string;
  } | null>(null);
  const [saved, setSaved] = useState<Service | undefined>(original);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  useUnsavedChanges(dirty);
  const [discardOpen, setDiscardOpen] = useState(false);
  const requestClose = () => {
    if (busy) return;
    if (dirty) setDiscardOpen(true);
    else onClose();
  };
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!templatesEnabled) { setTemplates([]); setLoading(false); return; }
    let active = true;
    api<{ items: Template[] }>("/templates")
      .then((x) => { if (active) { setTemplates(x.items); setTemplatesLoaded(true); } })
      .catch((e) => { if (active) setError(e.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [templatesEnabled]);
  const change = () => {
    setDirty(true);
    setPlan(null);
    setExtraApproved(false);
    setTemplateSavedName("");
  };
  const parseExtras = (): Json[] => {
    const values = parseStrictJson(extraText);
    if (
      !Array.isArray(values) ||
      values.some(
        (value) => !value || typeof value !== "object" || Array.isArray(value),
      )
    )
      throw Error(
        "额外清单必须是 JSON 对象数组，每个对象代表一个 Kubernetes 资源",
      );
    if (runtime !== "kubernetes" && values.length)
      throw Error(
        "Docker 不支持额外 Kubernetes 清单，请选择 Kubernetes 或将额外清单清空为 []",
      );
    return values;
  };
  const parsed = (): { user: Json; env: Json } => {
    const user = parseStrictJson(userText);
    const env = parseStrictJson(envText);
    if (
      !user ||
      Array.isArray(user) ||
      typeof user !== "object" ||
      !env ||
      Array.isArray(env) ||
      typeof env !== "object"
    )
      throw Error("服务配置与环境变量都必须是 JSON 对象");
    if (!user.motor_deploy_config)
      throw Error("服务配置缺少 motor_deploy_config");
    return { user, env };
  };
  const pick = (t: Template) => {
    if (t.config_redacted) {
      setError("当前账号无权读取完整模板配置");
      return;
    }
    try {
      validateJsonNumbers(t);
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    setSelected(t.id);
    setUserText(JSON.stringify(t.user_config, null, 2));
    setEnvText(JSON.stringify(t.env, null, 2));
    setExtraText(JSON.stringify(t.extra_manifests ?? [], null, 2));
    setMotorProfile(t.motor_profile ?? "");
    if (templateSource(t) === "custom" && t.runtime) setRuntime(t.runtime);
    setRows([]);
    if (!name)
      setName(
        t.name
          .toLowerCase()
          .replace(/[^a-z0-9-]+/g, "-")
          .replace(/^-|-$/g, "")
          .slice(0, 48) || "model-service",
      );
    change();
  };
  const updateDeploy = (key: string, value: string) => {
    try {
      const { user } = parsed();
      user.motor_deploy_config[key] = value;
      setUserText(JSON.stringify(user, null, 2));
      change();
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  let currentConfig: Json = {};
  try {
    const candidate = parseStrictJson(userText);
    if (candidate && typeof candidate === "object" && !Array.isArray(candidate))
      currentConfig = candidate;
  } catch {}
  const deployment = currentConfig.motor_deploy_config ?? {};
  const runtimeNodes = nodes.filter((node) => runtimeAvailable(node, runtime));
  const selectedAgent = nodes.find((node) => node.id === rows[0]?.node_id);
  const selectHost = (field: string, hostname: string) => {
    try {
      const { user } = parsed();
      const existing = user.motor_deploy_config[field];
      if (
        existing !== undefined &&
        (!existing ||
          Array.isArray(existing) ||
          typeof existing !== "object" ||
          Object.values(existing).some((value) => typeof value !== "string"))
      )
        throw Error("节点选择器必须为字符串键值对象，请先在服务配置中修正");
      const selector = { ...existing };
      if (hostname) selector["kubernetes.io/hostname"] = hostname;
      else delete selector["kubernetes.io/hostname"];
      user.motor_deploy_config[field] = selector;
      setUserText(JSON.stringify(user, null, 2));
      change();
      setError("");
    } catch (reason) {
      setError((reason as Error).message);
    }
  };
  const scheduling: SchedulingChoice[] =
    deployment.deploy_mode === "single_container"
      ? []
      : [
          ["controller_node_selector", "Controller"],
          ["coordinator_node_selector", "Coordinator"],
          ...(currentConfig.motor_engine_encode_config
            ? [["encode_node_selector", "Encode"]]
            : []),
          ...(currentConfig.motor_engine_prefill_config
            ? [["prefill_node_selector", "Prefill"]]
            : []),
          ...(currentConfig.motor_engine_decode_config
            ? [["decode_node_selector", "Decode"]]
            : []),
          ...((
            currentConfig.motor_engine_prefill_config ??
            currentConfig.motor_engine_union_config
          )?.engine_config?.kv_transfer_config?.kv_connector ===
            "MultiConnector" ||
          (currentConfig.kv_cache_store_config &&
            typeof currentConfig.kv_cache_store_config === "object" &&
            !Array.isArray(currentConfig.kv_cache_store_config) &&
            Object.keys(currentConfig.kv_cache_store_config).length > 0)
            ? [["kv_pool_node_selector", "KV Pool"]]
            : []),
          ...(currentConfig.kv_conductor_config?.http_server_port != null &&
          currentConfig.kv_conductor_config.http_server_port !== 0
            ? [["kv_conductor_node_selector", "KV Conductor"]]
            : []),
          ...(deployment.deploy_mode === "multi_deployment" &&
          (
            currentConfig.motor_engine_prefill_config ??
            currentConfig.motor_engine_union_config
          )?.engine_type === "sglang"
            ? [["mf_store_node_selector", "MF Store"]]
            : []),
        ].map(([key, label]) => ({ key, label, selector: deployment[key] }));
  const modelSection =
    currentConfig.motor_engine_union_config ??
    currentConfig.motor_engine_prefill_config ??
    currentConfig.motor_engine_decode_config;
  const scenario = currentConfig.motor_engine_union_config
    ? "hybrid"
    : "separate";
  const supportsKv = (role: string) =>
    Boolean(
      currentConfig[`motor_engine_${role}_config`]?.engine_config
        ?.kv_transfer_config,
    );
  const supportsPorts = (role: string) =>
    ["prefill", "decode", "union"].includes(role);
  const editRow = (i: number, part: Partial<Row>) => {
    setRows((x) => x.map((r, j) => (j === i ? { ...r, ...part } : r)));
    change();
  };
  const addRow = () => {
    setRows((x) => [
      ...x,
      {
        node_id: runtimeNodes[0]?.id ?? "",
        role: "prefill",
        instance_name: `p${x.filter((y) => y.role === "prefill").length}`,
        devices: [],
        devices_text: "",
        ports: initialPorts(x.length),
      },
    ]);
    change();
  };
  const generateLayout = () => {
    try {
      if (!submissionAllowed.current) throw Error(unavailableDraftMessage);
      const { user, env } = parsed();
      const cfg = user.motor_deploy_config;
      if (runtime === "docker") {
        setResourceProposal({
          user_config: user,
          env,
          ...(motorProfile ? { motor_profile: motorProfile } : {}),
        });
        setError("");
        return;
      }
      if (!runtimeNodes.length)
        throw Error("请先接入支持 Kubernetes 的执行 Agent，再生成布局");
      const single = cfg.deploy_mode === "single_container";
      const all: Row[] = [
        {
          node_id:
            runtimeNodes.find((node) => node.status === "online")?.id ??
            runtimeNodes[0].id,
          role: single ? "single_container" : "coordinator_controller",
          instance_name: single ? "single" : "ctrl",
          devices: [],
          devices_text: "",
          ports: {},
        },
      ];
      setRows(all);
      change();
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const importFile = async (file: File) => {
    try {
      const value = await readJsonFile(file);
      assertProductImport(value);
      if (isNativeMotorConfig(value)) {
        setUserText(JSON.stringify(value, null, 2));
      } else if (value.user_config) {
        setUserText(JSON.stringify(value.user_config, null, 2));
        setEnvText(JSON.stringify(value.env ?? {}, null, 2));
        setExtraText(JSON.stringify(value.extra_manifests ?? [], null, 2));
        if (value.name) setName(value.name);
        if (typeof value.motor_profile === "string")
          setMotorProfile(value.motor_profile);
        if (value.runtime === "docker" || value.runtime === "kubernetes")
          setRuntime(value.runtime);
        if (Array.isArray(value.placements))
          setRows(value.placements.map(toRow));
      } else if (configTab === "extra" && Array.isArray(value))
        setExtraText(JSON.stringify(value, null, 2));
      else if (configTab === "env") setEnvText(JSON.stringify(value, null, 2));
      else throw Error("请选择 Motor user_config.json 或控制台导出的完整配置");
      setSelected("");
      change();
      setError("");
    } catch (e) {
      setError(`导入失败：${(e as Error).message}`);
    }
  };
  const save = async () => {
    if (!submissionAllowed.current) throw Error(unavailableDraftMessage);
    const { user, env } = parsed();
    const extra_manifests = parseExtras();
    if (!name.trim()) throw Error("请填写服务名称");
    const placements: Placement[] = rows.map((r) => {
      const text = r.devices_text.trim();
      if (text && !/^\d+(\s*,\s*\d+)*$/.test(text))
        throw Error(`实例 ${r.instance_name} 的设备编号格式不正确`);
      const devices = text ? text.split(",").map(Number) : [];
      if (new Set(devices).size !== devices.length)
        throw Error(`实例 ${r.instance_name} 存在重复设备编号`);
      if (!r.node_id) throw Error("请为每个实例选择节点");
      const node = nodes.find((item) => item.id === r.node_id);
      if (!node || !runtimeAvailable(node, runtime))
        throw Error(
          `执行 Agent 不支持当前 ${runtime === "docker" ? "Docker" : "Kubernetes"} 运行时，请重新选择`,
        );
      const ports =
        supportsPorts(r.role) && runtime === "docker"
          ? Object.fromEntries(
              Object.entries(r.ports).filter(
                ([key, value]) =>
                  value != null && (key !== "kv" || supportsKv(r.role)),
              ),
            )
          : {};
      return {
        node_id: r.node_id,
        role: r.role,
        instance_name: r.instance_name,
        devices,
        ports,
      };
    });
    if (!placements.length)
      throw Error(
        runtime === "kubernetes"
          ? "请选择一个执行节点"
          : "请添加角色与节点布局",
      );
    const body = {
      name: name.trim(),
      runtime,
      user_config: user,
      env,
      placements,
      extra_manifests,
      motor_profile: motorProfile,
      ...(saved ? { expected_revision: saved.config_revision } : {}),
    };
    if (saved && (!Number.isSafeInteger(saved.config_revision) || saved.config_revision < 1))
      throw Error("当前配置缺少有效修订号。请保留草稿，重新打开服务后再保存。");
    const result = await api<Service>(
      saved ? `/services/${saved.id}` : "/services",
      { method: saved ? "PUT" : "POST", body: JSON.stringify(body) },
    );
    setSaved(result);
    setMotorProfile(result.motor_profile ?? motorProfile);
    setDirty(false);
    onSaved();
    return result;
  };
  const runCheck = async () => {
    if (!submissionAllowed.current) { setError(unavailableDraftMessage); return; }
    setError("");
    setBusy(true);
    setExtraApproved(false);
    setPlan(null);
    try {
      const s = dirty || !saved ? await save() : saved;
      if (!submissionAllowed.current) throw Error(unavailableDraftMessage);
      const result = await post<Plan>(`/services/${s.id}/plan`);
      setPlan(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const saveDraft = async () => {
    setBusy(true);
    setError("");
    try {
      await save();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const next = () => {
    setError("");
    try {
      if (!submissionAllowed.current) throw Error(unavailableDraftMessage);
      if (step === 1) {
        parsed();
        parseExtras();
        if (!name.trim()) throw Error("请填写服务名称");
      }
      if (step === 2 && rows.length === 0) throw Error("请先添加执行布局");
      setStep(Math.min(3, step + 1));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const displayed = templates.filter(
    (t) =>
      (templateSourceFilter === "all" ||
        templateSource(t) === templateSourceFilter) &&
      `${t.name} ${t.engine} ${t.hardware} ${t.scenario}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  if (templateSeed && templatesEnabled)
    return (
      <TemplateEditor
        initial={templateSeed}
        title="保存当前配置为模板"
        onClose={() => setTemplateSeed(null)}
        onSaved={(template) => {
          setTemplateSeed(null);
          setTemplateSavedName(template.name);
          setTemplates((current) => [
            ...current.filter((item) => item.id !== template.id),
            template,
          ]);
        }}
      />
    );
  return (
    <Modal
      title={original ? "编辑推理服务" : "创建推理服务"}
      subtitle="原生配置、执行布局与部署预检"
      onClose={requestClose}
      closeDisabled={busy}
      wide
      footer={
        <>
          <div className="wizard-footer-meta">
            {saved ? (
              <>
                <CheckCircle2 size={14} />
                已保存草稿 {saved.name}
                {dirty ? " · 有未保存更改" : ""}
              </>
            ) : (
              <>配置将在预检时保存为草稿</>
            )}
          </div>
          <Button onClick={requestClose} disabled={busy}>取消</Button>
          {step > 0 && (
            <Button disabled={busy} onClick={() => setStep(step - 1)}>
              <ArrowLeft size={14} />
              上一步
            </Button>
          )}
          {step < 3 ? (
            <Button kind="primary" disabled={!runtimeAccess.enabled} onClick={next}>
              下一步
              <ArrowRight size={14} />
            </Button>
          ) : (
            <>
              <Button onClick={saveDraft} loading={busy} disabled={!runtimeAccess.enabled}>
                保存草稿
              </Button>
              <Button onClick={runCheck} loading={busy} disabled={!runtimeAccess.enabled}>
                <ShieldCheck size={14} />
                {plan ? "重新预检" : "执行预检"}
              </Button>
              <Button
                kind="primary"
                disabled={
                  !runtimeAccess.enabled ||
                  !plan?.valid ||
                  dirty ||
                  !saved ||
                  Boolean(
                    saved.extra_manifests?.length &&
                    (!extraApproved ||
                      !plan.extra_manifest_sha256 ||
                      !plan.configuration_digest ||
                      !plan.manifests?.length),
                  )
                }
                loading={busy}
                onClick={async () => {
                  if (!submissionAllowed.current) { setError(unavailableDraftMessage); return; }
                  if (!saved) return;
                  setBusy(true);
                  try {
                    await onDeploy(
                      saved,
                      saved.extra_manifests?.length &&
                        plan?.extra_manifest_sha256
                        ? {
                            extra_manifests_approved_sha256:
                              plan.extra_manifest_sha256,
                            reviewed_configuration_digest:
                              plan.configuration_digest ?? undefined,
                          }
                        : undefined,
                    );
                    onClose();
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <Rocket size={14} />
                {saved?.needs_rebuild ? "重建并应用配置" : "开始部署"}
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="wizard-steps">
        {steps.map((s, i) => (
          <button
            key={s.title}
            disabled={busy}
            className={`${step === i ? "current" : ""} ${step > i ? "done" : ""}`}
            onClick={() => {
              if (i <= step) setStep(i);
            }}
          >
            <span className="step-dot">
              {step > i ? <Check size={16} /> : i + 1}
            </span>
            <span>
              <b>{s.title}</b>
              <small>{s.sub}</small>
            </span>
            {i < 3 && <i className="step-line" />}
          </button>
        ))}
      </div>
      {error && <ErrorBox message={error} />}
      {!runtimeAccess.enabled && <InfoBox>{unavailableDraftMessage}</InfoBox>}
      {step === 0 && (
        <div className="wizard-content">
          <div className="form-section-title">
            <span>运行环境</span>
            <small>选择服务的部署方式</small>
          </div>
          <div className="runtime-options">
            {[
              {
                id: "docker",
                name: "Docker",
                desc: "直接管理多节点容器，明确分配角色与 NPU",
                icon: Box,
              },
              {
                id: "kubernetes",
                name: "Kubernetes",
                desc: "通过集群调度，接入 Motor 完整能力",
                icon: Layers3,
              },
            ].filter((r) => r.id === "docker" ? dockerEnabled : kubernetesEnabled).map((r) => (
              <button
                key={r.id}
                className={`runtime-option ${runtime === r.id ? "selected" : ""}`}
                onClick={() => {
                  setRuntime(r.id as "docker" | "kubernetes");
                  setRows([]);
                  change();
                }}
              >
                <div className="runtime-icon">
                  <r.icon size={24} />
                </div>
                <div>
                  <b>{r.name}</b>
                  <small>{r.desc}</small>
                </div>
                <span className="radio-dot" />
              </button>
            ))}
          </div>
          {templatesEnabled && <><div className="form-section-title">
            <span>模型配置模板</span>
            <small>官方原生配置与已保存的自定义模板</small>
            <div className="template-search">
              <input
                placeholder="搜索模型 / 硬件 / 引擎"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>
          <div className="tab-list compact template-source-tabs">
            {[
              ["all", "全部模板"],
              ["official", "官方模板"],
              ["custom", "自定义模板"],
            ].map(([id, label]) => (
              <button
                key={id}
                className={templateSourceFilter === id ? "active" : ""}
                onClick={() => setTemplateSourceFilter(id)}
              >
                {label}
              </button>
            ))}
          </div>
          {loading ? (
            <Spinner label="正在获取模板" />
          ) : displayed.length ? (
            <div className="template-grid">
              {displayed.map((t) => (
                <button
                  key={t.id}
                  className={`template-card ${selected === t.id ? "selected" : ""}`}
                  onClick={() => pick(t)}
                  disabled={t.config_redacted}
                >
                  <div className="template-card-icon">
                    <Box size={20} />
                  </div>
                  <div className="template-card-info">
                    <b>{t.name}</b>
                    <div>
                      <span>
                        {templateSource(t) === "custom" ? "自定义" : "官方"}
                      </span>
                      <span>{t.hardware}</span>
                      <span>{t.engine}</span>
                      <span>
                        {t.scenario === "hybrid" ? "PD 混部" : "PD 分离"}
                      </span>
                    </div>
                  </div>
                  <span className="radio-dot" />
                </button>
              ))}
            </div>
          ) : (
            <Empty
              title={templatesLoaded ? "没有匹配的模板" : "模板列表尚未读取"}
              description={templatesLoaded ? "可以调整搜索条件，或在下一步导入自己的原生配置。" : "请检查错误提示；也可跳过模板，在下一步导入原生配置。"}
            />
          )}
          <div className="subtle-note">
            <FileJson size={14} />
            也可以跳过模板，在下一步导入已有配置；所有原生字段都会保留。
          </div>
          </>}
          {!templatesEnabled && <InfoBox>模板插件未启用。下一步可导入或编辑完整原生 JSON 配置。</InfoBox>}
        </div>
      )}
      {step === 1 && (
        <div className="wizard-content">
          {templateSavedName && (
            <InfoBox>已保存为模板“{templateSavedName}”。</InfoBox>
          )}
          {initialTemplate && selected === initialTemplate.id && (
            <div className="template-selected-note">
              <FileJson size={14} />
              当前模板：{initialTemplate.name}
            </div>
          )}
          <div className="form-two">
            <Field
              label="服务名称"
              required
              hint="建议使用容易识别的模型与环境名称"
            >
              <input
                placeholder="例如 glm-production"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  change();
                }}
              />
            </Field>
            <Field label="服务镜像" required>
              <input
                placeholder="完整 Motor 镜像名称或 ID"
                value={deployment.image_name ?? ""}
                onChange={(e) => updateDeploy("image_name", e.target.value)}
              />
            </Field>
            <Field label="硬件类型">
              <Select
                value={deployment.hardware_type ?? "800I_A3"}
                onChange={(v) => updateDeploy("hardware_type", v)}
              >
                <option value="800I_A3">Atlas 800I A3</option>
                <option value="800I_A2">Atlas 800I A2</option>
                <option value="950PR">Ascend 950PR</option>
                {deployment.hardware_type &&
                  !["800I_A3", "800I_A2", "950PR"].includes(
                    deployment.hardware_type,
                  ) && (
                    <option value={deployment.hardware_type}>
                      {deployment.hardware_type}
                    </option>
                  )}
              </Select>
            </Field>
            <Field
              label="Motor 兼容配置"
              hint="已有服务与导入配置保留绑定；新服务由后端匹配已探测的执行节点"
            >
              <input
                aria-label="Motor 兼容配置"
                readOnly
                value={motorProfile}
                placeholder="保存时匹配执行节点"
              />
            </Field>
            <Field label="权重挂载路径">
              <input
                value={deployment.weight_mount_path ?? ""}
                placeholder="/mnt/weight"
                onChange={(e) =>
                  updateDeploy("weight_mount_path", e.target.value)
                }
              />
            </Field>
          </div>
          {imagesEnabled && <ImageContractPanel
            image={
              typeof deployment.image_name === "string"
                ? deployment.image_name
                : ""
            }
            hardware={deployment.hardware_type}
            runtime={runtime}
            nodes={nodes}
            nodesRead={nodesRead}
            onSelectImage={(value) => updateDeploy("image_name", value)}
          />}
          {gatewaysEnabled && <NativeAigwEditor
            config={currentConfig}
            onChange={(field, value) => {
              try {
                const { user } = parsed();
                setUserText(
                  JSON.stringify(
                    applyNativeAigwField(user, field, value),
                    null,
                    2,
                  ),
                );
                change();
                setError("");
              } catch (reason) {
                setError((reason as Error).message);
              }
            }}
            onError={setError}
          />}
          <div className="editor-toolbar">
            <div className="tab-list compact">
              <button
                className={configTab === "user" ? "active" : ""}
                onClick={() => setConfigTab("user")}
              >
                服务配置 <span>user_config.json</span>
              </button>
              <button
                className={configTab === "env" ? "active" : ""}
                onClick={() => setConfigTab("env")}
              >
                环境变量 <span>env.json</span>
              </button>
              <button
                className={configTab === "extra" ? "active" : ""}
                onClick={() => setConfigTab("extra")}
              >
                高级清单 <span>Kubernetes</span>
              </button>
            </div>
            <div className="template-editor-actions">
              {templatesEnabled && <Button
                onClick={() => {
                  try {
                    const { user, env } = parsed();
                    setTemplateSeed(
                      templateInput({
                        name: name ? `${name} 模板` : "",
                        runtime,
                        motor_profile: motorProfile,
                        user_config: user,
                        env,
                        extra_manifests: parseExtras(),
                      }),
                    );
                    setError("");
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                <FileJson size={14} />
                保存为模板
              </Button>}
              <Button onClick={() => fileRef.current?.click()}>
                <Upload size={14} />
                导入 JSON
              </Button>
            </div>
            <input
              ref={fileRef}
              hidden
              type="file"
              accept=".json,application/json"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void importFile(f);
                e.target.value = "";
              }}
            />
          </div>
          {configTab === "extra" && (
            <InfoBox>
              以 JSON 对象数组填写额外 Kubernetes 资源；留空列表 []
              表示不添加。资源使用执行节点的管理员权限提交；命名空间不限制
              hostPath 或
              privileged。请核对资源权限、挂载与安全上下文，部署前需单独确认。Web
              不会自动增加权限或修改原始 Motor 配置。
            </InfoBox>
          )}
          <JsonEditor
            label={
              configTab === "user"
                ? "user_config.json"
                : configTab === "env"
                  ? "env.json"
                  : "extra_manifests.json"
            }
            value={
              configTab === "user"
                ? userText
                : configTab === "env"
                  ? envText
                  : extraText
            }
            onChange={(v) => {
              configTab === "user"
                ? setUserText(v)
                : configTab === "env"
                  ? setEnvText(v)
                  : setExtraText(v);
              change();
            }}
          />
          <div className="subtle-note">
            <Settings2 size={14} />
            支持直接编辑全部 Motor 配置字段。可视化表单与 JSON 编辑器保持同步。
          </div>
        </div>
      )}
      {step === 2 && (
        <div className="wizard-content">
          <div className="section-heading">
            <div>
              <h3>
                {runtime === "docker" ? "多节点实例布局" : "集群执行入口"}
              </h3>
              <p>
                {runtime === "docker"
                  ? "为管理面与每个推理实例选择节点、设备和端口。"
                  : "选择持有 kubeconfig 的执行 Agent。实际 Pod 调度由原生配置与集群调度器决定。"}
              </p>
            </div>
            <Button onClick={generateLayout} disabled={!runtimeAccess.enabled || !nodesRead.loaded}>
              <Layers3 size={14} />
              {runtime === "docker" ? "按配置生成布局" : "设置执行入口"}
            </Button>
          </div>
          {!nodesRead.loaded ? <InfoBox>{readNotice(nodesRead)}节点列表；取得节点连接信息后再规划布局。</InfoBox> : !runtimeNodes.length && (
            <InfoBox>
              还没有接入节点，请先在“节点管理”中注册并探测
              Agent，再回来分配资源。
            </InfoBox>
          )}
          {rows.length ? (
            <div className="placement-list">
              {rows.map((r, i) => (
                <div className="placement-editor" key={i}>
                  <div className="placement-editor-head">
                    <span className={`role-chip role-${r.role}`}>
                      <Box size={13} />
                      {roleName(r.role)}
                    </span>
                    <b>{r.instance_name || "未命名实例"}</b>
                    <button
                      className="icon-btn danger-text"
                      aria-label={`删除实例布局 ${r.instance_name}`}
                      onClick={() => {
                        setRows((x) => x.filter((_, j) => j !== i));
                        change();
                      }}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                  <div className="placement-fields">
                    <Field label="角色">
                      <Select
                        value={r.role}
                        disabled={runtime === "kubernetes"}
                        onChange={(v) =>
                          editRow(i, {
                            role: v,
                            devices_text:
                              v === "coordinator_controller"
                                ? ""
                                : r.devices_text,
                          })
                        }
                      >
                        <option value="coordinator_controller">管理面</option>
                        <option value="single_container">单容器全组件</option>
                        {runtime === "docker" && (
                          <>
                            <option value="prefill">Prefill</option>
                            <option value="decode">Decode</option>
                            <option value="union">PD 混部</option>
                            <option value="kv_store">KV Store</option>
                          </>
                        )}
                      </Select>
                    </Field>
                    <Field
                      label={
                        runtime === "kubernetes" ? "执行 Agent" : "部署节点"
                      }
                    >
                      <Select
                        value={r.node_id}
                        onChange={(v) => editRow(i, { node_id: v })}
                      >
                        <option value="">请选择节点</option>
                        {nodes.some(
                          (n) =>
                            n.id === r.node_id && !runtimeAvailable(n, runtime),
                        ) && (
                          <option value={r.node_id} disabled>
                            当前 Agent 不支持此运行时，请重新选择
                          </option>
                        )}
                        {runtimeNodes.map((n) => (
                          <option key={n.id} value={n.id}>
                            {n.name} · {n.address}
                            {isReadOnlyAgent(n) ? "（只读接入）" : ""}
                            {n.status === "online"
                              ? ""
                              : "（未在线，请先探测）"}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    {runtime === "docker" && (
                      <>
                        <Field label="实例标识">
                          <input
                            value={r.instance_name}
                            onChange={(e) =>
                              editRow(i, { instance_name: e.target.value })
                            }
                          />
                        </Field>
                        <Field
                          label="NPU 设备编号"
                          hint={
                            r.role === "coordinator_controller"
                              ? "管理面不分配 NPU"
                              : "以逗号分隔，例如 0,1,2,3"
                          }
                        >
                          <input
                            disabled={r.role === "coordinator_controller"}
                            value={r.devices_text}
                            placeholder="0,1,2,3"
                            onChange={(e) =>
                              editRow(i, { devices_text: e.target.value })
                            }
                          />
                        </Field>
                      </>
                    )}
                  </div>
                  {runtime === "docker" &&
                    ["prefill", "decode", "union"].includes(r.role) && (
                      <details className="port-details">
                        <summary>高级端口配置</summary>
                        <div className="port-fields">
                          {(
                            [
                              ["node_manager", "NodeManager"],
                              ["rpc", "DP RPC"],
                              ["base", "引擎起始"],
                              ["kv", "KV 起始"],
                            ] as const
                          )
                            .filter(
                              ([key]) => key !== "kv" || supportsKv(r.role),
                            )
                            .map(([key, label]) => (
                              <Field key={key} label={label}>
                                <input
                                  type="number"
                                  min="1"
                                  max="65535"
                                  value={r.ports[key] ?? ""}
                                  onChange={(e) =>
                                    editRow(i, {
                                      ports: {
                                        ...r.ports,
                                        [key]: Number(e.target.value),
                                      },
                                    })
                                  }
                                />
                              </Field>
                            ))}
                        </div>
                      </details>
                    )}
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title={
                runtime === "docker"
                  ? "开始规划实例布局"
                  : "选择 Kubernetes 执行入口"
              }
              description={
                runtime === "docker"
                  ? "读取可信资源观测并预览布局建议，确认后应用；也可以手工添加实例。"
                  : "选择一个持有 kubeconfig 的 Agent，即可查看它连接的全部集群节点。"
              }
              icon={<Layers3 size={25} />}
              action={
                <Button onClick={generateLayout} disabled={!runtimeAccess.enabled || !nodesRead.loaded}>
                  {runtime === "docker" ? "生成布局" : "选择执行入口"}
                </Button>
              }
            />
          )}
          {runtime === "docker" && (
            <Button className="add-placement" onClick={addRow}>
              <Plus size={15} />
              添加角色实例
            </Button>
          )}
          {runtime === "kubernetes" &&
            runtimeAccess.enabled &&
            selectedAgent &&
            runtimeAvailable(selectedAgent, runtime) && (
              <>
                <ClusterNodesPanel
                  key={selectedAgent.id}
                  agent={selectedAgent}
                  scheduling={scheduling}
                  onSelectHost={selectHost}
                />
                {deployment.deploy_mode === "single_container" ? (
                  <InfoBox>
                    当前固定版 Motor 的 single-container
                    模式按硬件标签调度，不消费角色节点选择器；此处只展示集群节点。最终条件请检查生成清单。
                  </InfoBox>
                ) : currentConfig.motor_engine_union_config ? (
                  <InfoBox>
                    当前固定版 Motor 的 Union 混部引擎不消费 Prefill / Decode
                    节点选择器；上方快捷选择只作用于明确列出的角色，不改变混部引擎调度。
                  </InfoBox>
                ) : null}
              </>
            )}
          <div className="subtle-note">
            {runtime === "docker"
              ? "同一节点的设备和整段端口需互不冲突，实际可用性由预检返回。"
              : "节点选择写入 motor_deploy_config 中相应的 *_node_selector 字段。命名空间由控制台生成并管理；预检清单是最终调度配置的核对依据。"}
          </div>
        </div>
      )}
      {step === 3 && (
        <div className="wizard-content">
          {saved?.needs_rebuild && (
            <InfoBox>
              配置已更新。应用时将重建本服务已停止的受管容器，模型权重保留；普通重启不会应用此修订。
            </InfoBox>
          )}
          <div className="review-summary">
            <div className="review-symbol">
              <Rocket size={26} />
            </div>
            <div>
              <h3>{name || "未命名服务"}</h3>
              <p>
                {runtime === "docker" ? "Docker" : "Kubernetes"}
                <span>·</span>
                {modelSection?.engine_type ?? "vllm"}
                <span>·</span>
                {scenario === "hybrid" ? "PD 混部" : "PD 分离"}
                <span>·</span>
                {rows.length} 个角色布局
              </p>
            </div>
            <Badge status={plan?.valid ? "pass" : "draft"}>
              {plan?.valid ? "预检通过" : "等待预检"}
            </Badge>
          </div>
          <div className="review-kv">
            <span>
              服务镜像
              <b title={deployment.image_name}>
                {deployment.image_name || "尚未填写"}
              </b>
            </span>
            <span>
              模型路径
              <b>
                {modelSection?.engine_config?.model ??
                  modelSection?.engine_config?.["model-path"] ??
                  "尚未填写"}
              </b>
            </span>
            <span>
              权重目录<b>{deployment.weight_mount_path || "—"}</b>
            </span>
            <span>
              硬件类型<b>{deployment.hardware_type || "—"}</b>
            </span>
          </div>
          {plan ? (
            <>
              <div className="section-heading">
                <h3>
                  预检结果{" "}
                  <span className="count-pill">{plan.checks.length}</span>
                </h3>
              </div>
              <div className="check-list">
                {plan.checks.map((c, i) => (
                  <div key={i}>
                    <Badge status={c.status} />
                    <div>
                      <b>{c.name}</b>
                      <p>{c.detail}</p>
                    </div>
                  </div>
                ))}
              </div>
              {plan.warnings?.length > 0 && (
                <InfoBox>{plan.warnings.join("；")}</InfoBox>
              )}
              <ManifestReview
                plan={plan}
                nodes={nodes}
                extraCount={saved?.extra_manifests?.length ?? 0}
                extraManifests={saved?.extra_manifests ?? []}
                approved={extraApproved}
                onApprovalChange={setExtraApproved}
              />
              {plan.commands.length > 0 && (
                <details className="command-preview">
                  <summary>
                    查看执行计划 · {plan.commands.length} 条命令
                  </summary>
                  {plan.commands.map((c, i) => (
                    <div key={i}>
                      <label>
                        {nodes.find((n) => n.id === c.node_id)?.name ??
                          c.node_id}{" "}
                        / {roleName(c.role)}
                      </label>
                      <pre>{c.command}</pre>
                    </div>
                  ))}
                </details>
              )}
            </>
          ) : (
            <div className="preflight-placeholder">
              <ShieldCheck size={33} />
              <h3>部署前，让每一项准备就绪</h3>
              <p>保存配置并执行预检，检查结果与执行计划将在这里展示。</p>
              <Button onClick={runCheck} kind="primary" loading={busy} disabled={!runtimeAccess.enabled}>
                保存并执行预检
              </Button>
            </div>
          )}
        </div>
      )}
      {resourceProposal && (
        <PlacementPlanDialog
          configuration={resourceProposal}
          nodes={nodes}
          existingCount={rows.length}
          onClose={() => setResourceProposal(null)}
          onApply={(placements) => {
            setRows(placements.map(toRow));
            setResourceProposal(null);
            change();
          }}
        />
      )}
      {discardOpen && <Confirm
        title="放弃未保存的更改？"
        description="当前修改尚未保存。继续编辑可保留输入，放弃后将关闭此窗口。"
        confirmText="放弃更改"
        onClose={() => setDiscardOpen(false)}
        onConfirm={onClose}
      />}
    </Modal>
  );
}
