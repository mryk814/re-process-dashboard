import { expect, test } from "@playwright/test";

test("catalog preflight failure remains readable while the API is unavailable", async ({ page }) => {
  await page.goto("/");
  const alert = page.getByRole("alert");
  await expect(alert.getByText("Workspaceの互換性検査で起動を停止しました")).toBeVisible();
  await expect(alert.getByText("catalog", { exact: true })).toBeVisible();
  await expect(alert.getByText("welding-package-v1", { exact: true })).toBeVisible();
  await expect(alert.getByText("task_contract_digestが一致しません", { exact: true })).toBeVisible();
  await expect(alert.getByText("catalog bootstrapが停止します", { exact: true })).toBeVisible();
  await expect(alert.getByText("新しいPackage versionとして再生成してください", { exact: true })).toBeVisible();
  await expect(alert.getByText("npm run dev の workspace preflight 出力", { exact: true })).toBeVisible();
  await expect(alert.getByText("docs/decisions/startup-failure-boundaries.md", { exact: true })).toBeVisible();
  await expect(page.getByText(/ローカルAPIの起動を待っています/)).toHaveCount(0);
});
