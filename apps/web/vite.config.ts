import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(() => {
  const proxyToken = process.env.WORKBENCH_DEV_PROXY_TOKEN;
  const apiTarget = process.env.WORKBENCH_DEV_API_URL ?? "http://127.0.0.1:8765";
  const webPort = Number(process.env.WORKBENCH_DEV_WEB_PORT ?? "5180");
  const proxy = proxyToken
    ? {
        target: apiTarget,
        headers: { "X-Workbench-Launch-Token": proxyToken },
      }
    : undefined;
  const startupDiagnostic = process.env.WORKBENCH_STARTUP_DIAGNOSTIC;
  const startupDiagnosticPlugin = {
    name: "workbench-startup-diagnostic",
    configureServer(server: { middlewares: { use: (path: string, handler: (request: { method?: string }, response: { statusCode: number; setHeader: (name: string, value: string) => void; end: (body?: string) => void }, next: () => void) => void) => void } }) {
      server.middlewares.use("/__workbench/startup-diagnostic.json", (request, response, next) => {
        if (request.method !== "GET") {
          next();
          return;
        }
        response.setHeader("Cache-Control", "no-store");
        if (!startupDiagnostic) {
          response.statusCode = 204;
          response.end();
          return;
        }
        response.setHeader("Content-Type", "application/json; charset=utf-8");
        response.end(startupDiagnostic);
      });
    },
  };

  return {
    base: "./",
    plugins: [react(), startupDiagnosticPlugin],
    server: {
      host: "127.0.0.1",
      port: webPort,
      strictPort: true,
      proxy: proxy
        ? {
            "/api": proxy,
            "/health": proxy,
            "/docs": proxy,
            "/openapi.json": proxy,
          }
        : undefined,
    },
  };
});
