import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The engine (python -m engine) listens here; `npm run dev` proxies the API to it.
const ENGINE_HTTP = "http://127.0.0.1:8000";
const ENGINE_WS = "ws://127.0.0.1:8000";

export default defineConfig({
  base: "/",
  plugins: [react()],
  build: {
    outDir: "dist",
  },
  server: {
    proxy: {
      "/api": ENGINE_HTTP,
      "/media": ENGINE_HTTP,
      "/stream.mjpg": ENGINE_HTTP,
      "/ws": { target: ENGINE_WS, ws: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // Vitest blanks CSS imports by default; let `?raw` imports through so tests can read the real file.
    css: { include: [/\.css\?raw$/] },
    // Fixed zone so time-formatting tests give the same result on every machine;
    // run with TZ=... to check that a test does not depend on it.
    env: { TZ: (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.TZ ?? "Asia/Kolkata" },
  },
});
