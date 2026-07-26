import createClient from "openapi-fetch";
import type { components, paths } from "../../generated/api-types";

export type ApiErrorKind = "not_found" | "conflict" | "validation" | "server" | "network";
export type ApiDomainErrorCode = components["schemas"]["ApiError"]["code"];
type ApiErrorPayload = components["schemas"]["ApiError"];

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind,
    readonly status: number,
    readonly fieldErrors: Array<{ path: string; message: string }> = [],
    readonly code?: ApiDomainErrorCode,
    readonly currentCandidate?: components["schemas"]["Candidate"] | null,
    readonly availability?: components["schemas"]["SubsystemAvailability"] | null,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

export type DesktopWorkspaceSummary = {
  bundleId: string;
  createdAt: string;
  appVersion: string;
  projectCount: number;
  candidateCount: number;
  snapshotCount: number;
  activityCount: number;
  chainCount: number;
  sourceLifecycleCount: number;
  resourceCount: number;
  warnings: string[];
};

export type DesktopWorkspaceOperationResult =
  | { status: "cancelled" }
  | { status: "created"; fileName: string; sizeBytes: number; summary: DesktopWorkspaceSummary }
  | { status: "prepared"; fileName: string; summary: DesktopWorkspaceSummary }
  | { status: "restored"; summary: DesktopWorkspaceSummary };

declare global {
  interface Window {
    workbenchDesktop?: Readonly<{
      apiBaseUrl: string;
      launchToken: string;
      exportWorkspace: () => Promise<DesktopWorkspaceOperationResult>;
      prepareWorkspaceRestore: () => Promise<DesktopWorkspaceOperationResult>;
      confirmWorkspaceRestore: () => Promise<DesktopWorkspaceOperationResult>;
      cancelWorkspaceRestore: () => Promise<DesktopWorkspaceOperationResult>;
      takeWorkspaceNotice: () => Promise<{ tone: "success" | "error"; message: string } | null>;
    }>;
  }
}

const desktopConfig = window.workbenchDesktop;
const baseUrl = desktopConfig?.apiBaseUrl ?? import.meta.env.VITE_API_URL ?? "";
export const apiBaseUrl = baseUrl;

const normalizedFetch: typeof fetch = async (input, init) => {
  try {
    const request = new Request(input, init);
    if (!desktopConfig?.launchToken) return await fetch(request);
    const headers = new Headers(request.headers);
    headers.set("X-Workbench-Launch-Token", desktopConfig.launchToken);
    return await fetch(new Request(request, { headers }));
  } catch (cause) {
    throw new ApiClientError(
      "APIへ接続できませんでした。接続状態を確認して、もう一度お試しください。",
      "network",
      0,
    );
  }
};

export const apiClient = createClient<paths>({ baseUrl, fetch: normalizedFetch });

function objectValue(value: unknown, key: string): unknown {
  return typeof value === "object" && value !== null ? Reflect.get(value, key) : undefined;
}

function isApiErrorPayload(value: unknown): value is ApiErrorPayload {
  return typeof objectValue(value, "message") === "string"
    && typeof objectValue(value, "code") === "string";
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
  const payload = isApiErrorPayload(result.error) ? result.error : undefined;
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
    payload?.code,
    payload?.current_candidate,
    payload?.availability,
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
