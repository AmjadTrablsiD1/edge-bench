import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import constants from "../shared/constants.json" with { type: "json" };

// Dev only: Vite serves the UI and proxies /api to a pinned dev server
// (python main.py --no-browser --port <server.dev_port>).  Shipped: FastAPI serves ui/dist.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": `http://${constants.server.host}:${constants.server.dev_port}` }, fs: { allow: [".."] } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 900 },
});
