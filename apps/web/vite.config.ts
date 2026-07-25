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

  return {
    base: "./",
    plugins: [react()],
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
