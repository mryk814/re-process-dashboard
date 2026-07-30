export const devProxyStartupWindowMs = 20_000;

function errorCode(error: unknown): unknown {
  return typeof error === "object" && error !== null
    ? Reflect.get(error, "code")
    : undefined;
}

export function isDevProxyStartupRefusal(error: unknown, elapsedMs: number): boolean {
  return elapsedMs < devProxyStartupWindowMs && errorCode(error) === "ECONNREFUSED";
}

export function isDevProxyStartupLog(message: string, elapsedMs: number): boolean {
  return elapsedMs < devProxyStartupWindowMs
    && message.includes("http proxy error:")
    && message.includes("ECONNREFUSED");
}

export function devProxyStartupPayload(): string {
  return JSON.stringify({
    message: "ローカルAPIを起動しています。",
    code: "dev_api_starting",
    field_errors: [],
  });
}
