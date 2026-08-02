import assert from "node:assert/strict";
import test from "node:test";

async function navigationModule(search) {
  globalThis.window = { location: { search, pathname: "/", hash: "" } };
  return import(`../src/app/navigation.ts?analysis=${encodeURIComponent(search)}-${Date.now()}-${Math.random()}`);
}

test("shareable analysis identities round-trip through one navigation intent", async () => {
  const { navigationUrl, readNavigationIntent } = await navigationModule(
    "?view=chain-graph&project=chain-1&candidate=c1&chain_edge=stage%3AA-%3EB.input%3A0",
  );
  const edgeIntent = readNavigationIntent();
  assert.deepEqual(edgeIntent.chainInspection, {
    kind: "edge",
    id: "stage:A->B.input:0",
  });
  assert.match(navigationUrl(edgeIntent), /chain_edge=stage%3AA-%3EB.input%3A0/);

  const workbench = await navigationModule(
    "?view=candidates&project=p1&candidate=c1&evidence_surface=prediction_space",
  );
  const workbenchIntent = workbench.readNavigationIntent();
  assert.equal(workbenchIntent.workbenchSurface, "prediction_space");
  assert.match(workbench.navigationUrl(workbenchIntent), /evidence_surface=prediction_space/);

  const screening = await navigationModule(
    "?view=explore&project=p1&candidate=c1&screening=r1&screening_surface=evaluated",
  );
  const screeningIntent = screening.readNavigationIntent();
  assert.equal(screeningIntent.screeningResultSurface, "evaluated");
  assert.match(screening.navigationUrl(screeningIntent), /screening_surface=evaluated/);

  const snapshot = await navigationModule(
    "?view=candidates&project=chain-1&candidate=c1&chain_snapshot=snapshot-9",
  );
  const snapshotIntent = snapshot.readNavigationIntent();
  assert.equal(snapshotIntent.chainSnapshotId, "snapshot-9");
  assert.match(snapshot.navigationUrl(snapshotIntent), /chain_snapshot=snapshot-9/);
});

test("unknown and ambiguous selections remain explainable instead of normalizing away", async () => {
  const invalid = await navigationModule(
    "?view=candidates&evidence_surface=not-real",
  );
  const invalidIntent = invalid.readNavigationIntent();
  assert.equal(invalidIntent.workbenchSurface, undefined);
  assert.equal(invalidIntent.workbenchSurfaceError, "not-real");
  assert.match(invalid.navigationUrl(invalidIntent), /evidence_surface=not-real/);

  const invalidScreening = await navigationModule(
    "?view=explore&screening_surface=missing",
  );
  const invalidScreeningIntent = invalidScreening.readNavigationIntent();
  assert.equal(invalidScreeningIntent.screeningResultSurface, undefined);
  assert.equal(invalidScreeningIntent.screeningResultSurfaceError, "missing");
  assert.match(
    invalidScreening.navigationUrl(invalidScreeningIntent),
    /screening_surface=missing/,
  );

  const ambiguous = await navigationModule(
    "?view=chain-graph&chain_stage=A&chain_edge=edge-1",
  );
  const ambiguousIntent = ambiguous.readNavigationIntent();
  assert.equal(ambiguousIntent.chainInspection, undefined);
  assert.deepEqual(ambiguousIntent.chainInspectionError, {
    kind: "ambiguous",
    stageId: "A",
    edgeId: "edge-1",
  });
  assert.match(ambiguous.navigationUrl(ambiguousIntent), /chain_stage=A/);
  assert.match(ambiguous.navigationUrl(ambiguousIntent), /chain_edge=edge-1/);
});

test("workbench surface availability waits for the application capability", async () => {
  const { resolvePrimaryWorkbenchSurface } = await import(
    `../src/features/workbench/workbenchSurfaceRegistry.ts?analysis=${Date.now()}-${Math.random()}`
  );
  assert.deepEqual(
    resolvePrimaryWorkbenchSurface(undefined, "prediction_space"),
    { status: "loading", surfaces: [] },
  );

  const application = {
    candidate_excel_export: false,
    candidate_excel_import: false,
    project_creation: true,
    sparse_blend: false,
    workbench_surfaces: [
      { kind: "response_curve", order: 10 },
      { kind: "prediction_space", order: 20 },
    ],
  };
  const resolved = resolvePrimaryWorkbenchSurface(
    application,
    "prediction_space",
  );
  assert.equal(resolved.status, "ready");
  assert.equal(resolved.selected?.kind, "prediction_space");
  assert.equal(resolved.unavailable, undefined);
});

test("view changes retain only identities owned by the destination", async () => {
  const { readNavigationIntent, withView } = await navigationModule(
    "?view=explore&project=p1&candidate=c1&screening=r1&screening_surface=map",
  );
  const explore = readNavigationIntent();
  const candidates = withView(explore, "candidates");
  assert.equal(candidates.workbenchSurface, undefined);
  assert.equal(candidates.screeningResultSurface, undefined);
  assert.equal(candidates.screeningRunId, undefined);

  const project = withView(explore, "project");
  assert.equal(project.workbenchSurface, undefined);
  assert.equal(project.screeningResultSurface, undefined);

  const wrongView = await navigationModule("?view=project&chain_edge=edge-1");
  assert.equal(wrongView.readNavigationIntent().chainInspection, undefined);
  assert.doesNotMatch(
    wrongView.navigationUrl(wrongView.readNavigationIntent()),
    /chain_edge=/,
  );
});
