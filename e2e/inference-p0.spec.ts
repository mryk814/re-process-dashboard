import { expect, test } from "@playwright/test";

test("inference runs only for changed candidates and visible selected curves", async ({ page }) => {
  let previewRequests = 0;
  let curveRequests = 0;
  const inferenceResponses: Array<{ kind: "preview" | "curve"; candidateId: string; status: number; body: unknown }> = [];
  const failedInferenceRequests: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/preview")) previewRequests += 1;
    if (path.endsWith("/response-curves")) curveRequests += 1;
  });
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/preview") || path.endsWith("/response-curves")) failedInferenceRequests.push(path);
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    const kind = path.endsWith("/preview") ? "preview" : path.endsWith("/response-curves") ? "curve" : null;
    if (!kind) return;
    const candidateId = path.split("/").at(-2) ?? "";
    void response.json().catch(() => null).then((body) => {
      inferenceResponses.push({ kind, candidateId, status: response.status(), body });
    });
  });

  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect.poll(() => previewRequests).toBe(3);
  await expect.poll(() => inferenceResponses.filter((item) => item.kind === "preview").length).toBe(3);
  const initialPreviews = inferenceResponses.filter((item) => item.kind === "preview");
  expect(new Set(initialPreviews.map((item) => item.candidateId)).size).toBe(3);
  for (const response of initialPreviews) {
    expect(response.status).toBe(200);
    expect(response.body).toEqual(expect.objectContaining({ canonical_input: expect.any(Object), predictions: expect.any(Object) }));
  }
  expect(curveRequests).toBe(0);

  const candidateRows = page.locator(".candidate-name-table tbody tr");
  await candidateRows.nth(1).click();
  await expect(candidateRows.nth(1)).toHaveClass(/selected-row/);
  await page.waitForTimeout(550);
  expect(previewRequests).toBe(3);
  const selectedCandidateId = new URL(page.url()).searchParams.get("candidate");
  expect(selectedCandidateId).toBeTruthy();

  const nameInput = candidateRows.nth(1).getByRole("textbox");
  const saveName = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await nameInput.fill(`${await nameInput.inputValue()} 表示名`);
  await page.locator(".table-heading h2").click();
  const savedNameResponse = await saveName;
  expect(new URL(savedNameResponse.url()).pathname.split("/").at(-1)).toBe(selectedCandidateId);
  const selectedCandidateLabel = await nameInput.inputValue();
  expect(new URL(page.url()).searchParams.get("candidate")).toBe(selectedCandidateId);
  await expect(candidateRows.nth(1)).toHaveClass(/selected-row/);
  await expect(page.getByRole("heading", { name: /予測特性/ })).toContainText(selectedCandidateLabel);
  await page.waitForTimeout(450);
  await expect(candidateRows.nth(1)).toHaveClass(/selected-row/);
  await expect(page.getByRole("heading", { name: /予測特性/ })).toContainText(selectedCandidateLabel);
  expect(previewRequests).toBe(3);
  expect(curveRequests).toBe(0);

  await page.getByRole("button", { name: "選択候補の応答曲線を表示" }).click();
  await expect(page.locator(".response-curves-panel")).toHaveAttribute("data-candidate-id", selectedCandidateId!);
  await expect.poll(() => curveRequests).toBe(1);
  await expect.poll(() => inferenceResponses.filter((item) => item.kind === "curve").length).toBe(1);
  const firstCurve = inferenceResponses.find((item) => item.kind === "curve");
  expect(firstCurve).toEqual(expect.objectContaining({ candidateId: selectedCandidateId, status: 200 }));
  expect(firstCurve?.body).toEqual(expect.objectContaining({ curves: expect.any(Object), output_ranges: expect.any(Object) }));
  await expect(page.locator(".curve-scope")).toContainText(selectedCandidateLabel);
  await expect(page.getByRole("heading", { name: /予測特性/ })).toContainText(selectedCandidateLabel);

  let releasePreview = () => undefined;
  const previewGate = new Promise<void>((resolve) => { releasePreview = resolve; });
  let previewHeld = false;
  await page.route("**/preview", async (route) => {
    previewHeld = true;
    const response = await route.fetch();
    await previewGate;
    await route.fulfill({ response });
  });

  await page.getByRole("button", { name: "候補を追加" }).click();
  await expect.poll(() => previewHeld).toBe(true);
  await expect.poll(() => new URL(page.url()).searchParams.get("candidate")).not.toBe(selectedCandidateId);
  const createdCandidateId = new URL(page.url()).searchParams.get("candidate");
  expect(createdCandidateId).toBeTruthy();
  const createdCandidateLabel = await page.locator(".candidate-name-table tbody tr.selected-row input").inputValue();
  await page.waitForTimeout(500);
  expect(curveRequests).toBe(1);

  releasePreview();
  await expect.poll(() => previewRequests).toBe(4);
  await expect.poll(() => curveRequests).toBe(2);
  await expect.poll(() => inferenceResponses.some((item) => item.kind === "preview" && item.candidateId === createdCandidateId)).toBe(true);
  await expect.poll(() => inferenceResponses.some((item) => item.kind === "curve" && item.candidateId === createdCandidateId)).toBe(true);
  const createdPreview = inferenceResponses.find((item) => item.kind === "preview" && item.candidateId === createdCandidateId);
  const createdCurve = inferenceResponses.find((item) => item.kind === "curve" && item.candidateId === createdCandidateId);
  expect(createdPreview).toEqual(expect.objectContaining({ status: 200 }));
  expect(createdPreview?.body).toEqual(expect.objectContaining({ canonical_input: expect.any(Object), predictions: expect.any(Object) }));
  expect(createdCurve).toEqual(expect.objectContaining({ status: 200 }));
  expect(createdCurve?.body).toEqual(expect.objectContaining({ curves: expect.any(Object), output_ranges: expect.any(Object) }));
  await expect(page.locator(".response-curves-panel")).toHaveAttribute("data-candidate-id", createdCandidateId!);
  await expect(page.locator(".curve-scope")).toContainText(createdCandidateLabel);
  await expect(page.getByRole("heading", { name: /予測特性/ })).toContainText(createdCandidateLabel);

  let releaseAbortedCurve = () => undefined;
  const abortedCurveGate = new Promise<void>((resolve) => { releaseAbortedCurve = resolve; });
  let abortedCurveStarted = false;
  await page.route("**/response-curves*", async (route) => {
    abortedCurveStarted = true;
    await abortedCurveGate;
    try {
      await route.continue();
    } catch {
      // Closing the panel aborts the browser request before this route is released.
    }
  });
  const curveVariable = page.getByRole("combobox", { name: "応答曲線の設計変数" });
  if (await curveVariable.locator("option").count() > 1) {
    await curveVariable.selectOption({ index: 1 });
    await expect.poll(() => abortedCurveStarted).toBe(true);
    await page.getByRole("button", { name: "応答曲線を閉じる" }).click();
    releaseAbortedCurve();
    await expect.poll(() => failedInferenceRequests.filter((path) => path.endsWith("/response-curves")).length).toBeGreaterThan(0);
  } else {
    releaseAbortedCurve();
    await page.getByRole("button", { name: "応答曲線を閉じる" }).click();
  }
  await page.unroute("**/response-curves*");
  const curvesAfterPanelClose = curveRequests;

  const selectedNumeric = page.locator(".comparison-detail-table tbody tr.selected-row input[type=number]").first();
  const currentValue = Number(await selectedNumeric.inputValue());
  const saveInput = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await selectedNumeric.fill(String(currentValue + 0.001));
  await page.locator(".table-heading h2").click();
  await saveInput;
  await expect.poll(() => previewRequests).toBe(5);
  expect(curveRequests).toBe(curvesAfterPanelClose);

  await page.unroute("**/preview");
  let failedPreviewResponse = false;
  await page.route("**/preview", async (route) => {
    failedPreviewResponse = true;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "forced preview failure" }) });
  }, { times: 1 });
  const savedValue = Number(await selectedNumeric.inputValue());
  const saveBeforeFailedPreview = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await selectedNumeric.fill(String(savedValue + 0.001));
  await page.locator(".table-heading h2").click();
  await saveBeforeFailedPreview;
  await expect.poll(() => failedPreviewResponse).toBe(true);
  await expect(page.getByText("入力は保存しましたが、予測結果を更新できませんでした")).toBeVisible();
  await expect(page.getByText("プレビュー結果を待っています。", { exact: true })).toBeVisible();
  await expect.poll(() => inferenceResponses.some((item) => item.kind === "preview" && item.status === 500)).toBe(true);
});
