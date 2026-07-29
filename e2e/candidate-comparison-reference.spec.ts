import { expect, test } from "@playwright/test";

test("one optional reference candidate highlights only differing input cells", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  const baselineReference = page.getByRole("checkbox", { name: "基準候補を比較の基準にする" });
  const strengthReference = page.getByRole("checkbox", { name: "高強度案を比較の基準にする" });
  const strengthRow = page.locator(".comparison-input-table tbody tr").filter({ hasText: "高強度案" });

  await expect(page.locator(".reference-difference-cell")).toHaveCount(0);
  await baselineReference.check();
  await expect(baselineReference).toBeChecked();
  await expect(strengthReference).not.toBeChecked();
  await expect(strengthRow.getByRole("spinbutton", { name: "高強度案 C", exact: true }).locator("..")).not.toHaveClass(/reference-difference-cell/);
  await expect(strengthRow.getByRole("spinbutton", { name: "高強度案 LS", exact: true }).locator("..")).toHaveClass(/reference-difference-cell/);
  await expect(strengthRow.getByLabel("基準「基準候補」と異なります").first()).toContainText("≠");

  await page.reload();
  await expect(baselineReference).toBeChecked();

  await strengthReference.check();
  await expect(strengthReference).toBeChecked();
  await expect(baselineReference).not.toBeChecked();

  await strengthReference.uncheck();
  await expect(page.locator(".candidate-reference-cell input:checked")).toHaveCount(0);
  await expect(page.locator(".reference-difference-cell")).toHaveCount(0);
});
