import { UiElement, withUiSlot } from "./uiPluginSdk";
import { useState } from "react";
import { useBranding } from "./brandingState";
import "./branding.css";

function DefaultBrandIdentity({ name, subtitle, iconUrl, compact = false, variant = "sidebar" }: {
  name: string; subtitle: string; iconUrl: string | null; compact?: boolean; variant?: "sidebar" | "login" | "preview";
}) {
  const [failedIcon, setFailedIcon] = useState<string | null>(null);
  const hasIcon = !!iconUrl && failedIcon !== iconUrl;
  return <UiElement as="div" className={`brand platform-brand platform-brand-${variant}${compact ? " compact" : ""}${hasIcon ? " has-icon" : ""}`}
    title={subtitle ? `${name} · ${subtitle}` : name} aria-label={name}>
    {hasIcon && <UiElement as="img" className="platform-brand-icon" src={iconUrl!} alt="" aria-hidden="true" decoding="async"
      onError={() => setFailedIcon(iconUrl)} />}
    {(!compact || !hasIcon) && <UiElement as="div" className="brand-wordmark platform-brand-wordmark">
      <UiElement as="b" title={name}>{compact ? Array.from(name)[0] : name}</UiElement>
      {!compact && subtitle && <UiElement as="small" title={subtitle}>{subtitle}</UiElement>}
    </UiElement>}
  </UiElement>;
}

function DefaultPlatformBrand({ compact = false, variant = "sidebar" }: { compact?: boolean; variant?: "sidebar" | "login" }) {
  const branding = useBranding();
  return <BrandIdentity name={branding.name} subtitle={branding.subtitle} iconUrl={branding.icon_url} compact={compact} variant={variant} />;
}

export const BrandIdentity = withUiSlot("component.BrandIdentity", DefaultBrandIdentity);
const PlatformBrand = withUiSlot("component.PlatformBrand", DefaultPlatformBrand);
export default PlatformBrand;

// Reference package implementation exports.
export { DefaultBrandIdentity, DefaultPlatformBrand };
