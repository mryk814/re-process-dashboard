import createClient from "openapi-fetch";
import type { paths } from "../../generated/api-types";

export type ApiErrorKind = "not_found" | "conflict" | "validation" | "server" | "network";

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind,
    readonly status: number,
    readonly fieldErrors: Array<{ path: string; message: string }> = [],
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

const baseUrl = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8765";
export const apiBaseUrl = baseUrl;

const normalizedFetch: typeof fetch = async (input, init) => {
  try {
    return await fetch(input, init);
  } catch (cause) {
    throw new ApiClientError(
      cause instanceof Error ? cause.message : "APIへ接続できませんでした。",
      "network",
      0,
    );
  }
};

export const apiClient = createClient<paths>({ baseUrl, fetch: normalizedFetch });

function objectValue(value: unknown, key: string): unknown {
  return typeof value === "object" && value !== null ? Reflect.get(value, key) : undefined;
}

function normalizeFieldErrors(value: unknown): Array<{ path: string; message: string }> {
  const items = objectValue(value, "field_errors");
  if (!Array.isArray(items)) return [];
  return items.flatMap((item) => {
    const path = objectValue(item, "path");
    const message = objectValue(item, "message");
    return typeof path === "string" && typeof message === "string" ? [{ path, message }] : [];
  });
}

export function requireData<T>(
  result: { data?: T; error?: unknown; response: Response },
  fallback: string,
): T {
  if (result.data !== undefined) return result.data;
  const message = objectValue(result.error, "message");
  const status = result.response.status;
  const kind: ApiErrorKind = status === 404
    ? "not_found"
    : status === 409
      ? "conflict"
      : status === 422
        ? "validation"
        : status >= 500
          ? "server"
          : "network";
  throw new ApiClientError(
    typeof message === "string" && message ? message : fallback,
    kind,
    status,
    normalizeFieldErrors(result.error),
  );
}

export function requireSuccess(
  result: { error?: unknown; response: Response },
  fallback: string,
): void {
  if (result.response.ok) return;
  requireData({ ...result, data: undefined }, fallback);
}

export function apiDownloadUrl(path: string): string {
  return `${baseUrl}${path}`;
}
