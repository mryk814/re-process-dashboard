import { expect, test } from "@playwright/test";

import { apiBaseUrl, resolveProjectBinding, starterCandidate } from "./helpers";

test("a completed decision replays historical evidence separately from later actuals", async ({ page, request }) => {
  const referenceResponse = await request.get(`${apiBaseUrl}/api/projects/default`);
  expect(referenceResponse.status(), await referenceResponse.text()).toBe(200);
  const reference = await referenceResponse.json() as { task_id: string };
  const binding = await resolveProjectBinding(request, reference.task_id);
  const projectResponse = await request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: "Decision Replay browser journey",
      ...binding,
      target_values: { TS: 500 },
    },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as { id: string };
  const starter = await starterCandidate(request, reference.task_id);
  const candidates = [] as Array<{ id: string; revision: number }>;
  const snapshots = [] as Array<{ id: string; created_at: string }>;
  for (const name of ["当時案 A", "当時案 B"]) {
    const candidateResponse = await request.post(
      `${apiBaseUrl}/api/projects/${project.id}/candidates`,
      { data: { ...starter, name } },
    );
    expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
    const candidate = await candidateResponse.json() as { id: string; revision: number };
    candidates.push(candidate);
    const snapshotResponse = await request.post(
      `${apiBaseUrl}/api/projects/${project.id}/candidates/${candidate.id}/snapshots`,
    );
    expect(snapshotResponse.status(), await snapshotResponse.text()).toBe(201);
    snapshots.push(await snapshotResponse.json() as { id: string; created_at: string });
  }
  const cutoffDate = new Date(snapshots.at(-1)!.created_at);
  const cutoffLocal = new Date(
    cutoffDate.getTime() + 1 - cutoffDate.getTimezoneOffset() * 60_000,
  ).toISOString().slice(0, 23);

  const actualResponse = await request.post(
    `${apiBaseUrl}/api/projects/${project.id}/candidates/${candidates[0].id}/actuals`,
    {
      params: { expected_revision: candidates[0].revision },
      data: {
        property: "TS",
        mean: 510,
        std: 0,
        replicates: 1,
        unit: "MPa",
        experiment_no: "REPLAY-E2E",
      },
    },
  );
  expect(actualResponse.status(), await actualResponse.text()).toBe(201);

  await page.goto(`/?view=candidate-review&project=${project.id}&candidate=${candidates[0].id}`);
  const panel = page.locator(".decision-replay-panel");
  await expect(panel).toBeVisible();
  await panel.locator(".decision-replay-create > summary").click();
  await panel.getByLabel("判断時刻").evaluate((element, value) => {
    const input = element as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set;
    if (!setter) throw new Error("datetime-local value setter is unavailable");
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, cutoffLocal);
  await expect(panel.locator(".decision-replay-fixed-evidence")).toContainText("当時案 A");
  await expect(panel.locator(".decision-replay-fixed-evidence")).toContainText("当時案 B");
  await expect(panel.locator(".decision-replay-fixed-evidence")).toContainText("後から得たActual: 1件");

  const caseResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith(`/projects/${project.id}/decision-cases`)
  ));
  await panel.getByRole("button", { name: "Decision Caseを固定" }).click();
  expect((await caseResponse).status()).toBe(201);
  const hindsightProjectResponse = await request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: "Decision Replay hindsight browser journey",
      ...binding,
      target_values: { TS: 500 },
    },
  });
  expect(hindsightProjectResponse.status(), await hindsightProjectResponse.text()).toBe(201);
  const hindsightProject = await hindsightProjectResponse.json() as { id: string };
  await page.reload();
  const historical = panel.locator(".decision-replay-layer.historical");
  const retrospective = panel.locator(".decision-replay-layer.retrospective");
  await expect(historical).toContainText("判断時点で利用できた証拠");
  await expect(retrospective).toContainText("実測と現在の見方");
  const historicalBox = await historical.boundingBox();
  const retrospectiveBox = await retrospective.boundingBox();
  expect(historicalBox).not.toBeNull();
  expect(retrospectiveBox).not.toBeNull();
  expect(historicalBox!.x).toBeLessThan(retrospectiveBox!.x);

  const hindsightSelect = retrospective.getByLabel("hindsight用の後発Project");
  await expect(hindsightSelect).toHaveValue(hindsightProject.id);
  const replayResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/replay-runs")
  ));
  await retrospective.getByRole("button", { name: "Replayを実行" }).click();
  expect((await replayResponse).status()).toBe(201);
  await expect(retrospective).toContainText("実測が一部または未到着です");
  await expect(retrospective).toContainText("未観測 target");
  await expect(retrospective).toContainText("選択した後発Project/Packageでの再評価（hindsight）");
  await expect(retrospective).toContainText("Decision Replay hindsight browser journey");

  await page.reload();
  await expect(panel).toContainText("判断時点で利用できた証拠");
  await expect(panel).toContainText("実測が一部または未到着です");
  await expect(panel).toContainText("選択した後発Project/Packageでの再評価（hindsight）");
  await expect(panel.locator(".decision-replay-history button[aria-current=true]")).toHaveCount(1);
  expect(snapshots).toHaveLength(2);
});
