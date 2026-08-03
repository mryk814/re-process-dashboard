import { expect, test } from "@playwright/test";

test("legacy workspace administration link resolves outside Project navigation", async ({ page }) => {
  await page.goto("/?view=settings&project=default&admin=developer");

  await expect(page).toHaveURL(/view=workspace/);
  await expect(page.getByRole("heading", { name: "ワークスペース", exact: true })).toBeVisible();
  await expect(page.getByText("WORKSPACE 全体")).toBeVisible();
  await expect(page.getByText("Bundled Capability Atlas")).toBeVisible();
  await expect(page.getByText(/3 modes · 17 Tasks · \d+ Packages · 2 Graphs/)).toBeVisible();
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

test("Project settings history restores both the tab and its category", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  await page.getByRole("button", { name: "設定", exact: true }).click();
  const categories = page.getByRole("navigation", { name: "Project設定カテゴリ" });
  await categories.getByRole("button", { name: "証拠・管理" }).click();
  await expect(categories.getByRole("button", { name: "証拠・管理" })).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".project-reference-strip")).toBeVisible();

  await page.goBack();
  await expect(categories.getByRole("button", { name: "通常設定" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("region", { name: "プロジェクト設定" })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "概要" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "次の作業" })).toBeVisible();

  await page.goForward();
  await expect(categories.getByRole("button", { name: "通常設定" })).toHaveAttribute("aria-current", "page");
  await page.goForward();
  await expect(categories.getByRole("button", { name: "証拠・管理" })).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".project-reference-strip")).toBeVisible();
});
