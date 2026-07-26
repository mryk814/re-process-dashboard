import { expect, test } from "@playwright/test";

test("series library keeps raw, canonical and model representation distinct", async ({ page }) => {
  await page.goto("/?view=data-library");

  const section = page.locator(".series-library-section");
  await expect(section.getByRole("heading", { name: "系列データ（Series）" })).toBeVisible();
  await expect(section.getByRole("button", { name: /焼鈍温度履歴.*5点/ })).toBeVisible();
  await expect(section.getByRole("button", { name: /電池容量劣化曲線.*6点/ })).toBeVisible();

  await section.getByRole("button", { name: /電池容量劣化曲線.*6点/ }).click();
  await expect(section.getByText("正規化済み")).toBeVisible();
  await expect(section.getByRole("img", { name: /Raw（source順）。6点、cycleと%/ })).toBeVisible();
  await expect(section.getByRole("img", { name: /Canonical（意味・単位を正規化）。6点、cycleと%/ })).toBeVisible();
  await expect(section.locator(".series-transform-log")).toContainText("stable sort");

  await section.getByRole("button", { name: "モデル入力を確認" }).click();
  await expect(section.locator(".series-feature-result")).toContainText("segment_statistics_v1");
  await expect(section.locator(".series-feature-result")).toContainText("shape [1 × 5]");

  await section.getByRole("button", { name: /焼鈍温度履歴.*5点/ }).click();
  await expect(section.getByRole("img", { name: /Raw（source順）。5点、minとK/ })).toBeVisible();
  await expect(section.getByRole("img", { name: /Canonical（意味・単位を正規化）。5点、sと°C/ })).toBeVisible();
  await expect(section.locator(".series-canonical-summary")).toContainText("heat-series-si-v1");
});
