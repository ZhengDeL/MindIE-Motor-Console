import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import { useRef } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import { Check, Monitor, Moon, RefreshCw, Sun } from "./icons";
import { SIDEBAR_WIDTH, THEMES, useAppearance } from "./appearance";
import type { ColorMode, Density, MotionPreference } from "./appearance";
import "./appearance-settings.css";

type Choice<T extends string> = { id: T; name: string; description?: string; decoration?: ReactNode };

function Choices<T extends string>({ label, options, value, onChange, className = "" }: {
  label: string; options: readonly Choice<T>[]; value: T; onChange: (value: T) => void; className?: string;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const selectedIndex = options.findIndex((option) => option.id === value);
  const focusIndex = selectedIndex < 0 ? 0 : selectedIndex;
  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const directions: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
      : event.key in directions ? (index + directions[event.key] + options.length) % options.length : -1;
    if (next < 0) return;
    event.preventDefault(); onChange(options[next].id); buttons.current[next]?.focus();
  };
  return <UiElement as="div" className={`appearance-selection ${className}`} role="radiogroup" aria-label={label}>
    {options.map((option, index) => <UiButton actionId="action.AppearanceSettings.001" key={option.id} type="button" role="radio"
      aria-label={option.name} aria-checked={option.id === value} tabIndex={index === focusIndex ? 0 : -1}
      className={`appearance-option ${option.id === value ? "is-selected" : ""}`}
      ref={(element) => { buttons.current[index] = element; }}
      onClick={() => onChange(option.id)} onKeyDown={(event) => move(event, index)}>
      {option.decoration}<UiElement as="span" className="appearance-option-label">{option.name}</UiElement>
      {option.description && <UiElement as="span" className="appearance-option-description">{option.description}</UiElement>}
      <UiElement as="span" className="appearance-check" aria-hidden="true"><Check size={13} /></UiElement>
    </UiButton>)}
  </UiElement>;
}

const modeIcons = { light: Sun, dark: Moon, system: Monitor };
const modeDescriptions = { light: "清晰明亮的工作区", dark: "适合低亮环境", system: "随设备外观自动切换" };
const motionDescriptions: Record<MotionPreference, string> = {
  enhanced: "保留完整的页面和面板过渡。", standard: "使用简短的页面过渡和操作反馈。", reduced: "停用非必要过渡，直接显示状态变化。",
};

function DefaultAppearanceSettings() {
  const appearance = useAppearance();
  const modes = (["light", "dark", "system"] satisfies ColorMode[]).map((id) => {
    const Icon = modeIcons[id];
    return { id, name: id === "light" ? "浅色" : id === "dark" ? "深色" : "跟随系统",
      description: modeDescriptions[id], decoration: <Icon size={16} className="appearance-mode-icon" /> };
  });
  return <UiRegion name="layout.panel" as="section" className="panel appearance-panel appearance-studio" aria-labelledby="appearance-title">
    <UiElement as="div" className="appearance-studio-heading">
      <UiElement as="div"><UiElement as="h2" id="appearance-title"><UiText>{"界面外观"}</UiText></UiElement><UiElement as="p"><UiText>{"选择显示模式、主题配色和导航布局。"}</UiText></UiElement></UiElement>
      <UiButton actionId="action.AppearanceSettings.002" type="button" className="btn btn-default appearance-reset" onClick={() => appearance.setAppearance({ theme: "blue", mode: "system", density: "comfortable", motion: "standard", sidebarWidth: SIDEBAR_WIDTH.default })}><RefreshCw size={14} /><UiText>{"恢复默认"}</UiText></UiButton>
    </UiElement>
    <UiElement as="div" className="appearance-studio-body">
      <UiElement as="div" className="appearance-section appearance-mode-section">
        <UiElement as="div" className="appearance-section-label"><UiElement as="h3"><UiText>{"显示模式"}</UiText></UiElement><UiElement as="span"><UiText>{"选择后立即生效"}</UiText></UiElement></UiElement>
        <Choices label="显示模式" options={modes} value={appearance.mode} onChange={(mode) => appearance.setAppearance({ mode })} className="appearance-mode-options" />
      </UiElement>
      <UiElement as="div" className="appearance-details-grid">
        <UiElement as="div" className="appearance-section appearance-accent-section">
          <UiElement as="div" className="appearance-section-label"><UiElement as="h3"><UiText>{"主题配色"}</UiText></UiElement><UiElement as="span"><UiText>{"用于链接、焦点与选中态"}</UiText></UiElement></UiElement>
          <Choices label="主题配色" options={THEMES.map((theme) => ({ ...theme, decoration: <UiElement as="span" className="appearance-accent-swatch" style={{ "--swatch-color": theme.color } as CSSProperties} aria-hidden="true"><Check size={16} /></UiElement> }))}
            value={appearance.theme} onChange={(theme) => appearance.setAppearance({ theme })} className="appearance-accent-options" />
        </UiElement>
        <UiElement as="div" className="appearance-section appearance-density-section">
          <UiElement as="div" className="appearance-section-label"><UiElement as="h3"><UiText>{"界面密度"}</UiText></UiElement><UiElement as="span"><UiText>{"按信息量调整间距"}</UiText></UiElement></UiElement>
          <Choices label="界面密度" options={[
            { id: "comfortable", name: "舒适", description: "常规行距" },
            { id: "compact", name: "紧凑", description: "缩小行距，显示更多内容" },
          ] satisfies Choice<Density>[]} value={appearance.density} onChange={(density) => appearance.setAppearance({ density })} className="appearance-density-options" />
        </UiElement>
      </UiElement>
      <UiElement as="div" className="appearance-section appearance-navigation-section">
        <UiElement as="div" className="appearance-section-label"><UiElement as="h3"><UiText>{"侧栏宽度"}</UiText></UiElement><UiElement as="span"><UiText>{"调整展开导航的宽度"}</UiText></UiElement></UiElement>
        <UiElement as="div" className="appearance-width-settings">
          <Choices label="侧栏宽度预设" options={[
            { id: "216", name: "紧凑", description: "216 px" },
            { id: "240", name: "标准", description: "240 px · 推荐" },
            { id: "280", name: "宽栏", description: "280 px" },
          ]} value={String(appearance.sidebarWidth)} onChange={(width) => appearance.setAppearance({ sidebarWidth: Number(width) })} className="appearance-width-options" />
          <UiElement as="div" className="appearance-width-range">
            <UiElement as="div" className="appearance-width-range-label"><UiElement as="label" htmlFor="sidebar-width"><UiText>{"自定义宽度"}</UiText></UiElement><UiElement as="output" htmlFor="sidebar-width">{appearance.sidebarWidth} <UiText>{"px"}</UiText></UiElement></UiElement>
            <UiElement as="input" id="sidebar-width" type="range" min={SIDEBAR_WIDTH.min} max={SIDEBAR_WIDTH.max} step={SIDEBAR_WIDTH.step}
              aria-label="侧栏宽度" aria-describedby="sidebar-width-hint" value={appearance.sidebarWidth}
              onChange={(event) => appearance.setAppearance({ sidebarWidth: Number(event.target.value) })} />
            <UiElement as="p" id="sidebar-width-hint">{SIDEBAR_WIDTH.min}<UiText>{"–"}</UiText>{SIDEBAR_WIDTH.max} <UiText>{"px，折叠导航保持 72 px。"}</UiText></UiElement>
          </UiElement>
        </UiElement>
      </UiElement>
      <UiElement as="div" className="appearance-section appearance-motion-section">
        <UiElement as="div" className="appearance-section-label"><UiElement as="h3"><UiText>{"交互动效"}</UiText></UiElement><UiElement as="span"><UiText>{"按操作习惯调整"}</UiText></UiElement></UiElement>
        <UiElement as="div" className="appearance-motion-settings">
          <Choices label="交互动效" options={[
            { id: "enhanced", name: "增强" }, { id: "standard", name: "标准" }, { id: "reduced", name: "减少" },
          ] satisfies Choice<MotionPreference>[]} value={appearance.motion} onChange={(motion) => appearance.setAppearance({ motion })} className="appearance-motion-options" />
          <UiElement as="p" className="appearance-motion-description">{motionDescriptions[appearance.resolvedMotion]}</UiElement>
          {appearance.motionReduced && appearance.motion !== "reduced" && <UiElement as="p" className="appearance-system-motion"><Monitor size={13} /><UiText>{"已遵循系统“减少动态效果”设置"}</UiText></UiElement>}
        </UiElement>
      </UiElement>
      <UiElement as="p" className="appearance-persistence"><UiElement as="span" aria-hidden="true" />{appearance.preferencesPersistent ? "外观更改会自动保存在当前浏览器，刷新与重新登录后保持。" : "当前浏览器未允许保存，外观更改仅本次打开有效。"}</UiElement>
    </UiElement>
  </UiRegion>;
}

const AppearanceSettings = withUiSlot("component.AppearanceSettings", DefaultAppearanceSettings);
export default AppearanceSettings;

// Reference package implementation exports.
export { DefaultAppearanceSettings };
