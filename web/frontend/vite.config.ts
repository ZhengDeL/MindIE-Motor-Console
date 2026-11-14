import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  resolve: { dedupe: ["react", "react-dom"] },
  server: { port: 5174, strictPort: true, proxy: { "/api": "http://127.0.0.1:19080" } },
  preview: { port: 4174, strictPort: true },
  build: {
    outDir: "dist", sourcemap: false,
    rollupOptions: { output: { manualChunks(id) {
      if (/node_modules\/(?:react|react-dom|scheduler)\//.test(id)) return "react-vendor";
      if (id.includes("node_modules/lucide-react/")) return "icons";
    } } },
  },
});
