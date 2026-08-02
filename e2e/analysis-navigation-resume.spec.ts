import { expect, test } from "@playwright/test";

import { apiBaseUrl, createProjectWithCandidate } from "./helpers";

test("analysis selections survive share, reload, and browser travel without silent fallback", async ({ page, request }) => {
  test.setTimeout(120_000);
  const project = await createProjectWithCandidate(
    request,
    "annealed-properties-v1",
    `分析位置の共有 ${Date.now()}`,
    "基準候補",
  );

  await page.goto(`/?view=candidates&project=${project.id}&evidence_surface=not-real`);
  await expect(page.getByRole("heading", { name: "指定された分析面を表示できません" })).toBeVisible();
  await page.getByRole("button", { name: "利用可能な分析面を表示" }).click();
  const primaryTabs = page.getByRole("tablist", { name: "予測の見方" }).getByRole("tab");
  await expect(primaryTabs.nth(1)).toBeVisible();
  const selectedSurface = primaryTabs.nth(1);
  await selectedSurface.click();
  const selectedSurfaceId = await selectedSurface.getAttribute("id");
  const selectedSurfaceKind = new URL(page.url()).searchParams.get("evidence_surface");
  expect(selectedSurfaceKind).toBeTruthy();
  await page.reload();
  await expect(page.locator(`#${selectedSurfaceId}`)).toHaveAttribute("aria-selected", "true");

  await page.getByRole("navigation", { name: "プロジェクト内メニュー" })
    .getByRole("button", { name: "範囲探索", exact: true }).click();
  await page.locator(".screening-mode-options").getByRole("button", { name: /領域を見る/ }).click();
  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/screening"
  ));
  await page.locator(".screening-run-footer .primary-button").click();
  expect((await runResponse).status()).toBe(201);
  await expect(page.locator(".screening-result-tabs")).toBeVisible();
  const screeningUrl = new URL(page.url());
  screeningUrl.searchParams.set("screening_surface", "not-real");
  await page.goto(screeningUrl.toString());
  await expect(page.getByRole("heading", { name: "指定された探索結果を表示できません" })).toBeVisible();
  await page.getByRole("button", { name: "全評価点を表示" }).click();
  await expect(page).toHaveURL(/screening_surface=evaluated/);
  await page.reload();
  await expect(page.locator(".screening-evaluated-table")).toBeVisible();

  const chainsResponse = await request.get(`${apiBaseUrl}/api/chains`);
  expect(chainsResponse.status(), await chainsResponse.text()).toBe(200);
  const chains = await chainsResponse.json() as Array<{
    definition: { chain_id: string };
    revisions: Array<{ revision: number; revision_digest: string }>;
  }>;
  const chain = chains.find((item) => item.definition.chain_id === "welding-consumable-a-b-c-v1");
  expect(chain).toBeTruthy();
  const revision = chain!.revisions[0];
  const chainProjectResponse = await request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: `Chain分析位置 ${Date.now()}`,
      scientific_identity: {
        identity_kind: "chain",
        chain_revision_id: `${chain!.definition.chain_id}:r${revision.revision}`,
        chain_revision_digest: revision.revision_digest,
      },
    },
  });
  expect(chainProjectResponse.status(), await chainProjectResponse.text()).toBe(201);
  const chainProject = await chainProjectResponse.json() as { id: string };
  const contractResponse = await request.get(
    `${apiBaseUrl}/api/projects/${chainProject.id}/chain/candidate-contract`,
  );
  expect(contractResponse.status(), await contractResponse.text()).toBe(200);
  const contract = await contractResponse.json() as { starter_candidate: object };
  const candidateResponse = await request.post(
    `${apiBaseUrl}/api/projects/${chainProject.id}/chain/candidates`,
    { data: contract.starter_candidate },
  );
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
  const candidate = await candidateResponse.json() as { id: string; revision: number };
  const executionResponse = await request.post(
    `${apiBaseUrl}/api/projects/${chainProject.id}/chain/candidates/${candidate.id}/executions`,
    { data: { candidate_revision: candidate.revision, request_id: `resume-${Date.now()}`, debounce_ms: 0 } },
  );
  expect(executionResponse.status(), await executionResponse.text()).toBe(200);
  const snapshotResponse = await request.post(
    `${apiBaseUrl}/api/projects/${chainProject.id}/chain/candidates/${candidate.id}/snapshots`,
    { data: { candidate_revision: candidate.revision, debounce_ms: 0 } },
  );
  expect(snapshotResponse.status(), await snapshotResponse.text()).toBe(201);
  const snapshot = await snapshotResponse.json() as { snapshot_id: string };

  await page.goto(`/?view=chain-graph&project=${chainProject.id}&candidate=${candidate.id}&chain_stage=missing-stage`);
  await expect(page.getByRole("heading", { name: "指定された検査対象を表示できません" })).toBeVisible();
  await page.locator(".chain-graph-node-button").first().click();
  await expect(page).toHaveURL(/chain_stage=/);
  await page.locator(".chain-graph-rail").first().click();
  const edgeId = new URL(page.url()).searchParams.get("chain_edge");
  expect(edgeId).toBeTruthy();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Binding inspector" })).toBeVisible();

  await page.goto(
    `/?view=candidates&project=${chainProject.id}&candidate=${candidate.id}`
    + `&chain_snapshot=${snapshot.snapshot_id}`,
  );
  await expect(page.locator(".chain-snapshot-evidence")).toBeVisible();
  await page.locator(".chain-snapshot-evidence > summary").click();
  await expect(page).toHaveURL(new RegExp(`chain_snapshot=${snapshot.snapshot_id}`));
  await page.reload();
  await page.locator(".chain-snapshot-evidence > summary").click();
  await expect(page.locator(".chain-snapshot-heading select")).toHaveValue(snapshot.snapshot_id);

  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`chain_edge=${encodeURIComponent(edgeId!)}`));
  await expect(page.getByRole("heading", { name: "Binding inspector" })).toBeVisible();
  await page.goForward();
  await page.locator(".chain-snapshot-evidence > summary").click();
  await expect(page.locator(".chain-snapshot-heading select")).toHaveValue(snapshot.snapshot_id);

  await page.goto(`/?view=candidates&project=${chainProject.id}&candidate=${candidate.id}&chain_snapshot=missing-snapshot`);
  await expect(page.getByRole("heading", { name: "指定された固定Snapshotを表示できません" })).toBeVisible();
  await expect(page).toHaveURL(/chain_snapshot=missing-snapshot/);
});
