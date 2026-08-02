import { expect, test } from "@playwright/test";

const apiError = (message: string, code = "data_integrity_error") => ({
  message,
  code,
  field_errors: [],
  current_candidate: null,
});

test("QualityとLineageは失敗を0件にせず、resource単位で保持・再試行する", async ({ page }) => {
  let qualityFails = false;
  await page.route(/\/api\/projects\/default\/quality$/, async (route) => {
    if (!qualityFails) {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify(apiError("forced quality failure")),
    });
  });

  await page.goto("/?view=quality&project=default");
  await expect(page.locator(".quality-summary")).toBeVisible();
  const detectedBeforeRefresh = await page.locator(".quality-summary").textContent();

  qualityFails = true;
  await page.getByRole("button", { name: "データ品質を更新" }).click();
  await expect(page.getByRole("alert")).toContainText("データ品質を更新できませんでした");
  await expect(page.getByRole("alert")).toContainText("この画面での取得時刻");
  await expect(page.getByRole("alert")).toContainText("最新の結果として扱わないでください");
  await expect(page.locator(".quality-summary")).toHaveText(detectedBeforeRefresh!);

  qualityFails = false;
  await page.getByRole("button", { name: "データ品質を再試行" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator(".quality-summary")).toBeVisible();

  let lineageIndexFails = true;
  let lineageReviewsFail = true;
  await page.route(/\/api\/projects\/default\/lineage\?/, async (route) => {
    if (!lineageIndexFails) {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify(apiError("forced lineage index failure")),
    });
  });
  await page.route(/\/api\/projects\/default\/lineage-reviews$/, async (route) => {
    if (!lineageReviewsFail) {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify(apiError("forced reviews unavailable", "subsystem_unavailable")),
    });
  });

  await page.goto("/?view=lineage&project=default");
  await expect(page.getByRole("alert").filter({
    hasText: "実績・工程の検索結果を取得できませんでした",
  })).toBeVisible();
  await expect(page.getByRole("alert").filter({
    hasText: "確認メモは現在利用できません",
  })).toBeVisible();
  await expect(page.getByRole("button", { name: "確認メモ 未取得" })).toBeVisible();
  await expect(page.getByText("一致するキーはありません。")).toHaveCount(0);

  lineageIndexFails = false;
  await page.getByRole("button", { name: "検索結果を再試行" }).click();
  await expect(page.locator(".lineage-source-facts")).toBeVisible();
  await expect(page.getByRole("alert").filter({
    hasText: "確認メモは現在利用できません",
  })).toBeVisible();

  lineageReviewsFail = false;
  await page.getByRole("button", { name: "メモを再試行" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /確認メモ \d+件/ })).toBeVisible();
});
