import { expect, test } from "@playwright/test";

test("Model Library compares assets and hands off without changing them", async ({ page }) => {
  await page.goto("/?view=model-library&asset=packages");

  await expect(page.getByRole("heading", { name: "モデル資産を確認する" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Model Library" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("tab", { name: /Model Package/ })).toHaveAttribute("aria-selected", "true");

  const availablePackage = page.locator(".model-asset-card").filter({
    has: page.getByRole("button", { name: "Projectを作成", exact: true }),
  }).filter({
    has: page.getByText("利用可能", { exact: true }),
  }).first();
  await availablePackage.getByRole("button", { name: "Projectを作成", exact: true }).click();
  await expect(page).toHaveURL(/view=project/);
  await expect(page.getByRole("heading", { name: "新しいプロジェクト" })).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=packages/);
  await page.getByRole("tab", { name: /Prediction Graph/ }).click();
  await expect(page).toHaveURL(/view=model-library.*asset=graphs/);
  const graph = page.locator(".model-graph-card").first();
  await graph.getByText(/件の固定Revision/).click();
  await expect(graph.getByText(/Branch layers:/)).toBeVisible();
  await expect(graph.getByRole("heading", { name: "Stages / fixed references" })).toBeVisible();
  await expect(graph.getByRole("heading", { name: "Decision outputs" })).toBeVisible();

  await graph.getByRole("button", { name: "Studioで新しいRevisionを作成" }).click();
  await expect(page).toHaveURL(/view=chain-studio/);
  await expect(page.getByRole("heading", { name: "予測Taskを固定したChainとして公開する" })).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=graphs/);
  await page.getByRole("tab", { name: /Prediction Task/ }).click();
  await page.locator(".model-asset-card").first().getByRole("button", { name: "対応データを確認" }).click();
  await expect(page).toHaveURL(/view=data-library/);
  await expect(page.getByRole("heading", { name: "データライブラリ" })).toBeVisible();
});
