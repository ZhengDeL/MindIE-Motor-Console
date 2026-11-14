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
  return <div className={`appearance-selection ${className}`} role="radiogroup" aria-label={label}>
    {options.map((option, index) => <button key={option.id} type="button" role="radio"
      aria-label={option.name} aria-checked={option.id === value} tabIndex={index === focusIndex ? 0 : -1}
      className={`appearance-option ${option.id === value ? "is-selected" : ""}`}
      ref={(element) => { buttons.current[index] = element; }}
      onClick={() => onChange(option.id)} onKeyDown={(event) => move(event, index)}>
      {option.decoration}<span className="appearance-option-label">{option.name}</span>
      {option.description && <span className="appearance-option-description">{option.description}</span>}
      <span className="appearance-check" aria-hidden="true"><Check size={13} /></span>
    </button>)}
  </div>;
}

const modeIcons = { light: Sun, dark: Moon, system: Monitor };
const modeDescriptions = { light: "清晰明亮的工作区", dark: "适合低亮环境", system: "随设备外观自动切换" };
const motionDescriptions: Record<MotionPreference, string> = {
  enhanced: "保留完整的页面和面板过渡。", standard: "使用简短的页面过渡和操作反馈。", reduced: "停用非必要过渡，直接显示状态变化。",
};

export default function AppearanceSettings() {
  const appearance = useAppearance();
  const modes = (["light", "dark", "system"] satisfies ColorMode[]).map((id) => {
    const Icon = modeIcons[id];
    return { id, name: id === "light" ? "浅色" : id === "dark" ? "深色" : "跟随系统",
      description: modeDescriptions[id], decoration: <Icon size={16} className="appearance-mode-icon" /> };
  });
  return <section className="panel appearance-panel appearance-studio" aria-labelledby="appearance-title">
    <div className="appearance-studio-heading">
      <div><h2 id="appearance-title">界面外观</h2><p>选择显示模式、主题配色和导航布局。</p></div>
      <button type="button" className="btn btn-default appearance-reset" onClick={() => appearance.setAppearance({ theme: "blue", mode: "system", density: "comfortable", motion: "standard", sidebarWidth: SIDEBAR_WIDTH.default })}><RefreshCw size={14} />恢复默认</button>
    </div>
    <div className="appearance-studio-body">
      <div className="appearance-section appearance-mode-section">
        <div className="appearance-section-label"><h3>显示模式</h3><span>选择后立即生效</span></div>
        <Choices label="显示模式" options={modes} value={appearance.mode} onChange={(mode) => appearance.setAppearance({ mode })} className="appearance-mode-options" />
      </div>
      <div className="appearance-details-grid">
        <div className="appearance-section appearance-accent-section">
          <div className="appearance-section-label"><h3>主题配色</h3><span>用于链接、焦点与选中态</span></div>
          <Choices label="主题配色" options={THEMES.map((theme) => ({ ...theme, decoration: <span className="appearance-accent-swatch" style={{ "--swatch-color": theme.color } as CSSProperties} aria-hidden="true"><Check size={16} /></span> }))}
            value={appearance.theme} onChange={(theme) => appearance.setAppearance({ theme })} className="appearance-accent-options" />
        </div>
        <div className="appearance-section appearance-density-section">
          <div className="appearance-section-label"><h3>界面密度</h3><span>按信息量调整间距</span></div>
          <Choices label="界面密度" options={[
            { id: "comfortable", name: "舒适", description: "常规行距" },
            { id: "compact", name: "紧凑", description: "缩小行距，显示更多内容" },
          ] satisfies Choice<Density>[]} value={appearance.density} onChange={(density) => appearance.setAppearance({ density })} className="appearance-density-options" />
        </div>
      </div>
      <div className="appearance-section appearance-navigation-section">
        <div className="appearance-section-label"><h3>侧栏宽度</h3><span>调整展开导航的宽度</span></div>
        <div className="appearance-width-settings">
          <Choices label="侧栏宽度预设" options={[
            { id: "216", name: "紧凑", description: "216 px" },
            { id: "240", name: "标准", description: "240 px · 推荐" },
            { id: "280", name: "宽栏", description: "280 px" },
          ]} value={String(appearance.sidebarWidth)} onChange={(width) => appearance.setAppearance({ sidebarWidth: Number(width) })} className="appearance-width-options" />
          <div className="appearance-width-range">
            <div className="appearance-width-range-label"><label htmlFor="sidebar-width">自定义宽度</label><output htmlFor="sidebar-width">{appearance.sidebarWidth} px</output></div>
            <input id="sidebar-width" type="range" min={SIDEBAR_WIDTH.min} max={SIDEBAR_WIDTH.max} step={SIDEBAR_WIDTH.step}
              aria-label="侧栏宽度" aria-describedby="sidebar-width-hint" value={appearance.sidebarWidth}
              onChange={(event) => appearance.setAppearance({ sidebarWidth: Number(event.target.value) })} />
            <p id="sidebar-width-hint">{SIDEBAR_WIDTH.min}–{SIDEBAR_WIDTH.max} px，折叠导航保持 72 px。</p>
          </div>
        </div>
      </div>
      <div className="appearance-section appearance-motion-section">
        <div className="appearance-section-label"><h3>交互动效</h3><span>按操作习惯调整</span></div>
        <div className="appearance-motion-settings">
          <Choices label="交互动效" options={[
            { id: "enhanced", name: "增强" }, { id: "standard", name: "标准" }, { id: "reduced", name: "减少" },
          ] satisfies Choice<MotionPreference>[]} value={appearance.motion} onChange={(motion) => appearance.setAppearance({ motion })} className="appearance-motion-options" />
          <p className="appearance-motion-description">{motionDescriptions[appearance.resolvedMotion]}</p>
          {appearance.motionReduced && appearance.motion !== "reduced" && <p className="appearance-system-motion"><Monitor size={13} />已遵循系统“减少动态效果”设置</p>}
        </div>
      </div>
      <p className="appearance-persistence"><span aria-hidden="true" />{appearance.preferencesPersistent ? "外观更改会自动保存在当前浏览器，刷新与重新登录后保持。" : "当前浏览器未允许保存，外观更改仅本次打开有效。"}</p>
    </div>
  </section>;
}
