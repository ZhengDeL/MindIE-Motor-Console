import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AppearanceProvider, initializeAppearance } from "./appearance";
import { PRODUCT_NAME } from "./brand";
import "./styles.css";
import "./interaction-motion.css";
initializeAppearance();
document.title = PRODUCT_NAME;
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppearanceProvider>
      <App />
    </AppearanceProvider>
  </StrictMode>,
);
