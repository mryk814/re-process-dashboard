import { expect, test } from "@playwright/test";

test("legacy workspace administration link resolves outside Project navigation", async ({ page }) => {
  await page.goto("/?view=settings&project=default&admin=developer");

  await expect(page).toHaveURL(/view=workspace/);
  await expect(page.getByRole("heading", { name: "ワークスペース", exact: true })).toBeVisible();
  await expect(page.getByText("WORKSPACE 全体")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" })).toHaveCount(0);
});

test("legacy Project setting link resolves into the canonical Project settings view", async ({ page }) => {
  await page.goto("/?view=settings&project=default&admin=ranges");

  await expect(page).toHaveURL(/view=project-settings/);
  await expect(page).toHaveURL(/project_settings=ranges/);
  await expect(page.getByRole("navigation", { name: "Project設定メニュー" })).toBeVisible();
  await expect(page.getByText("このPROJECT")).toBeVisible();
  await expect(page.getByRole("heading", { name: "入力範囲設定" })).toBeVisible();
  await expect(page.getByRole("button", { name: "開発・管理" })).toHaveCount(0);
});

test("the former Project overview settings query is normalized to the canonical view", async ({ page }) => {
  await page.goto("/?view=project&project=default&project_settings=targets");

  await expect(page).toHaveURL(/view=project-settings/);
  await expect(page).toHaveURL(/project_settings=targets/);
  await expect(page.getByRole("navigation", { name: "Project設定カテゴリ" }))
    .toBeVisible();
  await page.goBack();
  await page.goForward();
  await expect(page).toHaveURL(/view=project-settings/);
});
