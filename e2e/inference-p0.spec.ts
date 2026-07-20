import { expect, test } from "@playwright/test";

test("inference runs only for changed candidates and visible selected curves", async ({ page }) => {
  let previewRequests = 0;
  let curveRequests = 0;
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/preview")) previewRequests += 1;
    if (path.endsWith("/response-curves")) curveRequests += 1;
  });

  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect.poll(() => previewRequests).toBe(3);
  expect(curveRequests).toBe(0);

  const candidateRows = page.locator(".candidate-name-table tbody tr");
  await candidateRows.nth(1).click();
  await page.waitForTimeout(550);
  expect(previewRequests).toBe(3);

  const nameInput = candidateRows.nth(1).getByRole("textbox");
  const saveName = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await nameInput.fill(`${await nameInput.inputValue()} 表示名`);
  await nameInput.press("Tab");
  await saveName;
  await page.waitForTimeout(450);
  expect(previewRequests).toBe(3);
  expect(curveRequests).toBe(0);

  await page.getByRole("button", { name: "選択候補の応答曲線を表示" }).click();
  await expect.poll(() => curveRequests).toBe(1);

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
  await page.waitForTimeout(500);
  expect(curveRequests).toBe(1);

  releasePreview();
  await expect.poll(() => previewRequests).toBe(4);
  await expect.poll(() => curveRequests).toBe(2);

  await page.getByRole("button", { name: "応答曲線を閉じる" }).click();
  const selectedNumeric = page.locator(".comparison-detail-table tbody tr.selected-row input[type=number]").first();
  const currentValue = Number(await selectedNumeric.inputValue());
  const saveInput = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/candidates/"));
  await selectedNumeric.fill(String(currentValue + 0.001));
  await selectedNumeric.press("Tab");
  await saveInput;
  await expect.poll(() => previewRequests).toBe(5);
  expect(curveRequests).toBe(2);
});
