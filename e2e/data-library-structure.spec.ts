import { expect, test } from "@playwright/test";

test("Data Library keeps models in the selected dataset context", async ({ page }) => {
  await page.goto("/?view=data-library");

  await expect(page.getByRole("heading", { name: "使うデータを選ぶ" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "自分のデータ" })).toBeVisible();
  await expect(page.getByText(/同梱サンプル/).first()).toBeVisible();
  const bundledSamples = page.locator("details.bundled-dataset-group");
  await expect(bundledSamples).not.toHaveAttribute("open", "");
  await expect(page.getByRole("button", { name: /material_workbench_tutorial_v2.*詳細を表示/ })).not.toBeVisible();

  const selectedDataset = page.locator(".dataset-context");
  await expect(selectedDataset.getByRole("heading", { name: /material_workbench_tutorial_v2\.xlsx/ })).toBeVisible();
  await expect(selectedDataset.getByRole("heading", { name: "このデータで使うモデル" })).toBeVisible();
  await expect(selectedDataset.getByText("GP（安定ARD） · v2.1.0-stable-ard", { exact: true })).toBeVisible();

  await selectedDataset.getByRole("button", { name: "このデータでモデルを更新" }).click();
  const guide = page.getByRole("region", { name: "モデルを追加する" });
  const commands = guide.getByRole("textbox", { name: "PowerShellモデル更新手順" });
  await expect(commands).toHaveValue(
    /npm run model:diagnose[\s\S]*npm run model:build[\s\S]*npm run model:promote/,
  );
  await expect(commands).not.toHaveValue(/--activate/);
  await expect(commands).not.toHaveValue(/npm run dev/);
  await expect(page.getByRole("button", { name: "昇格済みモデルを再読込" })).toBeVisible();
  await expect(guide).toContainText("保存済み予測は再計算されません");
});

test("Data Library structure has no page-level horizontal overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=data-library");
  await page.getByRole("button", { name: "このデータでモデルを更新" }).click();

  const width = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(width.scrollWidth).toBeLessThanOrEqual(width.clientWidth);
});
