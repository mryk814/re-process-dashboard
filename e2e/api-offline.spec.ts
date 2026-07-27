import { expect, test } from "@playwright/test";
import { expectNoBlockingAxeViolations } from "./axe";

test("API断を明示し、変更を止め、再試行で同じ画面へ復帰する", async ({
  page,
}) => {
  let offline = true;
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    if (offline) {
      await route.abort("connectionfailed");
      return;
    }
    await route.continue();
  });

  await page.goto("/?view=project&project=default");

  await expect(page.getByRole("status").filter({
    hasText: "ローカルAPIの起動を待っています",
  })).toBeVisible();
  await expect(page.getByText(/最大 20秒/)).toBeVisible();

  const connection = page.locator(".connection-banner[role='alert']");
  await expect(connection).toBeVisible({ timeout: 25_000 });
  await expect(page.getByText("API 未接続", { exact: true })).toBeVisible();
  await expect(page.getByText("プレビュー更新中", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "＋ 新規プロジェクト" }),
  ).toBeDisabled();
  await expectNoBlockingAxeViolations(page, "API断");

  offline = false;
  await connection.getByRole("button", { name: "再試行" }).click();

  await expect(connection).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "焼鈍条件の候補検討", level: 1 }),
  ).toBeVisible();
  await expect(page.getByText("API 未接続", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "＋ 新規プロジェクト" }),
  ).toBeEnabled();
});
