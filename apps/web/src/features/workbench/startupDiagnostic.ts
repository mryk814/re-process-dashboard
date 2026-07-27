export type StartupDiagnosticFinding = Readonly<{
  stage: string;
  resource_id: string;
  cause: string;
  impact: string;
  recovery_hint: string;
}>;

export type StartupDiagnostic = Readonly<{
  schema_version: "startup-diagnostic/v1";
  source: "workspace_preflight";
  log_path: string;
  recovery_route: string;
  report: Readonly<{
    status: "error";
    findings: StartupDiagnosticFinding[];
  }>;
}>;

function isFinding(value: unknown): value is StartupDiagnosticFinding {
  if (!value || typeof value !== "object") return false;
  const finding = value as Record<string, unknown>;
  return ["stage", "resource_id", "cause", "impact", "recovery_hint"]
    .every((key) => typeof finding[key] === "string");
}

export function parseStartupDiagnostic(value: unknown): StartupDiagnostic | null {
  if (!value || typeof value !== "object") return null;
  const diagnostic = value as Record<string, unknown>;
  const report = diagnostic.report as Record<string, unknown> | undefined;
  if (
    diagnostic.schema_version !== "startup-diagnostic/v1"
    || diagnostic.source !== "workspace_preflight"
    || typeof diagnostic.log_path !== "string"
    || typeof diagnostic.recovery_route !== "string"
    || report?.status !== "error"
    || !Array.isArray(report.findings)
    || !report.findings.every(isFinding)
  ) return null;
  return diagnostic as StartupDiagnostic;
}

export async function readStartupDiagnostic(): Promise<StartupDiagnostic | null> {
  try {
    const response = await fetch("/__workbench/startup-diagnostic.json", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (response.status === 204 || !response.ok) return null;
    return parseStartupDiagnostic(await response.json());
  } catch {
    return null;
  }
}
