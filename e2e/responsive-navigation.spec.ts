import { expect, test } from "@playwright/test";

test.use({ viewport: { width: 375, height: 812 } });

test("狭い画面でもホームの3導線を直接操作できる", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  await expect(page.getByRole("heading", {
    name: "焼鈍条件の候補検討",
    level: 1,
  })).toBeVisible();

  const navigation = page.getByRole("navigation", { name: "ホーム" });
  const buttons = navigation.getByRole("button");
  await expect(buttons).toHaveCount(3);
  for (const name of ["プロジェクト", "データライブラリ", "ワークスペース"]) {
    const button = navigation.getByRole("button", { name });
    await expect(button).toBeVisible();
    await expect(button).toBeInViewport();
  }
  await expect.poll(() => navigation.evaluate(
    (element) => element.scrollWidth <= element.clientWidth,
  )).toBe(true);

  await navigation.getByRole("button", { name: "データライブラリ" }).click();
  await expect(page.getByRole("heading", { name: "データライブラリ" })).toBeVisible();
  await navigation.getByRole("button", { name: "プロジェクト" }).click();
  await expect(page.getByRole("heading", {
    name: "焼鈍条件の候補検討",
    level: 1,
  })).toBeVisible();

  await navigation.getByRole("button", { name: "ワークスペース" }).click();
  await expect(page).toHaveURL(/view=workspace/);
  await expect(page.getByRole("heading", { name: "ワークスペース" })).toBeVisible();
  const storageButton = page.getByRole("button", { name: "保存場所を管理" });
  await storageButton.click();
  await expect(page.getByRole("dialog", { name: "ワークスペースの保管と復元" })).toBeVisible();
  await page.getByRole("button", { name: "閉じる" }).click();
  await expect(storageButton).toBeFocused();
});

test("文字を200%へ拡大しても3導線の操作対象が画面内に残る", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  await page.addStyleTag({
    content: ".topbar .brand, .topbar .nav-button { font-size: 200% !important; }",
  });

  const navigation = page.getByRole("navigation", { name: "ホーム" });
  for (const name of ["プロジェクト", "データライブラリ", "ワークスペース"]) {
    const button = navigation.getByRole("button", { name });
    await expect(button).toBeVisible();
    await expect(button).toBeInViewport();
    const box = await button.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(375);
  }
});
