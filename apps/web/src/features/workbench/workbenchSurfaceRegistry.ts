import type { ApplicationCapability } from "../candidates";

export type WorkbenchSurface =
  ApplicationCapability["workbench_surfaces"][number];
export type WorkbenchSurfaceKind = WorkbenchSurface["kind"];
export type WorkbenchSurfaceZone =
  | "before_activity"
  | "analysis_primary"
  | "analysis_evidence"
  | "after_analysis";

export const workbenchSurfaceRegistry: Record<
  WorkbenchSurfaceKind,
  { zone: WorkbenchSurfaceZone; label: string }
> = {
  blend_tools: { zone: "before_activity", label: "配合ツール" },
  actual_measurement: { zone: "before_activity", label: "予測と実測" },
  curve_family: { zone: "before_activity", label: "二変数感度" },
  response_curve: { zone: "analysis_primary", label: "応答曲線" },
  response_contour: { zone: "analysis_primary", label: "予測地図" },
  similarity: { zone: "analysis_evidence", label: "近い過去実績" },
  feature_engineering: { zone: "after_analysis", label: "モデル入力" },
};

export function orderedWorkbenchSurfaces(
  application: ApplicationCapability | undefined,
): WorkbenchSurface[] {
  return [...(application?.workbench_surfaces ?? [])].sort(
    (left, right) => left.order - right.order,
  );
}

export function workbenchSurfacesInZone(
  application: ApplicationCapability | undefined,
  zone: WorkbenchSurfaceZone,
): WorkbenchSurface[] {
  return orderedWorkbenchSurfaces(application).filter(
    (surface) => workbenchSurfaceRegistry[surface.kind].zone === zone,
  );
}
