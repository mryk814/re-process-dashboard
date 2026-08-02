import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { apiBaseUrl as api, createProjectWithCandidate } from "./helpers";

test.setTimeout(90_000);

async function createProject(request: APIRequestContext, taskId: string, name: string) {
  return createProjectWithCandidate(request, taskId, name, "候補化の基準条件");
}

async function runScreening(page: Page) {
  await page.locator(".screening-question-action .primary-button").click();
}

async function openAdvancedSettings(page: Page) {
  const details = page.locator(".screening-advanced-settings");
  if (!(await details.evaluate((element) => element.hasAttribute("open")))) {
    await details.locator("> summary").click();
  }
  await expect.poll(() => details.evaluate((element) => element.hasAttribute("open"))).toBe(true);
}

test("historical records and batch members retain their evidence when promoted into candidate review", async ({ page, request }) => {
  const historicalProject = await createProject(
    request,
    "concrete-strength-v1",
    `historical candidate handoff ${Date.now()}`,
  );
  await page.goto(`/?view=candidates&project=${historicalProject.id}`);

  const historicalEvidence = page.locator(".similar-evidence-panel");
  const historicalAction = historicalEvidence.getByRole("button", { name: "この実測を候補にする" }).first();
  await expect(historicalAction).toBeEnabled();
  const historicalCreated = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && /\/historical-observations\/[^/]+\/candidate$/.test(new URL(response.url()).pathname)
  ));
  await historicalAction.click();
  const historicalResponse = await historicalCreated;
  expect(historicalResponse.status(), await historicalResponse.text()).toBe(201);
  const historicalCandidate = await historicalResponse.json() as { candidate: { id: string } };

  await page.goto(`/?view=candidates&project=${historicalProject.id}&candidate=${historicalCandidate.candidate.id}`);
  await page.reload();
  const historicalOrigin = page.locator(".candidate-origin.reference-data");
  await expect(historicalOrigin).toContainText("過去の実測値（actual）");
  await expect(historicalOrigin).toContainText("実測record");
  await expect(historicalOrigin).toContainText("Dataset Revision");
  await expect(historicalOrigin).toContainText("現在の予測値・区間・支持範囲は候補結果として別に表示しています");
  await expect(historicalOrigin.locator("code")).toHaveText(/[a-f0-9]{64}/);

  const batchProject = await createProject(
    request,
    "mpea-hardness-process-v1",
    `batch candidate handoff ${Date.now()}`,
  );
  await page.goto(`/?view=explore&project=${batchProject.id}`);
  await page.locator(".screening-mode-options").getByRole("button", { name: /有望候補を探す/ }).click();
  await openAdvancedSettings(page);
  await page.getByLabel("候補の提案方法").selectOption("bounded_simplex_goal_v1");
  await page.getByLabel(/主目標: .*の下限/).fill("300");
  const rows = page.locator(".variable-table tbody tr");
  await rows.nth(0).getByRole("combobox").first().selectOption("composition.Ni");
  await rows.nth(0).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(0).locator("input").nth(0).fill("20");
  await rows.nth(0).locator("input").nth(1).fill("50");
  await rows.nth(1).getByRole("combobox").first().selectOption("composition.Co");
  await rows.nth(1).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(1).locator("input").nth(0).fill("20");
  await rows.nth(1).locator("input").nth(1).fill("50");
  await runScreening(page);

  await page.locator(".screening-mode-options").getByRole("button", { name: /実験バッチを組む/ }).click();
  await page.getByLabel("バッチ件数").fill("5");
  await openAdvancedSettings(page);
  const batchSettings = page.getByRole("region", { name: "バッチの詳細設定" });
  await expect(batchSettings).toBeVisible();
  await batchSettings.getByLabel("Control条件").selectOption({ label: "候補化の基準条件" });
  await batchSettings.getByLabel("Control反復数").fill("2");
  const batchCreated = page.waitForResponse((response) => (
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening"
  ));
  await runScreening(page);
  expect((await batchCreated).status()).toBe(201);

  const batchSurface = page.getByRole("region", { name: "実験バッチ", exact: true });
  await expect(batchSurface).toBeVisible();
  const controlRows = batchSurface.locator("tbody tr").filter({ hasText: "固定Control" });
  expect(await controlRows.count()).toBeGreaterThan(0);
  await expect(controlRows.getByRole("button", { name: "この条件を候補にする" })).toHaveCount(0);
  const replicateRows = controlRows.filter({ hasText: "反復" });
  expect(await replicateRows.count()).toBeGreaterThan(0);
  await expect(replicateRows.getByRole("button", { name: "この条件を候補にする" })).toHaveCount(0);

  const memberActions = batchSurface.getByRole("button", { name: "この条件を候補にする" });
  expect(await memberActions.count()).toBeGreaterThanOrEqual(3);
  const promotedIds: string[] = [];
  for (let index = 0; index < 3; index += 1) {
    const promoted = page.waitForResponse((response) => (
      response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/candidates")
    ));
    await memberActions.first().click();
    const response = await promoted;
    expect(response.status(), await response.text()).toBe(201);
    const body = await response.json() as { candidates: Array<{ id: string }> };
    expect(body.candidates).toHaveLength(1);
    promotedIds.push(body.candidates[0].id);
  }

  const candidatesResponse = await request.get(`${api}/api/projects/${batchProject.id}/candidates`);
  expect(candidatesResponse.status()).toBe(200);
  const candidates = await candidatesResponse.json() as Array<{
    id: string;
    provenance?: { source_kind: string; source_ref: { batch_member_role?: string | null; source_run_id?: string | null } };
  }>;
  const promoted = candidates.filter((candidate) => promotedIds.includes(candidate.id));
  expect(promoted).toHaveLength(3);
  expect(promoted.every((candidate) => (
    candidate.provenance?.source_kind === "screening"
    && candidate.provenance.source_ref.batch_member_role
    && candidate.provenance.source_ref.source_run_id
  ))).toBe(true);

  await page.goto(`/?view=candidate-review&project=${batchProject.id}&candidate=${promotedIds[0]}`);
  await expect(page.locator(".candidate-origin")).toContainText("実験バッチ");
  await expect(page.locator(".candidate-origin")).toContainText("枠");
  await expect(page.locator(".decision-activity-panel")).toBeVisible();
});
