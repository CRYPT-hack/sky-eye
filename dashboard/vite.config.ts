import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The engine (python -m engine) listens here; `npm run dev` proxies the API to it.
const ENGINE_HTTP = "http://127.0.0.1:8000";
const ENGINE_WS = "ws://127.0.0.1:8000";

// Tests always run in Asia/Kolkata, whatever the machine's zone or its TZ variable.
// To check that a test does not depend on the zone, opt in with TEST_TZ, e.g.
// `TEST_TZ=UTC npm test` (env.test.ts, which pins the zone itself, then fails).
const TEST_TZ =
  (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env
    .TEST_TZ || "Asia/Kolkata";

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
    // Fixed zone so time-formatting tests give the same result on every machine.
    env: { TZ: TEST_TZ },
  },
});
