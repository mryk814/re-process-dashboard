import { expect, test } from "@playwright/test";

type RobustnessReading = {
  prediction: number;
  variationWidth: number;
};

async function runRobustness(page: import("@playwright/test").Page): Promise<RobustnessReading> {
  await page.getByRole("spinbutton", { name: "Cの公差幅" }).fill("0.01");
  await page.getByRole("spinbutton", { name: "サンプル数" }).fill("64");
  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/decision-activities/robustness-analysis-v1/runs")
  ));
  await page.getByRole("button", { name: "公差内を解析" }).click();
  expect((await runResponse).status()).toBe(201);

  const target = page.locator(".activity-targets article").filter({ hasText: "降伏強さ" });
  await expect(target.getByText("入力ばらつき", { exact: true })).toBeVisible();
  const values = await target.locator("dd").allTextContents();
  const prediction = Number(values[0].match(/-?[\d.]+/)?.[0]);
  const bounds = values[1].match(/-?[\d.]+/g)?.map(Number) ?? [];
  expect(Number.isFinite(prediction)).toBe(true);
  expect(bounds).toHaveLength(2);
  return { prediction, variationWidth: bounds[1] - bounds[0] };
}

test("robustness activity distinguishes a higher average candidate from a steadier candidate", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  await page.getByRole("button", { name: "検討アクティビティ" }).click();
  await expect(page.getByRole("heading", { name: "ロバストネス／公差解析" })).toBeVisible();
  const higherAverage = await runRobustness(page);

  await page.getByRole("button", { name: "高強度案を選択" }).click();
  await expect(page.getByText("選択中: 高強度案")).toBeVisible();
  const steadier = await runRobustness(page);

  expect(higherAverage.prediction).toBeGreaterThan(steadier.prediction);
  expect(higherAverage.variationWidth).toBeGreaterThan(steadier.variationWidth);

  await page.getByRole("button", { name: "基準候補を選択" }).click();
  await expect(page.getByRole("navigation", { name: "保存済みロバストネス解析" })).toBeVisible();
  await expect(page.locator(".activity-result-meta")).toContainText("64/64件を評価");
});

test("candidate difference activity attributes the gap and keeps an explicit residual", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  await page.getByRole("button", { name: "検討アクティビティ" }).click();
  const tabs = page.getByRole("navigation", { name: "検討アクティビティの選択" });
  await expect(tabs).toBeVisible();
  await tabs.getByRole("button", { name: "候補差分の要因分解" }).click();
  await expect(page.getByRole("heading", { name: "候補差分の要因分解" })).toBeVisible();

  await page.getByRole("combobox", { name: "比較候補" }).selectOption({ label: "高強度案" });
  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/decision-activities/candidate-difference-v1/runs")
  ));
  await page.getByRole("button", { name: "差分を分解" }).click();
  expect((await runResponse).status()).toBe(201);

  const target = page.locator(".activity-targets article").filter({ hasText: "降伏強さ" });
  await expect(target.getByText("置換で説明できた分", { exact: true })).toBeVisible();
  await expect(target.getByText("残差（交互作用）", { exact: true })).toBeVisible();
  await expect(page.getByText("入力の相違", { exact: false })).toBeVisible();
  await expect(page.locator(".activity-warnings")).toContainText("因果効果ではありません");
});
