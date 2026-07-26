import { expect, test } from "@playwright/test";

test("lineage shows the declared micrograph", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=AM-01");

  const panel = page.getByRole("region", { name: "観察画像" });
  await expect(panel).toBeVisible();
  const image = panel.getByRole("img", { name: "AM-01 の観察画像" });
  await expect(image).toBeVisible();
  // naturalWidth proves the bytes actually loaded, not just that an <img> exists.
  expect(await image.evaluate((node: HTMLImageElement) => node.naturalWidth > 0)).toBe(true);
  await expect(panel.locator("code")).toContainText(".png");
});

test("a declared image that was never imported is shown as missing, not hidden", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=AM-12");

  const panel = page.getByRole("region", { name: "観察画像" });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("img")).toHaveCount(0);
  await expect(panel).toContainText("画像ファイルを読み込めません");
  // 参照先は隠さない。取り込み漏れだと分かるようにする。
  await expect(panel.locator("code")).toContainText(".png");
});

test("a process condition without an image declares nothing", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=AN-01");

  await expect(page.getByRole("heading", { name: "AN-01" })).toBeVisible();
  await expect(page.getByRole("region", { name: "観察画像" })).toHaveCount(0);
});
