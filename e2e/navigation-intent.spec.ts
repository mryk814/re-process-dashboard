import { expect, test } from "@playwright/test";

test("lineage candidate remains in exploration and round-trips through stock", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=AN-00001");
  await expect(page.getByRole("heading", { name: "AN-00001" })).toBeVisible();

  const stock = page.getByRole("button", { name: /候補ストック/ });
  const before = await stock.locator("b").textContent();
  await page.getByRole("button", { name: "この実績条件から候補を作成" }).click();
  await expect(page).toHaveURL(/view=lineage/);
  await expect(page).toHaveURL(/candidate=/);
  const stockedCandidateId = new URL(page.url()).searchParams.get("candidate");
  expect(stockedCandidateId).toBeTruthy();
  await expect(stock.locator("b")).not.toHaveText(before ?? "");

  await stock.click();
  await expect(page).toHaveURL(/view=candidates/);
  await expect(page.locator(".candidate-origin")).toContainText("工程系譜 AN-00001");
  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page).toHaveURL(/view=lineage.*entity=AN-00001/);

  await page.reload();
  await expect(page.getByRole("heading", { name: "AN-00001" })).toBeVisible();
  await page.getByRole("button", { name: /候補ストック/ }).click();
  await expect(page).toHaveURL(new RegExp(`candidate=${stockedCandidateId}`));
  await page.goBack();
  await expect(page).toHaveURL(/view=lineage/);
});

test("copied candidate keeps its source and reports a deleted source", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  const sourceId = new URL(page.url()).searchParams.get("candidate");
  expect(sourceId).toBeTruthy();

  await page.getByRole("button", { name: "選択候補を複製" }).click();
  await expect(page.locator(".candidate-origin")).toContainText("候補コピー");
  const copiedId = new URL(page.url()).searchParams.get("candidate");
  expect(copiedId).toBeTruthy();

  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
  await page.getByRole("button", { name: "削除", exact: true }).click();
  await page.goto(`/?view=candidates&project=default&candidate=${copiedId}`);
  await expect(page.locator(".candidate-origin")).toContainText("コピー元は削除済みか参照できません");
});

test("archived copy source remains navigable", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  const sourceId = new URL(page.url()).searchParams.get("candidate");
  expect(sourceId).toBeTruthy();

  await page.getByRole("button", { name: /詳細予測を保存/ }).click();
  await expect(page.getByRole("status")).toContainText("詳細予測を実行");
  await page.getByRole("button", { name: "選択候補を複製" }).click();
  await expect(page.locator(".candidate-origin")).toContainText("候補コピー");
  const copiedId = new URL(page.url()).searchParams.get("candidate");
  expect(copiedId).toBeTruthy();

  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
  await page.getByRole("button", { name: "削除", exact: true }).click();
  await page.goto(`/?view=candidates&project=default&candidate=${copiedId}`);
  await expect(page.locator(".candidate-origin")).not.toContainText("削除済みか参照できません");
  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page.locator(".candidate-origin")).toContainText("archive済み候補を参照中");
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
  await expect(page.locator(".candidate-origin")).toContainText("archive済み候補を参照中");
});
