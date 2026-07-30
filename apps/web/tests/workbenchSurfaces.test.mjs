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
    "input_space",
    "prediction_space",
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
      { kind: "prediction_space", order: 25, target_keys: ["TS", "YS"], historical_limit: 200 },
      { kind: "response_curve", order: 20 },
      { kind: "input_space", order: 35, distance_target_key: "TS", seed: 508, landmark_limit: 96, historical_limit: 240 },
      { kind: "actual_measurement", order: 10 },
      { kind: "feature_engineering", order: 50 },
    ],
  };
  assert.deepEqual(
    orderedWorkbenchSurfaces(application).map((surface) => surface.kind),
    ["actual_measurement", "response_curve", "prediction_space", "response_contour", "input_space", "similarity", "feature_engineering"],
  );
  assert.deepEqual(
    workbenchSurfacesInZone(application, "analysis_primary").map((surface) => surface.kind),
    ["response_curve", "prediction_space", "response_contour", "input_space"],
  );
});

test("input space is lazy and keeps island support separate from candidate novelty", async () => {
  const source = await readFile(
    new URL("../src/features/workbench/InputSpacePanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /if \(!active \|\| !ready \|\| !selected\) return/);
  assert.match(source, /island_distance/);
  assert.match(source, /candidate_novelty/);
  assert.match(source, /図上の距離ではなくTask距離で判定します/);
  assert.match(source, /HistoricalEvidenceDrawer/);
});

test("prediction space is active-only and keeps marginal intervals distinct from joint probability", async () => {
  const source = await readFile(
    new URL("../src/features/workbench/PredictionSpacePanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /if \(!active \|\| !selectedCandidate \|\| !xTarget \|\| !yTarget/);
  assert.match(source, /outputSpaceEvidence/);
  assert.match(source, /distanceFilter/);
  assert.match(source, /HistoricalEvidenceDrawer/);
  assert.match(source, /prediction-space-interval/);
  assert.match(source, /実測ばらつき σ/);
  assert.match(source, /2特性を同時に含む確率領域ではありません/);
  assert.match(source, /予測値や同一試料の相関ではありません/);
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
  assert.match(source, /既存実績から遠い（予測値は非表示）/);
  assert.match(source, /payloadIdentity === requestIdentity/);
  const apiSource = await readFile(
    new URL("../src/shared/api/workbench-api.ts", import.meta.url),
    "utf8",
  );
  assert.match(apiSource, /response_contour", `\$\{expectedRevision\}/);
});
