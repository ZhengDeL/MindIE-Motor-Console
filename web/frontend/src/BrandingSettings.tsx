import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Button, Confirm, ErrorBox, Field, InfoBox } from "./ui";
import { ImagePlus, RefreshCw, RotateCcw, Trash2 } from "./icons";
import { usePollingQuery } from "./usePollingQuery";
import { useUnsavedChanges } from "./unsavedChanges";
import { beginBrandingReload, captureBrandingReadGeneration, DEFAULT_BRANDING, parseBranding, publishBranding, useBranding } from "./brandingState";
import type { PlatformBranding } from "./brandingState";
import { BrandIdentity } from "./PlatformBrand";
import { prepareBrandIcon } from "./brandingIcon";
import "./branding.css";

type Draft = { name: string; subtitle: string; iconData: string | null | undefined };
const from = (value: PlatformBranding): Draft => ({ name: value.name, subtitle: value.subtitle, iconData: undefined });

function DefaultBrandingSettings() {
  const active = useBranding();
  const [remote, setRemote] = useState<PlatformBranding | null>(null), highest = useRef<PlatformBranding | null>(null);
  const [draft, setDraft] = useState<Draft>(() => from(active)), baseline = useRef<number | null>(null);
  const dirty = useRef(false), mounted = useRef(true), flight = useRef(false), uploadSequence = useRef(0);
  const [edited, setEdited] = useState(false), [busy, setBusy] = useState(false), [uploading, setUploading] = useState(false);
  const [error, setError] = useState(""), [readError, setReadError] = useState(""), [feedback, setFeedback] = useState("");
  const [supported, setSupported] = useState(true), [uncertain, setUncertain] = useState(false), [reloadOpen, setReloadOpen] = useState(false);
  const [reloading, setReloading] = useState(false), manualGeneration = useRef<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useUnsavedChanges(edited || busy || uploading);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; uploadSequence.current++; }; }, []);
  const accept = (raw: unknown, generation = captureBrandingReadGeneration()) => {
    let value = parseBranding(raw); if (!value) throw Error("品牌配置响应格式无效，请核对后端版本。");
    const published = publishBranding(value, generation);
    if (generation !== captureBrandingReadGeneration()) return;
    if (published && published.revision >= value.revision) value = published;
    if (highest.current && highest.current.revision > value.revision) value = highest.current;
    highest.current = value; setRemote(value);
    if (!dirty.current) { baseline.current = value.revision; setDraft(from(value)); setEdited(false); }
  };
  const query = usePollingQuery("platform-branding-settings", async (signal) => {
    const generation = captureBrandingReadGeneration(), resetBaseline = manualGeneration.current === generation;
    return { value: await api<PlatformBranding>("/branding", { signal }), generation, resetBaseline };
  }, {
    onSuccess: ({ value, generation, resetBaseline }) => {
      if (generation !== captureBrandingReadGeneration()) return;
      if (!parseBranding(value)) throw Error("品牌配置响应格式无效，请核对后端版本。");
      if (resetBaseline) highest.current = null;
      accept(value, generation); manualGeneration.current = null; setReloading(false);
      setSupported(true); setReadError(""); setUncertain(false);
    },
    onError: (reason) => {
      manualGeneration.current = null; setReloading(false);
      if ((reason as Error & { status?: number }).status === 404) { setSupported(false); setReadError(""); }
      else setReadError(reason.message);
    },
  });
  useEffect(() => {
    if (!reloading && remote && active.revision > remote.revision) accept(active);
  }, [active, remote?.revision, reloading]);
  const change = (values: Partial<Draft>) => { dirty.current = true; setEdited(true); setFeedback(""); setDraft((previous) => ({ ...previous, ...values })); };
  const resetDraft = (value: PlatformBranding) => {
    uploadSequence.current++; setUploading(false); dirty.current = false; baseline.current = value.revision;
    setDraft(from(value)); setEdited(false); setError(""); setFeedback(""); if (input.current) input.current.value = "";
  };
  const reload = () => {
    if (remote) resetDraft(highest.current ?? remote);
    manualGeneration.current = beginBrandingReload(); setReloading(true); setUncertain(true);
    setReadError(""); query.invalidate();
  };
  const upload = async (file: File | undefined) => {
    if (!file || busy) return;
    const sequence = ++uploadSequence.current; setUploading(true); setError(""); setFeedback("");
    try {
      const iconData = await prepareBrandIcon(file);
      if (mounted.current && sequence === uploadSequence.current) change({ iconData });
    } catch (reason) { if (mounted.current && sequence === uploadSequence.current) setError((reason as Error).message); }
    finally { if (mounted.current && sequence === uploadSequence.current) setUploading(false); if (input.current) input.current.value = ""; }
  };
  const save = async () => {
    if (!remote || baseline.current === null || !edited || busy || uploading || reloading || flight.current || uncertain) return;
    const name = draft.name.trim(), subtitle = draft.subtitle.trim();
    if (!name || Array.from(name).length > 40 || Array.from(subtitle).length > 80 || /[\p{Cc}\p{Cf}]/u.test(name + subtitle)) {
      setError("名称须为 1–40 个字符，副标题最多 80 个字符，均不能包含控制字符。"); return;
    }
    flight.current = true; query.cancel(); setBusy(true); setError(""); setFeedback("");
    try {
      const value = await api<PlatformBranding>("/branding", { method: "PUT", body: JSON.stringify({
        name, subtitle, expected_revision: baseline.current, ...(draft.iconData !== undefined ? { icon_data_url: draft.iconData } : {}),
      }) });
      if (!mounted.current) return;
      const parsed = parseBranding(value); if (!parsed) { setUncertain(true); throw Error("保存响应无法确认，请重新载入服务器配置，未自动重复提交。"); }
      dirty.current = false; accept(parsed); setFeedback("平台品牌已保存，登录页、侧栏和浏览器标题已更新；其他会话会在下一次配置刷新时同步。");
    } catch (reason) {
      if (!mounted.current) return;
      const failure = reason as Error & { status?: number; outcomeUnknown?: boolean };
      if (failure.outcomeUnknown) setUncertain(true);
      setError(failure.status === 409 ? "平台品牌已被其他操作更新，本地输入已保留。请重新载入最新配置后再编辑。" :
        failure.outcomeUnknown ? "保存结果尚未确认，本地输入已保留。请重新载入服务器配置，未自动重复提交。" : failure.message);
    } finally { flight.current = false; if (mounted.current) setBusy(false); }
  };
  const previewIcon = draft.iconData === undefined ? remote?.icon_url ?? active.icon_url : draft.iconData;
  return <UiRegion name="layout.panel" as="section" className="panel platform-branding-settings" aria-labelledby="platform-branding-title">
    <UiElement as="div" className="panel-head"><UiElement as="h2" id="platform-branding-title"><UiText>{"平台品牌"}</UiText></UiElement><Button actionId="action.BrandingSettings.001" disabled={busy || uploading || reloading} loading={reloading} onClick={() => edited || uncertain ? setReloadOpen(true) : reload()}><RefreshCw size={14} /><UiText>{"重新载入"}</UiText></Button></UiElement>
    <UiElement as="div" className="settings-content">
      <UiElement as="p" className="settings-context-note"><UiText>{"由管理员统一管理，对所有用户与登录页面生效。编辑时只更新下方预览，保存后发布。"}</UiText></UiElement>
      {readError && <ErrorBox message={readError} />}{error && <ErrorBox message={error} />}{feedback && <InfoBox>{feedback}</InfoBox>}
      {!supported && <InfoBox><UiText>{"当前后端尚未提供品牌配置接口；升级后可在此统一管理名称与图标。"}</UiText></InfoBox>}
      <UiElement as="fieldset" className="branding-fields" disabled={!remote || !supported || busy || reloading}>
        <Field label="平台名称" required hint="1–40 个字符，用于登录页、侧栏和浏览器标题。"><UiElement as="input" value={draft.name} maxLength={80} onChange={(event) => change({ name: Array.from(event.target.value).slice(0, 40).join("") })} /></Field>
        <Field label="平台副标题" hint="最多 80 个字符；留空时隐藏副标题。"><UiElement as="input" value={draft.subtitle} maxLength={160} onChange={(event) => change({ subtitle: Array.from(event.target.value).slice(0, 80).join("") })} /></Field>
        <UiElement as="div" className="field"><UiElement as="span" className="field-label"><UiText>{"平台图标"}</UiText></UiElement>
          <UiElement as="input" ref={input} type="file" accept="image/png,image/jpeg,image/webp" aria-label="平台图标文件" hidden onChange={(event) => void upload(event.target.files?.[0])} />
          <UiElement as="div" className="branding-icon-actions"><Button actionId="action.BrandingSettings.002" disabled={uploading} loading={uploading} onClick={() => input.current?.click()}><ImagePlus size={15} /><UiText>{"上传图标"}</UiText></Button>
            <Button actionId="action.BrandingSettings.003" disabled={!previewIcon || uploading} onClick={() => { uploadSequence.current++; change({ iconData: null }); }}><Trash2 size={14} /><UiText>{"移除图标"}</UiText></Button></UiElement>
          <UiElement as="small"><UiText>{"支持 PNG、JPEG、静态 WebP，最大 2 MiB、4096 × 4096 像素。图像会等比处理为 256 × 256 的 PNG，保留透明背景。"}</UiText></UiElement>
        </UiElement>
      </UiElement>
      <UiElement as="div" className="branding-preview" aria-label="平台品牌预览"><UiElement as="span" className="small-caption"><UiText>{"显示预览"}</UiText></UiElement>
        <BrandIdentity name={draft.name.trim() || "平台名称"} subtitle={draft.subtitle.trim()} iconUrl={previewIcon} variant="preview" /></UiElement>
      <UiElement as="div" className="settings-actions branding-save-actions">
        <Button actionId="action.BrandingSettings.004" kind="primary" loading={busy} disabled={!remote || !supported || !edited || uploading || reloading || uncertain} onClick={() => void save()}><UiText>{"保存平台品牌"}</UiText></Button>
        <Button actionId="action.BrandingSettings.005" disabled={!remote || !edited || busy || uploading || reloading} onClick={() => remote && resetDraft(highest.current ?? remote)}><UiText>{"取消更改"}</UiText></Button>
        <Button actionId="action.BrandingSettings.006" disabled={!remote || busy || uploading || reloading} onClick={() => { uploadSequence.current++; change({ name: DEFAULT_BRANDING.name, subtitle: DEFAULT_BRANDING.subtitle, iconData: null }); }}><RotateCcw size={14} /><UiText>{"恢复默认品牌"}</UiText></Button>
      </UiElement>
      {edited && <UiElement as="p" className="small-caption"><UiText>{"有未保存更改；恢复默认品牌也需要点击“保存平台品牌”才会生效。"}</UiText></UiElement>}
      {uncertain && <UiElement as="p" className="small-caption" role="status"><UiText>{"请先重新载入确认服务器结果，再继续保存。"}</UiText></UiElement>}
    </UiElement>
    {reloadOpen && <Confirm title="重新载入平台品牌？" description="将读取服务器上的最新配置，并放弃本地未保存的编辑。" confirmText="重新载入" onClose={() => setReloadOpen(false)} onConfirm={() => { setReloadOpen(false); reload(); }} />}
  </UiRegion>;
}

const BrandingSettings = withUiSlot("component.BrandingSettings", DefaultBrandingSettings);
export default BrandingSettings;

// Reference package implementation exports.
export { DefaultBrandingSettings };
