import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  orderedWorkbenchSurfaces,
  workbenchSurfaceRegistry,
  workbenchSurfacesInZone,
} from "../src/features/workbench/workbenchSurfaceRegistry.ts";

test("Workbench Surface registry owns every allow-listed renderer zone", () => {
  assert.deepEqual(Object.keys(workbenchSurfaceRegistry).sort(), [
    "actual_measurement",
    "blend_tools",
    "curve_family",
    "feature_engineering",
    "response_contour",
    "response_curve",
    "similarity",
  ]);
  assert.equal("decision_activity" in workbenchSurfaceRegistry, false);
});

test("Task declaration order is canonical within each Surface zone", () => {
  const application = {
    workbench_surfaces: [
      { kind: "similarity", order: 40 },
      { kind: "response_contour", order: 30, axis_paths: ["process.x", "process.y"], grid_size: 11 },
      { kind: "response_curve", order: 20 },
      { kind: "actual_measurement", order: 10 },
      { kind: "feature_engineering", order: 50 },
    ],
  };
  assert.deepEqual(
    orderedWorkbenchSurfaces(application).map((surface) => surface.kind),
    ["actual_measurement", "response_curve", "response_contour", "similarity", "feature_engineering"],
  );
  assert.deepEqual(
    workbenchSurfacesInZone(application, "analysis_primary").map((surface) => surface.kind),
    ["response_curve", "response_contour"],
  );
});

test("prediction contour is lazy, revision-bound, and keeps support separate from colour", async () => {
  const source = await readFile(
    new URL("../src/features/workbench/ResponseContourPanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /!enabled \|\| !ready/);
  assert.match(source, /candidate\.raw\.revision/);
  assert.match(source, /candidateInputIdentity\(candidate\.raw\.inputs\)/);
  assert.match(source, /cell\.displayable/);
  assert.match(source, /contour-extrapolated/);
  assert.match(source, /数値で確認/);
  assert.match(source, /学習範囲外（予測値は非表示）/);
  const apiSource = await readFile(
    new URL("../src/shared/api/workbench-api.ts", import.meta.url),
    "utf8",
  );
  assert.match(apiSource, /response_contour", `\$\{expectedRevision\}/);
});
