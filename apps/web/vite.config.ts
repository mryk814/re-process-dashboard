import { createLogger, defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import {
  devProxyStartupPayload,
  isDevProxyStartupLog,
  isDevProxyStartupRefusal,
} from "./devProxyStartup";

export default defineConfig(() => {
  const proxyToken = process.env.WORKBENCH_DEV_PROXY_TOKEN;
  const apiTarget = process.env.WORKBENCH_DEV_API_URL ?? "http://127.0.0.1:8765";
  const webPort = Number(process.env.WORKBENCH_DEV_WEB_PORT ?? "5180");
  const proxyStartedAt = Date.now();
  let startupNoticeWritten = false;
  const logger = createLogger();
  const writeError = logger.error.bind(logger);
  logger.error = (message, options) => {
    if (isDevProxyStartupLog(message, Date.now() - proxyStartedAt)) {
      if (!startupNoticeWritten) {
        startupNoticeWritten = true;
        logger.info(`[proxy] ローカルAPIの起動を待っています: ${apiTarget}`, {
          timestamp: true,
        });
      }
      return;
    }
    writeError(message, options);
  };
  const proxy = proxyToken
    ? ({
        target: apiTarget,
        headers: { "X-Workbench-Launch-Token": proxyToken },
        configure(proxyServer) {
          proxyServer.on("error", (error, _request, response) => {
            if (
              !isDevProxyStartupRefusal(error, Date.now() - proxyStartedAt)
              || !response
              || !("writeHead" in response)
              || response.headersSent
              || response.writableEnded
            ) {
              return;
            }
            response.writeHead(503, {
              "Cache-Control": "no-store",
              "Content-Type": "application/json; charset=utf-8",
              "Retry-After": "1",
            });
            response.end(devProxyStartupPayload());
          });
        },
      } satisfies ProxyOptions)
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
    customLogger: logger,
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
