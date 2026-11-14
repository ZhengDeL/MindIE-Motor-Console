import { useState } from "react";
import { useBranding } from "./brandingState";
import "./branding.css";

export function BrandIdentity({ name, subtitle, iconUrl, compact = false, variant = "sidebar" }: {
  name: string; subtitle: string; iconUrl: string | null; compact?: boolean; variant?: "sidebar" | "login" | "preview";
}) {
  const [failedIcon, setFailedIcon] = useState<string | null>(null);
  const hasIcon = !!iconUrl && failedIcon !== iconUrl;
  return <div className={`brand platform-brand platform-brand-${variant}${compact ? " compact" : ""}${hasIcon ? " has-icon" : ""}`}
    title={subtitle ? `${name} · ${subtitle}` : name} aria-label={name}>
    {hasIcon && <img className="platform-brand-icon" src={iconUrl!} alt="" aria-hidden="true" decoding="async"
      onError={() => setFailedIcon(iconUrl)} />}
    {(!compact || !hasIcon) && <div className="brand-wordmark platform-brand-wordmark">
      <b title={name}>{compact ? Array.from(name)[0] : name}</b>
      {!compact && subtitle && <small title={subtitle}>{subtitle}</small>}
    </div>}
  </div>;
}

export default function PlatformBrand({ compact = false, variant = "sidebar" }: { compact?: boolean; variant?: "sidebar" | "login" }) {
  const branding = useBranding();
  return <BrandIdentity name={branding.name} subtitle={branding.subtitle} iconUrl={branding.icon_url} compact={compact} variant={variant} />;
}
