import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.PARALLEL_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": { target: api, changeOrigin: false } } },
  preview: { port: 4173, proxy: { "/api": { target: api, changeOrigin: false } } },
  build: { chunkSizeWarningLimit: 3000, sourcemap: true },
});
