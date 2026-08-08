import { expect, test } from "@playwright/test";
import { apiBaseUrl, openCandidateInputs } from "./helpers";

test("inference runs only for changed candidates and visible selected curves", async ({ page }) => {
  let previewRequests = 0;
  let curveRequests = 0;
  let similarityRequests = 0;
  const inferenceResponses: Array<{
    kind: "preview" | "curve";
    candidateId: string;
    status: number;
    body: unknown;
    parseError?: string;
  }> = [];
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
    void response.json()
      .then((body) => {
        inferenceResponses.push({ kind, candidateId, status: response.status(), body });
      })
      .catch((reason: unknown) => {
        inferenceResponses.push({
          kind,
          candidateId,
          status: response.status(),
          body: null,
          parseError: reason instanceof Error ? reason.message : String(reason),
        });
      });
  });

  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await openCandidateInputs(page);
  await expect.poll(() => previewRequests).toBe(3);
  await expect.poll(() => inferenceResponses.filter((item) => item.kind === "preview").length).toBe(3);
  const initialPreviews = inferenceResponses.filter((item) => item.kind === "preview");
  expect(new Set(initialPreviews.map((item) => item.candidateId)).size).toBe(3);
  for (const response of initialPreviews) {
    expect(response.status).toBe(200);
    expect(response.body).toEqual(expect.objectContaining({ canonical_input: expect.any(Object), predictions: expect.any(Object) }));
  }
  const initialSelectedCandidateId = new URL(page.url()).searchParams.get("candidate");
  expect(initialSelectedCandidateId).toBeTruthy();
  await expect(page.locator(".response-curves-panel")).toHaveAttribute("data-candidate-id", initialSelectedCandidateId!);
  await expect(page.locator(".response-curve-card")).toHaveCount(4);
  await expect(page.locator(".response-curves-panel")).toHaveAttribute("data-candidate-count", "3");
  await expect(page.locator(".response-curves-panel .candidate-color-legend span")).toHaveCount(3);
  await expect.poll(() => curveRequests).toBeGreaterThanOrEqual(12);
  await expect.poll(() => similarityRequests).toBe(1);
  await expect(page.locator(".similar-summary-table")).toBeVisible();

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
  await page.waitForTimeout(450);
  await expect(candidateRows.nth(1)).toHaveClass(/selected-row/);
  expect(previewRequests).toBe(3);
  await expect(page.locator(".response-curves-panel")).toHaveAttribute("data-candidate-id", selectedCandidateId!);
  await expect.poll(() => curveRequests).toBeGreaterThanOrEqual(2);
  await expect.poll(() => {
    const successful = inferenceResponses.some((item) => (
      item.kind === "curve"
      && item.candidateId === selectedCandidateId
      && item.status === 200
      && item.body !== null
    ));
    return {
      successful,
      parseErrors: inferenceResponses.flatMap((item) => item.parseError ? [item.parseError] : []),
    };
  }).toEqual({ successful: true, parseErrors: expect.any(Array) });
  const firstCurve = inferenceResponses.find((item) => (
    item.kind === "curve"
    && item.candidateId === selectedCandidateId
    && item.status === 200
    && item.body !== null
  ));
  expect(firstCurve).toEqual(expect.objectContaining({ candidateId: selectedCandidateId, status: 200 }));
  // 17 is the project's default response_curve_points.
  expect(firstCurve?.body).toEqual(expect.objectContaining({ target: expect.any(String), points: expect.any(Array), point_count: 17 }));
  await expect(page.locator(".response-curves-panel .candidate-color-legend .selected")).toContainText(selectedCandidateLabel);

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
  let createdCandidateId = "";
  await expect.poll(() => {
    const candidateId = new URL(page.url()).searchParams.get("candidate");
    createdCandidateId = candidateId && candidateId !== selectedCandidateId ? candidateId : "";
    return Boolean(createdCandidateId);
  }).toBe(true);
  const successfulCreatedPreviews = () => inferenceResponses.filter(
    (item) => item.kind === "preview" && item.candidateId === createdCandidateId && item.status === 200,
  ).length;
  const createdCandidateLabel = await page
    .locator(".candidate-name-table tbody tr.selected-row")
    .getByRole("textbox")
    .inputValue();
  const curvesBeforeCreatedPreview = curveRequests;
  await page.waitForTimeout(500);
  expect(curveRequests).toBe(curvesBeforeCreatedPreview);

  releasePreview();
  await expect.poll(() => previewRequests).toBe(4);
  await expect.poll(() => curveRequests).toBe(curvesBeforeCreatedPreview + 4);
  await expect.poll(() => inferenceResponses.some((item) => item.kind === "preview" && item.candidateId === createdCandidateId)).toBe(true);
  await expect.poll(() => inferenceResponses.some((item) => (
    item.kind === "curve"
    && item.candidateId === createdCandidateId
    && item.status === 200
    && item.body !== null
  ))).toBe(true);
  const createdPreview = inferenceResponses.find((item) => item.kind === "preview" && item.candidateId === createdCandidateId);
  const createdCurve = inferenceResponses.find((item) => (
    item.kind === "curve"
    && item.candidateId === createdCandidateId
    && item.status === 200
    && item.body !== null
  ));
  expect(createdPreview).toEqual(expect.objectContaining({ status: 200 }));
  expect(createdPreview?.body).toEqual(expect.objectContaining({ canonical_input: expect.any(Object), predictions: expect.any(Object) }));
  expect(createdCurve).toEqual(expect.objectContaining({ status: 200 }));
  expect(createdCurve?.body).toEqual(expect.objectContaining({ target: expect.any(String), points: expect.any(Array), point_count: 17 }));
  await expect(page.locator(".response-curves-panel")).toHaveAttribute("data-candidate-id", createdCandidateId!);
  await expect(page.locator(".response-curves-panel .candidate-color-legend .selected")).toContainText(createdCandidateLabel);

  const selectedNumeric = page.locator(".comparison-detail-table tbody tr.selected-row input[type=number]").first();
  const currentValue = Number(await selectedNumeric.inputValue());
  const successfulPreviewsBeforeInputChange = successfulCreatedPreviews();
  const saveInput = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await selectedNumeric.fill(String(currentValue + 0.001));
  await page.locator(".table-heading h2").click();
  await saveInput;
  await expect.poll(() => previewRequests).toBe(5);
  await expect.poll(successfulCreatedPreviews).toBe(successfulPreviewsBeforeInputChange + 1);

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
  const successfulPreviewsBeforePending = successfulCreatedPreviews();
  const failedPreviewsBeforePending = failedInferenceRequests.filter((path) => path.endsWith("/preview")).length;
  const valueBeforePendingPreview = Number(await selectedNumeric.inputValue());
  const saveBeforePendingPreview = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await selectedNumeric.fill(String(valueBeforePendingPreview + 0.001));
  await page.locator(".table-heading h2").click();
  await saveBeforePendingPreview;
  await expect.poll(() => pendingPreviewHeld).toBe(true);
  const selectedPredictionCells = page.locator(".comparison-prediction-table tbody tr.selected-row .decision-output-cell");
  await expect(selectedPredictionCells.first()).not.toHaveText("—");

  const pendingCandidateName = page
    .locator(".candidate-name-table tbody tr.selected-row")
    .getByRole("textbox");
  const saveNameDuringPreview = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await pendingCandidateName.fill(`${await pendingCandidateName.inputValue()} 更新中`);
  await page.locator(".table-heading h2").click();
  await saveNameDuringPreview;
  expect(previewRequests).toBe(6);
  releasePendingPreview();
  await expect.poll(successfulCreatedPreviews).toBe(successfulPreviewsBeforePending + 1);
  expect(failedInferenceRequests.filter((path) => path.endsWith("/preview")).length).toBe(failedPreviewsBeforePending);
  await expect(selectedPredictionCells.first()).not.toHaveText("—");
  await page.unroute("**/preview*");

  const candidateApiUrl = `${apiBaseUrl}/api/projects/default/candidates/${createdCandidateId}`;
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
  const successfulPreviewsBeforeConflictRecovery = successfulCreatedPreviews();
  const conflictName = page
    .locator(".candidate-name-table tbody tr.selected-row")
    .getByRole("textbox");
  await conflictName.fill(`${await conflictName.inputValue()} 競合1`);
  await page.locator(".table-heading h2").click();
  await expect.poll(() => conflictHeld).toBe(true);
  expect(conflictStatus).toBe(409);
  const recoveredSave = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && response.url().endsWith(`/candidates/${createdCandidateId}`)
    && response.status() === 200
  ));
  await conflictName.fill(`${await conflictName.inputValue()} 競合2`);
  await page.locator(".table-heading h2").click();
  await page.waitForTimeout(350);
  releaseConflict();
  await recoveredSave;
  await expect.poll(() => previewRequests).toBe(previewsBeforeConflictRecovery + 1);
  await expect.poll(successfulCreatedPreviews).toBe(successfulPreviewsBeforeConflictRecovery + 1);
  await expect(selectedPredictionCells.first()).not.toHaveText("—");
  await page.unroute(`**/candidates/${createdCandidateId}`);

  let failedPreviewResponse = false;
  await page.route("**/preview*", async (route) => {
    failedPreviewResponse = true;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "forced preview failure" }) });
  }, { times: 1 });
  const failedPreviewNumeric = page.locator(".candidate-inspector input.slider-number").first();
  const savedValue = Number(await failedPreviewNumeric.inputValue());
  const saveBeforeFailedPreview = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await failedPreviewNumeric.fill(String(savedValue + 0.001));
  await saveBeforeFailedPreview;
  await expect.poll(() => failedPreviewResponse).toBe(true);
  await expect(page.getByText("入力は保存しましたが、予測結果を更新できませんでした")).toBeVisible();
  await expect(selectedPredictionCells.first()).not.toHaveText("—");
  await expect(page.getByRole("alert").filter({ hasText: "プレビューを取得できませんでした" })).toHaveCount(1);
  await expect.poll(() => inferenceResponses.some((item) => item.kind === "preview" && item.status === 500)).toBe(true);
});
