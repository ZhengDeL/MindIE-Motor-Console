import type { ReactNode } from "react";
import "./styles.css";
import "./interaction-motion.css";
import "./ui-plugins.css";

/** Ordinary default theme provider; its global design styles load only when selected. */
export default function DefaultUiTheme({ children }: { children?: ReactNode }) {
  return <>{children}</>;
}
