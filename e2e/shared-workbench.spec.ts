import { expect, test } from "@playwright/test";

const tasks = [
  { projectId: "default", outputLabels: ["引張強さ", "降伏強さ", "全伸び", "穴広げ率 λ"], hasHeatPattern: true, responseCurve: true },
  { projectId: "hot-rolling-default", outputLabels: ["引張強さ"], hasHeatPattern: false, responseCurve: true },
] as const;

for (const task of tasks) {
  test(`${task.projectId} uses the common candidate, prediction, and snapshot flow`, async ({ page }) => {
    const pageErrors: string[] = [];
    let curveRequests = 0;
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/response-curve")) curveRequests += 1;
    });

    await page.goto(`/?view=candidates&project=${task.projectId}`);
    await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
    await expect(page.locator(".comparison-prediction-table")).toBeVisible();
    const outputHeader = page.locator(".comparison-detail-table thead");
    await expect(outputHeader.locator(".prediction-col")).toHaveCount(task.outputLabels.length);
    for (const output of task.outputLabels) await expect(outputHeader.locator(".prediction-col").filter({ hasText: output })).toBeVisible();
    await expect(outputHeader.locator(".prediction-col small").first()).not.toHaveText("");
    const firstPredictionCell = page.locator(".comparison-prediction-table tbody .prediction-cell").first();
    await expect(firstPredictionCell).toContainText(/\d/);
    await expect(firstPredictionCell).not.toContainText(/MPa|%|µm/);
    await expect(firstPredictionCell.locator(".metric-value")).toHaveAttribute("aria-label", /\d+.*(?:MPa|%|µm)/);
    if (task.projectId === "default") {
      expect(await firstPredictionCell.evaluate((cell) => cell.getBoundingClientRect().width)).toBeLessThanOrEqual(90);
    }
    if (task.projectId === "hot-rolling-default") {
      await expect(outputHeader.getByText("降伏強さ", { exact: false })).toHaveCount(0);
      await expect(page.locator(".heat-panel")).toHaveCount(0);
      const responseVariable = page.getByRole("combobox", { name: "応答曲線の設計変数" });
      await expect(responseVariable.locator("option").first()).toHaveText("C (%)");
      await expect(responseVariable.locator("option")).toContainText(["C (%)", "Si (%)", "Mn (%)"]);
      const responseOptions = await responseVariable.locator("option").allTextContents();
      expect(responseOptions).toContain("均熱温度 (°C)");
      expect(responseOptions).toContain("仕上げ温度 (°C)");
      await expect(page.locator(".response-curve-card")).toHaveCount(1);
      await expect.poll(() => curveRequests).toBeGreaterThan(0);
      await expect(page.locator(".response-curves-panel .inference-surface-status")).toHaveText("最新");
      await expect(page.getByRole("img", { name: "引張強さの応答曲線" })).toBeVisible();
      await page.locator(".response-curve-card .svg-chart-hit-target").first().hover({ force: true });
      const responseTooltip = page.locator(".response-curve-card .svg-chart-tooltip");
      await expect(responseTooltip).toContainText("90%区間");
      const wideTooltipHeight = (await responseTooltip.boundingBox())?.height ?? 0;
      expect(wideTooltipHeight).toBeGreaterThanOrEqual(65);
      expect(wideTooltipHeight).toBeLessThanOrEqual(67);
      await page.setViewportSize({ width: 920, height: 720 });
      await page.locator(".response-curve-card .svg-chart-hit-target").first().hover({ force: true });
      const narrowTooltipHeight = (await responseTooltip.boundingBox())?.height ?? 0;
      expect(Math.abs(narrowTooltipHeight - wideTooltipHeight)).toBeLessThan(1);
      await page.setViewportSize({ width: 1280, height: 720 });
    } else {
      await expect(page.locator(".heat-panel")).toBeVisible();
      await page.locator(".heat-chart circle[tabindex='0']").first().hover({ force: true });
      const heatTooltip = page.locator(".heat-chart .svg-chart-tooltip");
      await expect(heatTooltip).toContainText("温度");
      const heatTooltipHeight = (await heatTooltip.boundingBox())?.height ?? 0;
      expect(heatTooltipHeight).toBeGreaterThanOrEqual(65);
      expect(heatTooltipHeight).toBeLessThanOrEqual(67);
      const responseVariable = page.getByRole("combobox", { name: "応答曲線の設計変数" });
      await expect(responseVariable.locator("option").first()).toHaveText("C (%)");
      const responseOptions = await responseVariable.locator("option").allTextContents();
      expect(responseOptions).toContain("ラインスピード (mpm)");
      expect(responseOptions.some((label) => label.includes("点目") || label.includes("時間"))).toBe(false);
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
    await expect(selectedName).toHaveValue(editedName);

    const disposableResponse = page.waitForResponse((response) => response.request().method() === "POST" && /\/candidates$/.test(new URL(response.url()).pathname));
    await page.getByRole("button", { name: "候補を追加" }).click();
    const createdDisposableResponse = await disposableResponse;
    expect(createdDisposableResponse.status()).toBe(201);
    const disposableId = ((await createdDisposableResponse.json()) as { id: string }).id;
    await expect(page).toHaveURL(new RegExp(`candidate=${disposableId}`));
    const disposableName = await page.locator(".candidate-name-table tbody tr.selected-row input").inputValue();
    const deleteResponse = page.waitForResponse((response) => response.request().method() === "DELETE" && response.url().includes(`/candidates/${disposableId}`));
    await page.getByRole("button", { name: `${disposableName}を削除`, exact: true }).click();
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
    await expect(page.locator(".notice")).toContainText("詳細予測を保存しました");
    await expect(page.getByRole("button", { name: `${editedName}の詳細予測を保存済み` })).toBeDisabled();

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
  await expect(page.getByRole("alert")).toContainText("このタスクではプレビューを利用できません");
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
