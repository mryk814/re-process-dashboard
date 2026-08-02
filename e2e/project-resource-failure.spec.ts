import { expect, test } from "@playwright/test";

test("a failed Project reference refresh does not replace independently ready content", async ({ page }) => {
  let modelPackageAttempts = 0;
  await page.route("**/api/projects/default/model-package", async (route) => {
    modelPackageAttempts += 1;
    if (modelPackageAttempts === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "temporary model package failure" }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/?view=project&project=default");

  await expect(page.getByRole("alert").filter({
    hasText: "Project参照情報を取得できませんでした",
  })).toBeVisible();
  await expect(page.getByText("次の作業", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "候補と判断履歴" })).toBeVisible();

  const retry = page.getByRole("button", { name: "Project参照情報を再試行" });
  await retry.click();
  await expect(page.getByRole("alert").filter({
    hasText: "Project参照情報を取得できませんでした",
  })).toHaveCount(0);
  expect(modelPackageAttempts).toBe(2);
  await expect(page.getByText("次の作業", { exact: true })).toBeVisible();
});
