import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Built as plain static files and served by the CyberLab control plane at /app.
// `npm run dev` proxies API calls to a control plane on localhost:8000.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  // es2022: noVNC (remote desktop) uses top-level await (Chrome/Edge 89+, Firefox 89+, Safari 15+).
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  server: {
    proxy: {
      "/v1": { target: "http://127.0.0.1:8000", ws: true },
      "/healthz": "http://127.0.0.1:8000",
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
