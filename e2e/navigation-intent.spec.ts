import { expect, test } from "@playwright/test";

test("quality finding opens the selected lineage node and returns with filters", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto("/?view=quality&project=default");
  await expect(page.getByRole("heading", { name: "データ品質" })).toBeVisible();

  await page.getByRole("button", { name: /重複/ }).click();
  await expect(page).toHaveURL(/quality_type=duplicate_key/);
  const issueRow = page.locator(".quality-table tbody tr").filter({ has: page.getByRole("button", { name: "系譜で確認" }) }).first();
  const entityKey = (await issueRow.locator("td").nth(1).textContent())?.trim();
  expect(entityKey).toBeTruthy();
  await issueRow.getByRole("button", { name: "系譜で確認" }).click();

  await expect(page).toHaveURL(new RegExp(`view=lineage.*entity=${entityKey}`));
  await expect(page.locator(".lineage-node.selected")).toContainText(entityKey!);
  await expect(page.locator(".investigation-context")).toContainText("データ品質の検出結果から調査中");
  await page.reload();
  await expect(page.locator(".lineage-node.selected")).toContainText(entityKey!);

  await page.getByRole("button", { name: "品質一覧へ戻る" }).click();
  await expect(page).toHaveURL(/view=quality/);
  await expect(page).toHaveURL(/quality_type=duplicate_key/);
  await expect(page.locator(".quality-focus-row")).toContainText(entityKey!);

  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("permission denied")) },
    });
  });
  await page.locator(".quality-focus-row").getByRole("button", { name: "キーをコピー" }).click();
  await expect(page.getByRole("alert")).toContainText("クリップボード権限を確認してください");

  const beforeDownloadUrl = page.url();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "検出結果をCSV出力" }).click();
  expect((await download).suggestedFilename()).toBe("detected-data-quality.csv");
  await expect(page).toHaveURL(beforeDownloadUrl);
  expect(pageErrors).toEqual([]);
});

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
