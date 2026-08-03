import { expect, test } from "@playwright/test";
import { openCandidateInputs } from "./helpers";

const tasks = [
  { projectId: "default", outputLabels: ["引張強さ", "降伏強さ", "全伸び", "穴広げ率 λ"], hasHeatPattern: true, responseCurve: true },
  { projectId: "hot-rolling-default", outputLabels: ["引張強さ"], hasHeatPattern: false, responseCurve: true },
] as const;
const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 8875);

test("Bayesian additive response curves distinguish mean and observation uncertainty", async ({ page }) => {
  await page.route("**/response-curve?**", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    if (!Array.isArray(payload.points)) {
      await route.fulfill({ response, json: payload });
      return;
    }
    payload.points = payload.points.map((point: {
      value: number;
      lower: number;
      upper: number;
      [key: string]: unknown;
    }) => ({
      ...point,
      interval_method: "bayesian",
      interval_coverage_level: 0.9,
      latent_mean_credible_interval: {
        method: "bayesian",
        estimand: "latent_mean",
        coverage_level: 0.9,
        lower: point.value - (point.value - point.lower) * 0.45,
        upper: point.value + (point.upper - point.value) * 0.45,
      },
    }));
    await route.fulfill({ response, json: payload });
  });

  await page.goto("/?view=candidates&project=hot-rolling-default");
  await expect(
    page.getByText(
      "濃い帯: 平均の信用区間 · 薄い帯: 新しい観測の予測区間",
      { exact: true },
    ),
  ).toBeVisible();
  const chart = page.getByRole("group", { name: /引張強さの応答曲線/ });
  await expect(
    chart.locator('[data-uncertainty-band="latent-mean"]').first(),
  ).toBeVisible();
  await expect(
    chart.locator('[data-uncertainty-band="posterior-predictive"]').first(),
  ).toBeVisible();
});

test("engineered material features are inspectable for annealing and hot rolling", async ({ page }, testInfo) => {
  await page.goto("/?view=candidates&project=default");
  const annealing = page.locator(".feature-engineering-panel");
  await expect(annealing.getByText("内部で作った特徴量")).toBeVisible();
  await expect(annealing).not.toHaveAttribute("open", "");
  await expect(page.locator(".central-workspace > :last-child")).toHaveAttribute(
    "data-workbench-surface",
    "feature_engineering",
  );
  await annealing.locator("summary").click();
  await expect(annealing.getByText("Ac1 目安", { exact: true })).toBeVisible();
  await expect(annealing.getByText("最高温度−Ac3目安", { exact: true })).toBeVisible();

  await page.goto("/?view=candidates&project=hot-rolling-default");
  const hotRolling = page.locator(".feature-engineering-panel");
  await expect(hotRolling.getByText("内部で作った特徴量")).toBeVisible();
  await expect(hotRolling).not.toHaveAttribute("open", "");
  await expect(page.locator(".central-workspace > :last-child")).toHaveAttribute(
    "data-workbench-surface",
    "feature_engineering",
  );
  await hotRolling.locator("summary").click();
  await expect(hotRolling.getByText("総圧下率", { exact: true })).toBeVisible();
  await expect(hotRolling.getByText("仕上温度−Ar3目安", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("feature-engineering-hot-rolling.png"), fullPage: true });
});

test("Task-declared prediction contour loads on demand and keeps support separate", async ({ page }, testInfo) => {
  let contourRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/response-contour")) {
      contourRequests += 1;
    }
  });
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("tab", { name: "応答曲線" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "予測地図" })).toHaveAttribute("aria-selected", "false");
  expect(contourRequests).toBe(0);

  await page.getByRole("tab", { name: "予測地図" }).click();
  await expect(page.getByRole("heading", { name: "2変数の予測地図" })).toBeVisible();
  expect(contourRequests).toBe(0);
  const responsePromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith("/response-contour"),
  );
  await page.getByRole("button", { name: "地図を表示" }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  expect((await response.json()).grid_shape).toEqual([11, 11]);
  await expect(page.getByRole("img", { name: /予測地図/ })).toBeVisible();
  const axisGroup = page.getByRole("group", { name: "表示軸" });
  await expect(axisGroup.getByRole("combobox", { name: "横軸" })).toBeVisible();
  await expect(axisGroup.getByRole("combobox", { name: "縦軸" })).toBeVisible();
  await expect(axisGroup.getByRole("button", { name: "横軸と縦軸を入れ替える" })).toBeVisible();
  expect(await axisGroup.evaluate((element) => element.getBoundingClientRect().width)).toBeLessThan(500);
  await expect(page.getByText(/既存実績から遠い（予測非表示）/)).toBeVisible();
  await expect(page.locator(".response-contour-header p")).toContainText(
    "固定した他の入力まで含めると既存実績から遠い",
  );
  const hiddenCellTitles = await page
    .locator('.response-contour-panel rect[fill="url(#contour-extrapolated)"] title')
    .allTextContents();
  expect(hiddenCellTitles.length).toBeGreaterThan(0);
  expect(hiddenCellTitles.every((title) => title.includes("既存実績から遠い"))).toBeTruthy();
  await expect(
    page.locator(".response-contour-panel").getByText("数値で確認", { exact: true }),
  ).toBeVisible();
  expect(contourRequests).toBe(1);

  const changedAxisResponse = page.waitForResponse((candidateResponse) => {
    const url = new URL(candidateResponse.url());
    return url.pathname.endsWith("/response-contour")
      && url.searchParams.get("x_variable") === "process.ls_mpm";
  });
  await page.getByRole("combobox", { name: "横軸" }).selectOption("process.ls_mpm");
  await expect(page.getByRole("img", { name: /予測地図/ })).toBeHidden();
  expect((await changedAxisResponse).status()).toBe(200);
  await expect(page.getByRole("img", { name: /LS.*Mn.*予測地図/ })).toBeVisible();
  expect(contourRequests).toBe(2);

  const xAxis = axisGroup.getByRole("combobox", { name: "横軸" });
  const yAxis = axisGroup.getByRole("combobox", { name: "縦軸" });
  const xBeforeSwap = await xAxis.inputValue();
  const yBeforeSwap = await yAxis.inputValue();
  const swappedAxesResponse = page.waitForResponse((candidateResponse) => {
    const url = new URL(candidateResponse.url());
    return url.pathname.endsWith("/response-contour")
      && url.searchParams.get("x_variable") === yBeforeSwap
      && url.searchParams.get("y_variable") === xBeforeSwap;
  });
  await axisGroup.getByRole("button", { name: "横軸と縦軸を入れ替える" }).click();
  await expect(xAxis).toHaveValue(yBeforeSwap);
  await expect(yAxis).toHaveValue(xBeforeSwap);
  expect((await swappedAxesResponse).status()).toBe(200);
  expect(contourRequests).toBe(3);

  await page.getByRole("tab", { name: "応答曲線" }).click();
  await page.getByRole("tab", { name: "予測地図" }).click();
  await expect(page.getByRole("img", { name: /予測地図/ })).toBeVisible();
  expect(contourRequests).toBe(3);

  await page.setViewportSize({ width: 720, height: 900 });
  await expect(page.getByRole("combobox", { name: "横軸" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(720);
  await page.locator(".response-contour-panel").screenshot({
    path: testInfo.outputPath("response-contour.png"),
  });
  await page.setViewportSize({ width: 375, height: 900 });
  await expect.poll(() => axisGroup.evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  )).toBeLessThanOrEqual(0);
  await page.locator(".response-contour-panel").screenshot({
    path: testInfo.outputPath("response-contour-narrow.png"),
  });
});

test("Task-declared input space loads on demand and keeps evidence in context", async ({ page }, testInfo) => {
  let inputSpaceRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/model-package/input-space")) {
      inputSpaceRequests += 1;
    }
  });

  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("tab", { name: "入力空間" })).toHaveAttribute(
    "aria-selected",
    "false",
  );
  expect(inputSpaceRequests).toBe(0);

  const responsePromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith("/model-package/input-space"),
  );
  await page.getByRole("tab", { name: "入力空間" }).click();
  expect((await responsePromise).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "学習データの中で見る" })).toBeVisible();
  await expect(page.locator(".input-space-reading dt", {
    hasText: "島までの距離",
  })).toBeVisible();
  await expect(page.locator(".input-space-reading dt", {
    hasText: "候補間の新規性",
  })).toBeVisible();
  expect(inputSpaceRequests).toBe(1);

  const conditions = page.locator(".input-space-technical");
  await conditions.locator("summary").click();
  await expect(conditions.getByText(/Landmark MDS/)).toBeVisible();
  await expect(conditions.getByText("seed / landmark", { exact: true })).toBeVisible();
  await expect(conditions.getByText(/^508 \/ \d+$/)).toBeVisible();
  await expect(conditions.getByText("入力空間identity", { exact: true })).toBeVisible();
  await expect(conditions.getByText(/^sha256:[0-9a-f]+…$/)).toHaveCount(2);

  await page.getByRole("button", {
    name: "ME-01::AN-02の過去実績を開く",
  }).click();
  const evidenceDrawer = page.getByRole("complementary", { name: "過去実績の根拠" });
  await expect(evidenceDrawer.getByRole("heading", { name: "実測特性" })).toBeVisible();
  await expect(evidenceDrawer.getByText("510.0MPa", { exact: true })).toBeVisible();
  await evidenceDrawer.getByRole("button", { name: "過去実績の根拠を閉じる" }).click();

  const candidatePoint = page.locator(".input-space-candidate").nth(1);
  const candidateLabel = (await candidatePoint.getAttribute("aria-label"))!.replace(/を選択、.*$/, "");
  await candidatePoint.focus();
  await candidatePoint.press("Enter");
  await expect(page.locator(".input-space-reading").getByRole("heading", {
    name: candidateLabel,
  })).toBeVisible();
  await expect(page).toHaveURL(/candidate=/);

  await page.setViewportSize({ width: 720, height: 900 });
  await expect(page.getByRole("heading", { name: "学習データの中で見る" })).toBeVisible();
  expect(await page.locator(".input-space-panel").evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  )).toBeLessThanOrEqual(1);
  await page.locator(".input-space-panel").screenshot({
    path: testInfo.outputPath("input-space.png"),
  });

  await page.setViewportSize({ width: 375, height: 900 });
  expect(await page.locator(".input-space-panel").evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  )).toBeLessThanOrEqual(1);
  await expect(page.getByText("図は横にスクロールできます")).toBeVisible();
  expect(await page.locator(".input-space-chart").evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  )).toBeGreaterThan(200);
  await page.locator(".input-space-panel").screenshot({
    path: testInfo.outputPath("input-space-narrow.png"),
  });
});

test("Task-declared prediction space compares candidates with paired training actuals", async ({ page }, testInfo) => {
  let evidenceRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/output-space-evidence")) evidenceRequests += 1;
  });
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("tab", { name: "特性バランス" })).toBeVisible();
  expect(evidenceRequests).toBe(0);

  const evidenceResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith("/output-space-evidence"),
  );
  await page.getByRole("tab", { name: "特性バランス" }).click();
  const response = await evidenceResponse;
  expect(response.status()).toBe(200);
  const evidence = await response.json() as {
    total_contexts: number;
    returned_contexts: number;
    eligible_contexts: number;
    distance_method: string;
    distance_version: string;
    cohort_digest: string;
    points: unknown[];
  };
  expect(evidence.total_contexts).toBeGreaterThan(0);
  expect(evidence.points.length).toBe(evidence.returned_contexts);
  expect(evidence.returned_contexts).toBeLessThanOrEqual(evidence.eligible_contexts);
  expect(evidence.distance_method).not.toBe("");
  expect(evidence.distance_version).toBe("1.0.0");
  expect(evidence.cohort_digest).toMatch(/^sha256:/);

  const panel = page.locator(".prediction-space-panel");
  await expect(panel.getByRole("heading", { name: "特性のトレードオフ" })).toBeVisible();
  await expect(panel.getByRole("group", { name: /引張強さと降伏強さ/ })).toBeVisible();
  await expect(panel.locator(".prediction-space-actual")).toHaveCount(evidence.returned_contexts);
  const candidateCount = await panel.locator(".prediction-space-candidate").count();
  expect(candidateCount).toBeGreaterThanOrEqual(3);
  await expect(panel.locator(".prediction-space-interval")).toHaveCount(candidateCount * 2);
  await expect(panel.locator("svg [role=button]")).toHaveCount(evidence.returned_contexts);
  await expect(panel).toContainText("2特性を同時に含む確率領域ではありません");
  await expect(panel).toContainText("予測値や同一試料の相関ではありません");
  await expect(panel).toContainText("2軸共通cohort");

  const actualXPositions = await panel.locator(".prediction-space-actual").evaluateAll(
    (elements) => elements.map((element) => Number(element.getAttribute("x"))),
  );
  if (actualXPositions.length > 1) {
    expect(Math.max(...actualXPositions) - Math.min(...actualXPositions)).toBeGreaterThan(20);
  }

  const xAxis = panel.getByRole("combobox", { name: "横軸" });
  const yAxis = panel.getByRole("combobox", { name: "縦軸" });
  await expect(xAxis).toHaveValue("TS");
  await expect(yAxis).toHaveValue("YS");
  const swappedResponse = page.waitForResponse((candidateResponse) => {
    const url = new URL(candidateResponse.url());
    return url.pathname.endsWith("/output-space-evidence")
      && url.searchParams.get("x_target") === "YS"
      && url.searchParams.get("y_target") === "TS";
  });
  await panel.getByRole("button", { name: "横軸と縦軸を入れ替え" }).click();
  expect((await swappedResponse).status()).toBe(200);
  await expect(xAxis).toHaveValue("YS");
  await expect(yAxis).toHaveValue("TS");
  expect(evidenceRequests).toBe(2);

  const filteredResponse = page.waitForResponse((candidateResponse) => {
    const url = new URL(candidateResponse.url());
    return url.pathname.endsWith("/output-space-evidence")
      && url.searchParams.get("distance_filter") === "caution";
  });
  await panel.getByRole("combobox", { name: "実績の範囲" }).selectOption("caution");
  expect((await filteredResponse).status()).toBe(200);
  expect(evidenceRequests).toBe(3);

  await panel.getByText("数値で確認", { exact: true }).click();
  const numericalTable = panel.getByRole("table");
  await expect(numericalTable).toBeVisible();
  await expect(numericalTable.getByText("学習実績", { exact: true })).toHaveCount(
    await panel.locator(".prediction-space-actual").count(),
  );
  await expect(numericalTable).toContainText("ME-");
  await expect(numericalTable).toContainText("同じ実測行");
  await expect(numericalTable).toContainText("実測ばらつき");
  const candidateButtons = numericalTable.locator(".prediction-space-row-select");
  await candidateButtons.nth(1).focus();
  await candidateButtons.nth(1).press("Enter");
  await expect(candidateButtons.nth(1)).toHaveAttribute("aria-pressed", "true");
  await panel.locator("svg [role=button]").first().focus();
  await panel.locator("svg [role=button]").first().press("Enter");
  const evidenceDrawer = page.getByRole("complementary", { name: "過去実績の根拠" });
  await expect(evidenceDrawer).toBeVisible();
  await expect(candidateButtons.nth(1)).toHaveAttribute("aria-pressed", "true");
  await evidenceDrawer.screenshot({
    path: testInfo.outputPath("prediction-space-historical-drawer.png"),
  });
  await page.getByRole("button", { name: "過去実績の根拠を閉じる" }).click();
  await page.setViewportSize({ width: 720, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(720);
  await panel.screenshot({ path: testInfo.outputPath("prediction-space-narrow.png") });
});

for (const task of tasks) {
  test(`${task.projectId} uses the common candidate, prediction, and snapshot flow`, async ({ page }) => {
    const pageErrors: string[] = [];
    let curveRequests = 0;
    const curveRequestUrls: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/response-curve")) {
        curveRequests += 1;
        curveRequestUrls.push(request.url());
      }
    });

    await page.goto(`/?view=candidates&project=${task.projectId}`);
    await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
    await openCandidateInputs(page);
    await expect(page.locator(".comparison-prediction-table")).toBeVisible();
    const outputHeader = page.locator(".comparison-detail-table thead");
    await expect(outputHeader.locator(".decision-output-col")).toHaveCount(task.outputLabels.length);
    for (const output of task.outputLabels) await expect(outputHeader.locator(".decision-output-col").filter({ hasText: output })).toBeVisible();
    await expect(outputHeader.locator(".decision-output-col small").first()).not.toHaveText("");
    const firstPredictionCell = page.locator(".comparison-prediction-table tbody .decision-output-cell").first();
    // The cell shows a bare number; the unit stays reachable on hover instead of
    // widening every column.
    const firstPredictionValue = firstPredictionCell.locator(".decision-prediction");
    await expect(firstPredictionValue).toContainText(/\d/);
    await expect(firstPredictionValue).not.toContainText(/MPa|%|µm/);
    await expect(firstPredictionValue).toHaveAttribute("title", /\d+.*(?:MPa|%|µm)/);
    if (task.projectId === "default") {
      const exported = page.waitForResponse((response) => response.url().endsWith("/candidates/export.xlsx"));
      await page.getByRole("button", { name: "候補・予測をXLSX出力" }).click();
      expect((await exported).status()).toBe(200);
    }
    expect((await page.locator(".comparison-action-scroll").boundingBox())?.width).toBeLessThanOrEqual(120);
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
      await expect(page.getByRole("group", { name: /引張強さの応答曲線/ })).toBeVisible();
      const yAxisLabels = page.locator(".response-curve-card svg text").filter({ hasNotText: "C (%)" }).first();
      await page.getByRole("combobox", { name: "Y軸の表示範囲" }).selectOption("preferred");
      const preferredTopTick = await yAxisLabels.textContent();
      const requestsBeforeDisplayRangeToggle = curveRequests;
      await page.getByRole("combobox", { name: "Y軸の表示範囲" }).selectOption("full");
      await expect(page.getByRole("combobox", { name: "Y軸の表示範囲" })).toHaveValue("full");
      await page.waitForTimeout(500);
      expect(curveRequests).toBe(requestsBeforeDisplayRangeToggle);
      await expect(yAxisLabels).not.toHaveText(preferredTopTick ?? "");
      const axisSettingsButton = page.getByRole("button", { name: "軸範囲を設定" });
      await axisSettingsButton.click();
      const axisSettings = page.locator(".response-curve-axis-settings");
      const curveGrid = page.locator(".response-curves-grid");
      await expect(axisSettings).toBeVisible();
      const settingsBox = await axisSettings.boundingBox();
      const curveGridBox = await curveGrid.boundingBox();
      const viewport = page.viewportSize();
      // The panel opens beside the controls as a popover. It must stay reachable
      // inside the viewport and start to the right of the chart it adjusts.
      expect(settingsBox && viewport && settingsBox.x + settingsBox.width).toBeLessThanOrEqual(viewport?.width ?? 0);
      expect(settingsBox?.x ?? 0).toBeGreaterThan(curveGridBox?.x ?? 0);
      await page.locator(".axis-settings-close").click();
      await expect(axisSettings).toHaveCount(0);
      await expect(axisSettingsButton).toBeFocused();
      await page.locator(".response-curve-card .svg-chart-hit-target").first().hover({ force: true });
      const responseTooltip = page.locator(".response-curve-card .svg-chart-tooltip");
      await expect(responseTooltip).toContainText("予測区間");
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
      await expect(page.locator(".response-curves-panel .inference-surface-status")).toHaveText("最新");
      await expect(page.locator(".curve-extrapolation-summary")).toHaveCount(task.outputLabels.length);
      const carbonCurveRequest = curveRequestUrls.map((url) => new URL(url)).find((url) => (
        url.searchParams.get("target") === "TS"
        && url.searchParams.get("variable") === "composition.C"
      ));
      expect(carbonCurveRequest?.searchParams.get("range_min")).toBe("0");
      expect(carbonCurveRequest?.searchParams.get("range_max")).toBe("0.2");
      const firstCurve = page.getByRole("group", { name: /引張強さの応答曲線/ });
      await expect(firstCurve.getByRole("img", { name: /C 0\.000/ }).first()).toBeAttached();
      await expect(firstCurve.getByRole("img", { name: /C 0\.200/ }).first()).toBeAttached();
    }

    const createResponse = page.waitForResponse((response) => response.request().method() === "POST" && /\/candidates$/.test(new URL(response.url()).pathname));
    await page.getByRole("button", { name: "候補を追加" }).click();
    const createdResponse = await createResponse;
    expect(createdResponse.status()).toBe(201);
    const keptCandidateId = ((await createdResponse.json()) as { id: string }).id;
    await expect(page).toHaveURL(new RegExp(`candidate=${keptCandidateId}`));

    const selectedName = page.locator(".candidate-name-table tbody tr.selected-row").getByRole("textbox");
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
    const disposableName = await page.locator(".candidate-name-table tbody tr.selected-row").getByRole("textbox").inputValue();
    await page.getByRole("button", { name: `${disposableName}を一覧から外す`, exact: true }).click();
    await expect(page.getByRole("group", { name: `${disposableName}を一覧から外す確認` })).toContainText("後でプロジェクト概要から復元できます");
    await page.getByRole("button", { name: "キャンセル", exact: true }).click();
    await expect(page.getByRole("textbox", { name: `${disposableName}の候補名` })).toBeVisible();

    const deleteResponse = page.waitForResponse((response) => response.request().method() === "DELETE" && response.url().includes(`/candidates/${disposableId}`));
    await page.getByRole("button", { name: `${disposableName}を一覧から外す`, exact: true }).click();
    await page.getByRole("button", { name: "一覧から外す", exact: true }).click();
    expect((await deleteResponse).status()).toBe(204);

    await page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "概要", exact: true }).click();
    const restoreResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith(`/candidates/${disposableId}/restore`));
    const archivedCandidates = page.locator(".archived-candidate-history");
    await expect(archivedCandidates.locator("summary")).toContainText(
      "一覧から外した候補",
    );
    await expect(archivedCandidates).not.toHaveAttribute("open", "");
    await archivedCandidates.locator("summary").click();
    const archivedCandidate = archivedCandidates.getByRole("article").filter({ hasText: disposableName });
    await expect(archivedCandidate).toContainText("固定結果 0件");
    await archivedCandidate.getByRole("button", { name: "候補へ戻す" }).click();
    expect((await restoreResponse).status()).toBe(200);
    await page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "候補比較", exact: true }).click();
    await expect(page.getByRole("textbox", { name: `${disposableName}の候補名` })).toBeVisible();

    await page.getByRole("textbox", { name: `${editedName}の候補名` }).click();
    await expect(page).toHaveURL(new RegExp(`candidate=${keptCandidateId}`));
    const candidateResponse = await page.request.get(`http://127.0.0.1:${apiPort}/api/projects/${task.projectId}/candidates/${keptCandidateId}`);
    const currentCandidate = await candidateResponse.json() as { revision: number };
    const detailedResponsePromise = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith(`/candidates/${keptCandidateId}/predict`));
    await page.getByRole("button", { name: new RegExp(`${editedName}の詳細予測を保存`) }).click();
    const detailedResponse = await detailedResponsePromise;
    expect(detailedResponse.status()).toBe(200);
    const detailed = await detailedResponse.json() as { snapshot: { payload: { raw_candidate: { revision: number } } } };
    expect(detailed.snapshot.payload.raw_candidate.revision).toBe(currentCandidate.revision);
    await expect(page.locator(".workspace-notice")).toContainText("詳細予測を保存しました");
    await expect(page.getByRole("button", { name: `${editedName}の詳細予測を保存済み` })).toBeDisabled();

    await page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "概要", exact: true }).click();
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
  await openCandidateInputs(page);
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
