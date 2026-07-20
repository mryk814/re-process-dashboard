import { expect, test } from "@playwright/test";

test("inference runs only for changed candidates and visible selected curves", async ({ page }) => {
  let previewRequests = 0;
  let curveRequests = 0;
  let similarityRequests = 0;
  const inferenceResponses: Array<{ kind: "preview" | "curve"; candidateId: string; status: number; body: unknown }> = [];
  const failedInferenceRequests: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/preview")) previewRequests += 1;
    if (path.endsWith("/response-curve")) curveRequests += 1;
    if (path.endsWith("/similar")) similarityRequests += 1;
  });
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/preview") || path.endsWith("/response-curve")) failedInferenceRequests.push(path);
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    const kind = path.endsWith("/preview") ? "preview" : path.endsWith("/response-curve") ? "curve" : null;
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
  expect(similarityRequests).toBe(0);

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
  expect(firstCurve?.body).toEqual(expect.objectContaining({ target: expect.any(String), points: expect.any(Array), point_count: 9 }));
  await expect(page.locator(".curve-scope")).toContainText(selectedCandidateLabel);
  await expect(page.getByRole("heading", { name: /予測特性/ })).toContainText(selectedCandidateLabel);

  let releasePreview = () => undefined;
  const previewGate = new Promise<void>((resolve) => { releasePreview = resolve; });
  let previewHeld = false;
  await page.route("**/preview*", async (route) => {
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
  expect(createdCurve?.body).toEqual(expect.objectContaining({ target: expect.any(String), points: expect.any(Array), point_count: 9 }));
  await expect(page.locator(".response-curves-panel")).toHaveAttribute("data-candidate-id", createdCandidateId!);
  await expect(page.locator(".curve-scope")).toContainText(createdCandidateLabel);
  await expect(page.getByRole("heading", { name: /予測特性/ })).toContainText(createdCandidateLabel);

  let releaseAbortedCurve = () => undefined;
  const abortedCurveGate = new Promise<void>((resolve) => { releaseAbortedCurve = resolve; });
  let abortedCurveStarted = false;
  await page.route("**/response-curve*", async (route) => {
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
    await expect.poll(() => failedInferenceRequests.filter((path) => path.endsWith("/response-curve")).length).toBeGreaterThan(0);
  } else {
    releaseAbortedCurve();
    await page.getByRole("button", { name: "応答曲線を閉じる" }).click();
  }
  await page.unroute("**/response-curve*");
  const curvesAfterPanelClose = curveRequests;

  const selectedNumeric = page.locator(".comparison-detail-table tbody tr.selected-row input[type=number]").first();
  const currentValue = Number(await selectedNumeric.inputValue());
  const saveInput = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await selectedNumeric.fill(String(currentValue + 0.001));
  await page.locator(".table-heading h2").click();
  await saveInput;
  await expect.poll(() => previewRequests).toBe(5);
  expect(curveRequests).toBe(curvesAfterPanelClose);

  await page.unroute("**/preview*");
  let releasePendingPreview = () => undefined;
  const pendingPreviewGate = new Promise<void>((resolve) => { releasePendingPreview = resolve; });
  let pendingPreviewHeld = false;
  await page.route("**/preview*", async (route) => {
    const response = await route.fetch();
    pendingPreviewHeld = true;
    await pendingPreviewGate;
    await route.fulfill({ response });
  }, { times: 1 });
  const successfulCreatedPreviews = () => inferenceResponses.filter(
    (item) => item.kind === "preview" && item.candidateId === createdCandidateId && item.status === 200,
  ).length;
  const successfulPreviewsBeforePending = successfulCreatedPreviews();
  const failedPreviewsBeforePending = failedInferenceRequests.filter((path) => path.endsWith("/preview")).length;
  const valueBeforePendingPreview = Number(await selectedNumeric.inputValue());
  const saveBeforePendingPreview = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await selectedNumeric.fill(String(valueBeforePendingPreview + 0.001));
  await page.locator(".table-heading h2").click();
  await saveBeforePendingPreview;
  await expect.poll(() => pendingPreviewHeld).toBe(true);
  await expect(page.locator(".evidence-panel .metric-table")).toBeVisible();
  await expect(page.getByText("旧revision・更新中", { exact: true })).toBeVisible();

  const pendingCandidateName = page.locator(".candidate-name-table tbody tr.selected-row input");
  const saveNameDuringPreview = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await pendingCandidateName.fill(`${await pendingCandidateName.inputValue()} 更新中`);
  await page.locator(".table-heading h2").click();
  await saveNameDuringPreview;
  expect(previewRequests).toBe(6);
  releasePendingPreview();
  await expect.poll(successfulCreatedPreviews).toBe(successfulPreviewsBeforePending + 1);
  expect(failedInferenceRequests.filter((path) => path.endsWith("/preview")).length).toBe(failedPreviewsBeforePending);
  await expect(page.locator(".evidence-panel .metric-table")).toBeVisible();
  await page.unroute("**/preview*");

  const similarityToggle = page.getByRole("button", { name: "根拠を表示" });
  await expect(similarityToggle).toBeVisible();
  expect(similarityRequests).toBe(0);
  await similarityToggle.click();
  await expect.poll(() => similarityRequests).toBe(1);
  await expect(page.locator(".similar-summary-table")).toBeVisible();
  await page.getByRole("button", { name: "閉じる" }).click();
  await page.getByRole("button", { name: "根拠を表示" }).click();
  await expect(page.locator(".similar-summary-table")).toBeVisible();
  expect(similarityRequests).toBe(1);

  const candidateApiUrl = `http://127.0.0.1:8875/api/projects/default/candidates/${createdCandidateId}`;
  const currentCandidateResponse = await page.request.get(candidateApiUrl);
  expect(currentCandidateResponse.status()).toBe(200);
  const externalCandidate = await currentCandidateResponse.json() as {
    name: string;
    revision: number;
    inputs: { composition: Record<string, number>; process: Record<string, number>; categorical: Record<string, string>; heat_pattern: unknown };
    provenance: unknown;
  };
  const externalInputs = structuredClone(externalCandidate.inputs);
  const externalCompositionKey = Object.keys(externalInputs.composition)[0];
  expect(externalCompositionKey).toBeTruthy();
  externalInputs.composition[externalCompositionKey] += 0.002;
  const externalUpdate = await page.request.put(candidateApiUrl, {
    data: {
      name: externalCandidate.name,
      inputs: externalInputs,
      provenance: externalCandidate.provenance,
      expected_revision: externalCandidate.revision,
    },
  });
  expect(externalUpdate.status()).toBe(200);

  let releaseConflict = () => undefined;
  const conflictGate = new Promise<void>((resolve) => { releaseConflict = resolve; });
  let conflictHeld = false;
  let conflictStatus = 0;
  await page.route(`**/candidates/${createdCandidateId}`, async (route) => {
    const response = await route.fetch();
    conflictStatus = response.status();
    conflictHeld = true;
    await conflictGate;
    await route.fulfill({ response });
  }, { times: 1 });
  const previewsBeforeConflictRecovery = previewRequests;
  const conflictName = page.locator(".candidate-name-table tbody tr.selected-row input");
  await conflictName.fill(`${await conflictName.inputValue()} 競合1`);
  await page.locator(".table-heading h2").click();
  await expect.poll(() => conflictHeld).toBe(true);
  expect(conflictStatus).toBe(409);
  await conflictName.fill(`${await conflictName.inputValue()} 競合2`);
  await page.locator(".table-heading h2").click();
  await page.waitForTimeout(350);
  const recoveredSave = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && response.url().endsWith(`/candidates/${createdCandidateId}`)
    && response.status() === 200
  ));
  releaseConflict();
  await recoveredSave;
  await expect.poll(() => previewRequests).toBe(previewsBeforeConflictRecovery + 1);
  await expect(page.locator(".evidence-panel .metric-table")).toBeVisible();
  await page.unroute(`**/candidates/${createdCandidateId}`);

  let failedPreviewResponse = false;
  await page.route("**/preview*", async (route) => {
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
  await expect(page.locator(".evidence-panel .metric-table")).toBeVisible();
  await expect(page.getByText("更新失敗・旧結果", { exact: true })).toBeVisible();
  await expect.poll(() => inferenceResponses.some((item) => item.kind === "preview" && item.status === 500)).toBe(true);
});
