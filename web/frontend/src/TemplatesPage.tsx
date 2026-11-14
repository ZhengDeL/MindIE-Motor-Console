import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import { TEMPLATE_FORMAT } from "./brand";
import { readCount, readNotice } from "./readState";
import { assertProductImport, isNativeMotorConfig } from "./productImports";
import { useUnsavedChanges } from "./unsavedChanges";
import { useEffect, useRef, useState } from "react";
import {
  Copy as CopyIcon,
  Download,
  FileJson,
  Plus,
  RefreshCw,
  Upload,
} from "./icons";
import {
  api,
  download,
  parseStrictJson as parseJson,
  readJsonFile,
  validateJsonNumbers,
  when,
} from "./api";
import type { Json, Service, Template, TemplateInput } from "./api";
import { recordTime } from "./operationRecords";
import {
  Button,
  Confirm,
  Copy,
  Empty,
  ErrorBox,
  Field,
  InfoBox,
  JsonEditor,
  Modal,
  SearchInput,
  Select,
  Spinner,
} from "./ui";

export const templateSource = (template: Template) =>
  template.source === "custom" ? "custom" : "official";
const runtimeLabel = (runtime: string | null | undefined) =>
  runtime === "docker"
    ? "Docker"
    : runtime === "kubernetes"
      ? "Kubernetes"
      : "创建时选择";
const templatePath = (id: string) => `/templates/${encodeURIComponent(id)}`;
const fileName = (name: string) =>
  `${
    name
      .replace(/[^\p{L}\p{N}_.-]+/gu, "-")
      .replace(/^\.+/, "")
      .slice(0, 80) || "model-template"
  }.json`;

export function templateInput(template: Partial<TemplateInput>): TemplateInput {
  validateJsonNumbers(template);
  return {
    name: template.name ?? "",
    description: template.description ?? "",
    tags: [...(template.tags ?? [])],
    runtime: template.runtime ?? null,
    motor_profile: template.motor_profile ?? "",
    user_config: template.user_config ?? {
      version: "v2.0",
      motor_deploy_config: {},
    },
    env: template.env ?? {},
    extra_manifests: template.extra_manifests ?? [],
  };
}

export function serviceTemplateInput(service: Service): TemplateInput {
  return templateInput({
    name: `${service.name} 模板`,
    runtime: service.runtime,
    motor_profile: service.motor_profile,
    user_config: service.user_config,
    env: service.env,
    extra_manifests: service.extra_manifests,
  });
}

function object(value: unknown, label: string): Json {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Error(`${label}必须是 JSON 对象`);
  return value as Json;
}

function manifestArray(value: unknown): Json[] {
  if (!Array.isArray(value)) throw Error("高级清单必须是 JSON 对象数组");
  return value.map((item) => object(item, "高级清单中的资源"));
}

function importedTemplate(value: unknown): TemplateInput {
  assertProductImport(value);
  const data = object(value, "模板文件");
  if (isNativeMotorConfig(data))
    throw Error("这是原生 Motor 配置，请在创建服务的配置步骤导入，再另存为模板。");
  if (!data.user_config)
    throw Error("模板文件缺少 user_config；请导入模板或完整服务配置 JSON");
  if (data.config_redacted)
    throw Error("文件中的配置已脱敏，无法导入为完整模板");
  if (data.runtime != null && data.runtime !== "docker" && data.runtime !== "kubernetes")
    throw Error("运行环境必须为 Docker、Kubernetes 或 null");
  if (
    data.tags != null &&
    (!Array.isArray(data.tags) ||
      data.tags.some((tag: unknown) => typeof tag !== "string"))
  )
    throw Error("模板标签必须是字符串数组");
  return templateInput({
    name: typeof data.name === "string" ? data.name : "",
    description: typeof data.description === "string" ? data.description : "",
    tags: Array.isArray(data.tags) ? data.tags : [],
    runtime: data.runtime === "docker" || data.runtime === "kubernetes" ? data.runtime : null,
    motor_profile:
      typeof data.motor_profile === "string" ? data.motor_profile : "",
    user_config: object(data.user_config, "服务配置"),
    env: object(data.env ?? {}, "环境变量"),
    extra_manifests: manifestArray(data.extra_manifests ?? []),
  });
}

function DefaultTemplateEditor({
  template,
  initial,
  title,
  onClose,
  onSaved,
}: {
  template?: Template;
  initial?: Partial<TemplateInput>;
  title?: string;
  onClose: () => void;
  onSaved: (template: Template) => void;
}) {
  const [original, setOriginal] = useState(template);
  const [values, setValues] = useState(() =>
    templateInput(template ?? initial ?? {}),
  );
  const [userText, setUserText] = useState(() =>
    JSON.stringify(values.user_config, null, 2),
  );
  const [envText, setEnvText] = useState(() =>
    JSON.stringify(values.env, null, 2),
  );
  const [extraText, setExtraText] = useState(() =>
    JSON.stringify(values.extra_manifests, null, 2),
  );
  const [tags, setTags] = useState(() => values.tags.join(", "));
  const [tab, setTab] = useState("user");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const signature = JSON.stringify([values, userText, envText, extraText, tags]);
  const baseline = useRef(signature);
  const dirty = signature !== baseline.current;
  useUnsavedChanges(dirty);
  const [discardOpen, setDiscardOpen] = useState(false);
  const close = () => { if (!busy) { if (dirty) setDiscardOpen(true); else onClose(); } };
  const file = useRef<HTMLInputElement>(null);
  const replace = (data: TemplateInput, acceptAsBaseline = false) => {
    if (acceptAsBaseline) baseline.current = JSON.stringify([data, JSON.stringify(data.user_config, null, 2), JSON.stringify(data.env, null, 2), JSON.stringify(data.extra_manifests, null, 2), data.tags.join(", ")]);
    setValues(data);
    setUserText(JSON.stringify(data.user_config, null, 2));
    setEnvText(JSON.stringify(data.env, null, 2));
    setExtraText(JSON.stringify(data.extra_manifests, null, 2));
    setTags(data.tags.join(", "));
    setError("");
    setConflict(false);
  };
  const parsed = (): TemplateInput => {
    if (!values.name.trim()) throw Error("请填写模板名称");
    const extras = manifestArray(parseJson(extraText));
    const normalizedTags = [
      ...new Set(
        tags
          .split(/[,，\n]/)
          .map((tag) => tag.trim())
          .filter(Boolean),
      ),
    ];
    if (
      normalizedTags.length > 16 ||
      normalizedTags.some((tag) => tag.length > 40)
    )
      throw Error("最多填写 16 个标签，每个标签不超过 40 个字符");
    const user = object(parseJson(userText), "服务配置");
    object(user.motor_deploy_config, "motor_deploy_config");
    return {
      ...values,
      name: values.name.trim(),
      tags: normalizedTags,
      user_config: user,
      env: object(parseJson(envText), "环境变量"),
      extra_manifests: extras,
    };
  };
  const save = async () => {
    setBusy(true);
    setError("");
    setConflict(false);
    try {
      const body = parsed();
      const saved = await api<Template>(
        original ? templatePath(original.id) : "/templates",
        {
          method: original ? "PUT" : "POST",
          body: JSON.stringify(
            original ? { ...body, revision: original.revision } : body,
          ),
        },
      );
      onSaved(saved);
    } catch (e) {
      setError((e as Error).message);
      setConflict(
        Boolean(original && (e as Error & { status?: number }).status === 409),
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={title ?? (original ? "编辑模板" : "新建模板")}
      subtitle="保存配置快照；创建服务时再选择执行节点与设备"
      wide
      onClose={close}
      closeDisabled={busy}
      footer={
        <>
          <Button actionId="action.TemplatesPage.001" disabled={busy} onClick={close}>
            <UiText>{"取消"}</UiText>
          </Button>
          <Button actionId="action.TemplatesPage.002" kind="primary" loading={busy} onClick={() => void save()}>
            <UiText>{"保存模板"}</UiText>
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {conflict && original && (
        <UiElement as="div" className="template-conflict">
          <UiElement as="span"><UiText>{"当前内容已保留。重新加载会替换本次未保存的编辑。"}</UiText></UiElement>
          <Button actionId="action.TemplatesPage.003"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const latest = await api<Template>(templatePath(original.id));
                const content = templateInput(latest);
                setOriginal(latest);
                replace(content, true);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <UiText>{"重新加载模板"}</UiText>
          </Button>
        </UiElement>
      )}
      <UiElement as="div" className="form-two">
        <Field label="模板名称" required>
          <UiElement as="input"
            aria-label="模板名称"
            disabled={busy}
            maxLength={100}
            value={values.name}
            onChange={(event) =>
              setValues({ ...values, name: event.target.value })
            }
            placeholder="用于识别模型与部署方式"
          />
        </Field>
        <Field label="适用运行环境">
          <Select
            ariaLabel="模板运行环境"
            disabled={busy}
            value={values.runtime ?? ""}
            onChange={(runtime) =>
              setValues({
                ...values,
                runtime: runtime ? (runtime as "docker" | "kubernetes") : null,
              })
            }
          >
            <UiElement as="option" value=""><UiText>{"创建时选择"}</UiText></UiElement>
            <UiElement as="option" value="docker"><UiText>{"Docker"}</UiText></UiElement>
            <UiElement as="option" value="kubernetes"><UiText>{"Kubernetes"}</UiText></UiElement>
          </Select>
        </Field>
        <Field label="模板说明">
          <UiElement as="input"
            aria-label="模板说明"
            disabled={busy}
            maxLength={1000}
            value={values.description}
            onChange={(event) =>
              setValues({ ...values, description: event.target.value })
            }
            placeholder="可选，说明用途与使用条件"
          />
        </Field>
        <Field label="标签" hint="多个标签用逗号分隔">
          <UiElement as="input"
            aria-label="模板标签"
            disabled={busy}
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder="例如 PD分离, A3"
          />
        </Field>
        <Field
          label="Motor 兼容配置"
          hint={
            original
              ? "现有模板保留原绑定；变更兼容配置请另存新模板"
              : "保留原绑定；留空时由服务端匹配当前兼容配置"
          }
        >
          <UiElement as="input"
            aria-label="模板 Motor 兼容配置"
            disabled={busy}
            readOnly={Boolean(original)}
            value={values.motor_profile}
            onChange={(event) =>
              setValues({ ...values, motor_profile: event.target.value })
            }
          />
        </Field>
        <UiElement as="div" className="template-snapshot-note">
          <UiText>{"仅保存原始配置、环境变量与高级清单。模板不包含节点布局、部署审批、Agent 凭据或运行状态。"}</UiText>
        </UiElement>
      </UiElement>
      <UiElement as="div" className="editor-toolbar template-editor-toolbar">
        <UiElement as="div" className="tab-list compact">
          {[
            ["user", "服务配置"],
            ["env", "环境变量"],
            ["extra", "高级清单"],
          ].map(([id, label]) => (
            <UiButton actionId="action.TemplatesPage.004"
              key={id}
              disabled={busy}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
            >
              {label}
            </UiButton>
          ))}
        </UiElement>
        <UiElement as="div" className="template-editor-actions">
          <Button actionId="action.TemplatesPage.005" disabled={busy} onClick={() => file.current?.click()}>
            <Upload size={14} />
            <UiText>{"导入 JSON"}</UiText>
          </Button>
          <Button actionId="action.TemplatesPage.006"
            disabled={busy}
            onClick={() => {
              try {
                download(
                  { format: TEMPLATE_FORMAT, ...parsed() },
                  fileName(values.name),
                );
                setError("");
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            <Download size={14} />
            <UiText>{"导出 JSON"}</UiText>
          </Button>
        </UiElement>
      </UiElement>
      <UiElement as="input"
        hidden
        ref={file}
        type="file"
        accept=".json,application/json"
        onChange={async (event) => {
          const selected = event.target.files?.[0];
          event.target.value = "";
          if (selected) {
            try {
              replace(importedTemplate(await readJsonFile(selected)));
            } catch (e) {
              setError(`导入失败：${(e as Error).message}`);
            }
          }
        }}
      />
      {tab === "extra" && (
        <InfoBox>
          <UiText>{"高级清单会原样保存。只有 Kubernetes 服务可使用这些清单，部署前需重新预检与审查；模板保存不授予部署权限。"}</UiText>
        </InfoBox>
      )}
      <JsonEditor
        disabled={busy}
        label={
          tab === "user"
            ? "模板 user_config.json"
            : tab === "env"
              ? "模板 env.json"
              : "模板 extra_manifests.json"
        }
        value={tab === "user" ? userText : tab === "env" ? envText : extraText}
        onChange={
          tab === "user"
            ? setUserText
            : tab === "env"
              ? setEnvText
              : setExtraText
        }
        rows={16}
      />
      {discardOpen && <Confirm title="放弃未保存的模板？" description="本次编辑还未保存。取消可继续编辑，放弃后将关闭窗口。" confirmText="放弃更改" onClose={() => setDiscardOpen(false)} onConfirm={onClose} />}
    </Modal>
  );
}

function DefaultTemplatesPage({
  canOperate,
  onUse,
  notify,
}: {
  canOperate: boolean;
  onUse: (template: Template) => void;
  notify: (message: string) => void;
}) {
  const [items, setItems] = useState<Template[]>([]);
  const [hasSuccess, setHasSuccess] = useState(false);
  const [readError, setReadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const [runtime, setRuntime] = useState("all");
  const [editor, setEditor] = useState<{
    template?: Template;
    initial?: Partial<TemplateInput>;
    title?: string;
  } | null>(null);
  const [view, setView] = useState<Template | null>(null);
  const [removing, setRemoving] = useState<Template | null>(null);
  const [removeError, setRemoveError] = useState("");
  const loadGeneration = useRef(0);
  const loadRequest = useRef<AbortController | null>(null);
  const readGeneration = useRef(0);
  const readRequest = useRef<AbortController | null>(null);
  const importFile = useRef<HTMLInputElement>(null);
  const load = async () => {
    loadRequest.current?.abort();
    const controller = new AbortController();
    loadRequest.current = controller;
    const generation = ++loadGeneration.current;
    setLoading(true);
    setError("");
    setReadError("");
    try {
      const result = await api<{ items: Template[] }>("/templates", { signal: controller.signal });
      if (controller.signal.aborted || generation !== loadGeneration.current) return;
      setItems(result.items);
      setHasSuccess(true);
    } catch (e) {
      if (controller.signal.aborted || generation !== loadGeneration.current) return;
      setError((e as Error).message);
      setReadError((e as Error).message);
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  };
  useEffect(() => {
    void load();
    return () => {
      loadGeneration.current++;
      readGeneration.current++;
      loadRequest.current?.abort();
      readRequest.current?.abort();
    };
  }, []);
  const read = async (
    template: Template,
    action: "view" | "copy" | "edit" | "use" | "export",
  ) => {
    readRequest.current?.abort();
    const controller = new AbortController();
    readRequest.current = controller;
    const generation = ++readGeneration.current;
    setBusy(template.id);
    setError("");
    try {
      const latest = await api<Template>(templatePath(template.id), { signal: controller.signal });
      if (controller.signal.aborted || generation !== readGeneration.current) return;
      validateJsonNumbers(latest);
      if (action !== "view" && (!canOperate || latest.config_redacted))
        throw Error("当前账号无权读取完整模板配置");
      if (action === "view") setView(latest);
      if (action === "copy") {
        setView(null);
        setEditor({
          title: "复制为自定义模板",
          initial: { ...templateInput(latest), name: `${latest.name} 副本` },
        });
      }
      if (action === "edit") {
        setView(null);
        setEditor({ template: latest });
      }
      if (action === "use") {
        setView(null);
        onUse(latest);
      }
      if (action === "export")
        download(
          { format: TEMPLATE_FORMAT, ...templateInput(latest) },
          fileName(latest.name),
        );
    } catch (e) {
      if (!controller.signal.aborted && generation === readGeneration.current)
        setError((e as Error).message);
    } finally {
      if (generation === readGeneration.current) setBusy("");
    }
  };
  const displayed = items.filter(
    (template) =>
      (source === "all" || templateSource(template) === source) &&
      (runtime === "all" ||
        (runtime === "unrestricted"
          ? !template.runtime
          : template.runtime === runtime)) &&
      `${template.name} ${template.description ?? ""} ${(template.tags ?? []).join(" ")} ${template.engine} ${template.hardware}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <>
      {error && !view && <ErrorBox message={error} />}
      <UiRegion name="layout.panel" as="section" className="panel">
        <UiElement as="div" className="table-toolbar template-library-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="搜索模板名称、说明或标签"
          />
          <Select ariaLabel="筛选模板来源" value={source} onChange={setSource}>
            <UiElement as="option" value="all"><UiText>{"全部来源"}</UiText></UiElement>
            <UiElement as="option" value="official"><UiText>{"官方模板"}</UiText></UiElement>
            <UiElement as="option" value="custom"><UiText>{"自定义模板"}</UiText></UiElement>
          </Select>
          <Select
            ariaLabel="筛选模板运行环境"
            value={runtime}
            onChange={setRuntime}
          >
            <UiElement as="option" value="all"><UiText>{"全部运行环境"}</UiText></UiElement>
            <UiElement as="option" value="docker"><UiText>{"Docker"}</UiText></UiElement>
            <UiElement as="option" value="kubernetes"><UiText>{"Kubernetes"}</UiText></UiElement>
            <UiElement as="option" value="unrestricted"><UiText>{"创建时选择"}</UiText></UiElement>
          </Select>
          <UiElement as="span" className="toolbar-right-note" role="status">{readCount({ loaded: hasSuccess, refreshing: loading, error: readError }, displayed.length, "个模板")}</UiElement>
          <Button actionId="action.TemplatesPage.007" loading={loading} onClick={() => void load()}>
            <RefreshCw size={14} />
            <UiText>{"刷新"}</UiText>
          </Button>
          <Button actionId="action.TemplatesPage.008"
            disabled={!canOperate}
            onClick={() => importFile.current?.click()}
          >
            <Upload size={14} />
            <UiText>{"导入模板"}</UiText>
          </Button>
          <Button actionId="action.TemplatesPage.009"
            kind="primary"
            disabled={!canOperate}
            onClick={() => setEditor({})}
          >
            <Plus size={14} />
            <UiText>{"新建模板"}</UiText>
          </Button>
          <UiElement as="input"
            ref={importFile}
            hidden
            type="file"
            accept=".json,application/json"
            onChange={async (event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) {
                try {
                  setEditor({
                    initial: importedTemplate(await readJsonFile(file)),
                    title: "导入模板",
                  });
                  setError("");
                } catch (e) {
                  setError(`导入失败：${(e as Error).message}`);
                }
              }
            }}
          />
        </UiElement>
        {loading && !hasSuccess ? (
          <Spinner label="正在读取模板" />
        ) : displayed.length ? (
          <UiElement as="div" className="table-wrap">
            <UiElement as="table" className="templates-table">
              <UiElement as="thead">
                <UiElement as="tr">
                  <UiElement as="th"><UiText>{"模板名称 / 说明"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"来源"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"运行环境"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"引擎 / 硬件"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"最近更新"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"操作"}</UiText></UiElement>
                </UiElement>
              </UiElement>
              <UiElement as="tbody">
                {displayed.map((template) => (
                  <UiElement as="tr" key={template.id}>
                    <UiElement as="td">
                      <UiButton actionId="action.TemplatesPage.010"
                        className="text-link template-name"
                        onClick={() => void read(template, "view")}
                      >
                        {template.name}
                      </UiButton>
                      {template.description && (
                        <UiElement as="span" className="cell-secondary template-description">
                          {template.description}
                        </UiElement>
                      )}
                      <UiElement as="div" className="template-tags">
                        {(template.tags ?? []).map((tag) => (
                          <UiElement as="span" key={tag} className="tag small">
                            {tag}
                          </UiElement>
                        ))}
                      </UiElement>
                    </UiElement>
                    <UiElement as="td">
                      <UiElement as="span"
                        className={`tag small ${templateSource(template) === "custom" ? "template-custom-tag" : ""}`}
                      >
                        {templateSource(template) === "custom"
                          ? "自定义"
                          : "官方只读"}
                      </UiElement>
                    </UiElement>
                    <UiElement as="td">{runtimeLabel(template.runtime)}</UiElement>
                    <UiElement as="td">
                      {template.engine}
                      {template.hardware && <UiElement as="span" className="cell-secondary">
                        {template.hardware}
                      </UiElement>}
                    </UiElement>
                    <UiElement as="td" className="tabular nowrap">
                      {recordTime(template.updated_at) &&
                        when(template.updated_at!)}
                      <UiElement as="span" className="cell-secondary">
                        {templateSource(template) === "custom"
                          ? `修订 ${template.revision}`
                          : "固定上游版本"}
                      </UiElement>
                    </UiElement>
                    <UiElement as="td">
                      <UiElement as="div" className="row-actions template-row-actions">
                        <UiButton actionId="action.TemplatesPage.011"
                          disabled={
                            !canOperate ||
                            Boolean(busy) ||
                            template.config_redacted
                          }
                          onClick={() => void read(template, "use")}
                        >
                          <UiText>{"使用模板"}</UiText>
                        </UiButton>
                        <UiButton actionId="action.TemplatesPage.012"
                          disabled={
                            !canOperate ||
                            Boolean(busy) ||
                            template.config_redacted
                          }
                          onClick={() => void read(template, "copy")}
                        >
                          <UiText>{"复制"}</UiText>
                        </UiButton>
                        {templateSource(template) === "custom" && (
                          <>
                            <UiButton actionId="action.TemplatesPage.013"
                              disabled={!canOperate || Boolean(busy)}
                              onClick={() => void read(template, "edit")}
                            >
                              <UiText>{"编辑"}</UiText>
                            </UiButton>
                            <UiButton actionId="action.TemplatesPage.014"
                              className="danger-text"
                              disabled={!canOperate || Boolean(busy)}
                              onClick={() => {
                                setRemoving(template);
                                setRemoveError("");
                              }}
                            >
                              <UiText>{"删除"}</UiText>
                            </UiButton>
                          </>
                        )}
                      </UiElement>
                    </UiElement>
                  </UiElement>
                ))}
              </UiElement>
            </UiElement>
          </UiElement>
        ) : (
          <Empty
            title={!hasSuccess ? readNotice({ loaded: false, refreshing: loading, error: readError }) : items.length ? "没有匹配的模板" : "暂无模板"}
            description={
              !hasSuccess ? "尚未取得模板列表，请检查错误提示后重试。" : items.length
                ? "调整搜索或筛选条件后查看。"
                : "创建自定义模板，或导入已有原生配置快照。"
            }
            icon={<FileJson size={25} />}
          />
        )}
      </UiRegion>
      {!canOperate && (
        <UiElement as="div" className="subtle-note">
          <UiText>{"当前账号仅可查看模板元数据，完整配置与模板变更需要运维操作员或管理员权限。"}</UiText>
        </UiElement>
      )}
      {editor && canOperate && (
        <TemplateEditor
          {...editor}
          onClose={() => setEditor(null)}
          onSaved={(saved) => {
            setEditor(null);
            notify(`模板“${saved.name}”已保存`);
            void load();
          }}
        />
      )}
      {view && (
        <Modal
          title={view.name}
          subtitle={
            templateSource(view) === "official"
              ? "官方模板 · 固定上游版本，只读"
              : `自定义模板 · 修订 ${view.revision}`
          }
          wide
          onClose={() => setView(null)}
          footer={
            <>
              <Button actionId="action.TemplatesPage.015" onClick={() => setView(null)}><UiText>{"关闭"}</UiText></Button>
              <Button actionId="action.TemplatesPage.016"
                disabled={!canOperate || view.config_redacted || Boolean(busy)}
                onClick={() => void read(view, "export")}
              >
                <Download size={14} />
                <UiText>{"导出 JSON"}</UiText>
              </Button>
              <Button actionId="action.TemplatesPage.017"
                disabled={!canOperate || view.config_redacted || Boolean(busy)}
                onClick={() => void read(view, "copy")}
              >
                <CopyIcon size={14} />
                <UiText>{"复制为自定义模板"}</UiText>
              </Button>
              <Button actionId="action.TemplatesPage.018"
                kind="primary"
                disabled={!canOperate || view.config_redacted || Boolean(busy)}
                onClick={() => void read(view, "use")}
              >
                <UiText>{"使用模板"}</UiText>
              </Button>
            </>
          }
        >
          {error && <ErrorBox message={error} />}
          <UiElement as="div" className="template-view-meta">
            <UiElement as="span"><UiText>{"运行环境："}</UiText>{runtimeLabel(view.runtime)}</UiElement>
            {view.engine && <UiElement as="span"><UiText>{"推理引擎："}</UiText>{view.engine}</UiElement>}
            {view.hardware && <UiElement as="span"><UiText>{"硬件："}</UiText>{view.hardware}</UiElement>}
          </UiElement>
          {view.description && (
            <UiElement as="p" className="template-view-description">{view.description}</UiElement>
          )}
          {canOperate && !view.config_redacted ? (
            <>
              <UiElement as="div" className="editor-toolbar">
                <UiElement as="b"><UiText>{"完整模板 JSON"}</UiText></UiElement>
                <Copy
                  label="复制完整模板 JSON"
                  value={JSON.stringify(templateInput(view), null, 2)}
                />
              </UiElement>
              <UiElement as="pre" className="json-view template-json-view">
                {JSON.stringify(templateInput(view), null, 2)}
              </UiElement>
            </>
          ) : (
            <InfoBox><UiText>{"当前账号没有读取完整模板配置的权限。"}</UiText></InfoBox>
          )}
        </Modal>
      )}
      {removing && (
        <Confirm
          title={`删除模板 ${removing.name}？`}
          description={
            <>
              <UiElement as="p">
                <UiText>{"删除后将无法再从模板库选择此模板。已经创建的服务保留各自配置，不受影响。"}</UiText>
              </UiElement>
              <UiElement as="div" className="confirm-resource">
                <FileJson size={17} />
                <UiElement as="b">{removing.name}</UiElement>
                <UiElement as="span"><UiText>{"修订"}</UiText> {removing.revision}</UiElement>
              </UiElement>
            </>
          }
          confirmText="删除模板"
          busy={busy === removing.id}
          error={removeError}
          onClose={() => {
            if (!busy) setRemoving(null);
          }}
          onConfirm={async () => {
            setBusy(removing.id);
            setRemoveError("");
            try {
              await api(
                `${templatePath(removing.id)}?revision=${removing.revision}`,
                { method: "DELETE" },
              );
              setRemoving(null);
              notify("模板已删除");
              await load();
            } catch (e) {
              setRemoveError((e as Error).message);
            } finally {
              setBusy("");
            }
          }}
        />
      )}
    </>
  );
}

export const TemplateEditor = withUiSlot("component.TemplateEditor", DefaultTemplateEditor);
const TemplatesPage = withUiSlot("component.TemplatesPage", DefaultTemplatesPage);
export default TemplatesPage;

// Reference package implementation exports.
export { DefaultTemplateEditor, DefaultTemplatesPage };
