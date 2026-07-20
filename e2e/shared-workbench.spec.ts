import { expect, test } from "@playwright/test";

const tasks = [
  { projectId: "default", outputLabels: ["引張強さ", "降伏強さ", "全伸び", "穴広げ率 λ"], hasHeatPattern: true, responseCurve: true },
  { projectId: "hot-rolling-default", outputLabels: ["引張強さ"], hasHeatPattern: false, responseCurve: false },
] as const;

for (const task of tasks) {
  test(`${task.projectId} uses the common candidate, prediction, snapshot, and actual flow`, async ({ page }) => {
    const pageErrors: string[] = [];
    let curveRequests = 0;
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/response-curve")) curveRequests += 1;
    });

    await page.goto(`/?view=candidates&project=${task.projectId}`);
    await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
    await expect(page.locator(".evidence-panel .metric-table")).toBeVisible();
    const outputHeader = page.locator(".comparison-detail-table thead");
    await expect(outputHeader.locator(".prediction-col")).toHaveCount(task.outputLabels.length);
    for (const output of task.outputLabels) await expect(outputHeader.getByText(output, { exact: false })).toBeVisible();
    if (task.projectId === "hot-rolling-default") {
      await expect(outputHeader.getByText("降伏強さ", { exact: false })).toHaveCount(0);
      await expect(page.locator(".heat-panel")).toHaveCount(0);
      await expect(page.getByLabel("応答曲線は利用できません")).toBeVisible();
      expect(curveRequests).toBe(0);
    } else {
      await expect(page.locator(".heat-panel")).toBeVisible();
    }

    const createResponse = page.waitForResponse((response) => response.request().method() === "POST" && /\/candidates$/.test(new URL(response.url()).pathname));
    await page.getByRole("button", { name: "候補を追加" }).click();
    const createdResponse = await createResponse;
    expect(createdResponse.status()).toBe(201);
    const keptCandidateId = ((await createdResponse.json()) as { id: string }).id;
    await expect(page).toHaveURL(new RegExp(`candidate=${keptCandidateId}`));

    const selectedName = page.locator(".candidate-name-table tbody tr.selected-row input");
    const editedName = `${task.projectId} 共通Workbench`;
    const updateResponse = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().endsWith(`/candidates/${keptCandidateId}`));
    await selectedName.fill(editedName);
    await page.locator(".table-heading h2").click();
    expect((await updateResponse).status()).toBe(200);
    await expect(page.getByRole("heading", { name: /予測特性/ })).toContainText(editedName);

    const disposableResponse = page.waitForResponse((response) => response.request().method() === "POST" && /\/candidates$/.test(new URL(response.url()).pathname));
    await page.getByRole("button", { name: "候補を追加" }).click();
    const createdDisposableResponse = await disposableResponse;
    expect(createdDisposableResponse.status()).toBe(201);
    const disposableId = ((await createdDisposableResponse.json()) as { id: string }).id;
    await expect(page).toHaveURL(new RegExp(`candidate=${disposableId}`));
    const deleteResponse = page.waitForResponse((response) => response.request().method() === "DELETE" && response.url().includes(`/candidates/${disposableId}`));
    await page.getByRole("button", { name: "削除", exact: true }).click();
    expect((await deleteResponse).status()).toBe(204);

    await page.getByRole("textbox", { name: `${editedName}の候補名` }).click();
    await expect(page).toHaveURL(new RegExp(`candidate=${keptCandidateId}`));
    const candidateResponse = await page.request.get(`http://127.0.0.1:8875/api/projects/${task.projectId}/candidates/${keptCandidateId}`);
    const currentCandidate = await candidateResponse.json() as { revision: number };
    const detailedResponsePromise = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith(`/candidates/${keptCandidateId}/predict`));
    await page.getByRole("button", { name: new RegExp(`${editedName}の詳細予測を保存`) }).click();
    const detailedResponse = await detailedResponsePromise;
    expect(detailedResponse.status()).toBe(200);
    const detailed = await detailedResponse.json() as { snapshot: { payload: { raw_candidate: { revision: number } } } };
    expect(detailed.snapshot.payload.raw_candidate.revision).toBe(currentCandidate.revision);
    await expect(page.locator(".notice")).toContainText("詳細予測を実行");

    await page.getByRole("spinbutton", { name: "実測平均" }).fill("510");
    const actualResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith(`/candidates/${keptCandidateId}/actuals`));
    await page.getByRole("button", { name: "実測を追加" }).click();
    expect((await actualResponse).status()).toBe(201);
    await expect(page.locator(".actual-table tbody")).toContainText("510");

    await page.getByRole("button", { name: "プロジェクト概要", exact: true }).click();
    await expect(page.getByRole("heading", { name: "候補と判断履歴" })).toBeVisible();
    await expect(page.getByRole("button", { name: "詳細" }).first()).toBeVisible();
    if (!task.responseCurve) expect(curveRequests).toBe(0);
    expect(pageErrors).toEqual([]);
  });
}

test("preview capability disables initial and edited-candidate requests", async ({ page }) => {
  let previewRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/preview")) previewRequests += 1;
  });
  await page.route("**/task-definition", async (route) => {
    const response = await route.fetch();
    const body = await response.json() as { runtime_capability: { operations: { preview: boolean } } };
    body.runtime_capability.operations.preview = false;
    await route.fulfill({ response, json: body });
  });

  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect(page.locator(".evidence-panel .panel-error")).toContainText("このタスクではプレビューを利用できません");
  await page.waitForTimeout(600);
  expect(previewRequests).toBe(0);

  const numeric = page.locator(".comparison-detail-table tbody tr.selected-row input[type=number]").first();
  const current = Number(await numeric.inputValue());
  const save = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await numeric.fill(String(current + 0.001));
  await page.locator(".table-heading h2").click();
  expect((await save).status()).toBe(200);
  await page.waitForTimeout(600);
  expect(previewRequests).toBe(0);
});
