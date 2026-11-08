import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by FastAPI at /app (see receiver/api.py). In dev, /api and the classic pages proxy to uvicorn.
const backend = process.env.RECEIVER_URL ?? "http://127.0.0.1:8766";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/sources/file": backend,
      "/static": backend,
    },
  },
});
