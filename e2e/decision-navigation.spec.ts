import { expect, test, type APIRequestContext } from "@playwright/test";
import { apiBaseUrl as api, createProjectWithCandidate } from "./helpers";

async function createIsolatedProject(request: APIRequestContext) {
  const project = await createProjectWithCandidate(
    request,
    "annealed-properties-v1",
    `振り返りE2E ${Date.now()}`,
    "振り返り候補",
  );
  return project.id;
}

test("primary navigation follows the decision flow and separates developer administration", async ({ page }) => {
  await page.goto("/?view=project&project=default");

  await expect(page.locator(".topbar nav").getByRole("button")).toHaveText([
    "プロジェクト",
    "データライブラリ",
    "ワークスペース",
  ]);
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button")).toHaveText([
    "概要",
    "データ探索",
    "範囲探索",
    "候補比較",
    "開発・管理",
  ]);

  await page.getByRole("button", { name: "データ探索", exact: true }).click();
  await expect(page.getByRole("heading", { name: "調べるノードを選択してください" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "データ探索" })).toBeVisible();
  await page.getByRole("button", { name: "データ品質", exact: true }).click();
  await expect(page.getByRole("heading", { name: "データ品質" })).toBeVisible();
  await expect(page.locator(".quality-filters")).toBeVisible();
  await expect(page.locator(".quality-summary")).toBeVisible();
  await expect(page.locator(".reference-scenarios")).toBeVisible();
  await page.getByText("参照データを確認", { exact: true }).click();
  await expect(page.locator(".dataset-identity")).toContainText("annealed-properties-v1");
  await expect(page.locator(".dataset-identity")).toContainText("source sha256:");
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "データ探索" })).toHaveClass(/active/);
  const qualityUrl = page.url();
  await page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "データ探索" }).click();
  await expect(page).toHaveURL(qualityUrl);

  await page.getByRole("button", { name: "開発・管理", exact: true }).click();
  await expect(page.getByRole("heading", { name: "検証と構成" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "開発・管理メニュー" })
    .getByRole("button", { name: "データ品質集計" })).toHaveCount(0);
  await page.getByRole("button", { name: "予測タスク定義", exact: true }).click();
  await expect(page).toHaveURL(/admin=task/);
  await expect(page.getByRole("heading", { name: "予測タスク定義" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "成分" })).toBeVisible();
  await expect(page.getByText("引張強さ").first()).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "予測タスク定義" })).toBeVisible();
  await page.getByRole("button", { name: "モデルと実行環境", exact: true }).click();
  await expect(page).toHaveURL(/admin=model/);
  await expect(page.getByRole("heading", { name: "モデルと実行環境" })).toBeVisible();
  await expect(page.locator(".admin-model-identity")).toBeVisible();
});

test("legacy data-quality admin link redirects to the canonical screen", async ({ page }) => {
  await page.goto("/?view=settings&admin=quality&project=default&quality_type=duplicate_key");

  await expect(page).toHaveURL(/view=quality/);
  await expect(page).not.toHaveURL(/admin=quality/);
  await expect(page).toHaveURL(/quality_type=duplicate_key/);
  await expect(page.getByRole("heading", { name: "データ品質" })).toBeVisible();
  await expect(page.locator(".quality-summary")).toBeVisible();
  await expect(page.locator(".quality-filters")).toBeVisible();
});

test("default comparison starts with candidates inside model support", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");

  const summary = page.getByRole("complementary", { name: "候補比較の判断サマリー" });
  await expect(summary).toBeVisible();
  await expect(page.locator(".target-support-list").first()).toContainText("範囲内");
  await expect(page.locator(".target-support-list").filter({ hasText: "学習範囲外" })).toHaveCount(0);
});

test("saved prediction returns directly to project history", async ({ page, request }) => {
  const projectId = await createIsolatedProject(request);
  await page.goto(`/?view=candidates&project=${projectId}`);
  await expect(page.getByLabel("候補ごとの操作")).toBeVisible();
  const save = page.getByRole("button", { name: /の詳細予測を保存/ });
  await expect(save).toBeEnabled();
  const snapshotResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/predict"));
  await save.click();
  expect((await snapshotResponse).status()).toBe(200);
  await expect(page.getByRole("button", { name: /の詳細予測を保存済み/ })).toBeDisabled();
  await page.reload();
  await expect(page.getByRole("button", { name: /の詳細予測を保存済み/ })).toBeDisabled();

  await page.getByRole("button", { name: "保存結果・履歴" }).click();
  await expect(page).toHaveURL(new RegExp(`view=project.*project=${projectId}`));
  await expect(page.getByRole("heading", { name: "候補と判断履歴" })).toBeVisible();
  await expect(page.getByText("固定した予測").first()).toBeVisible();
});

test("candidate row actions target their own row", async ({ page, request }) => {
  const projectId = await createIsolatedProject(request);
  const listed = await (await request.get(`${api}/api/projects/${projectId}/candidates`)).json() as Array<{ id: string; name: string }>;
  const first = listed[0];
  await page.goto(`/?view=candidates&project=${projectId}&candidate=${first.id}`);

  const createResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/candidates"));
  await page.getByRole("button", { name: "候補を追加" }).click();
  const second = await (await createResponse).json() as { id: string };
  await expect(page).toHaveURL(new RegExp(`candidate=${second.id}`));

  const predictResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith(`/candidates/${first.id}/predict`));
  await page.getByRole("button", { name: `${first.name}の詳細予測を保存` }).click();
  expect((await predictResponse).status()).toBe(200);
  await expect(page).toHaveURL(new RegExp(`candidate=${second.id}`));
  await expect(page.getByRole("button", { name: `${first.name}の詳細予測を保存済み` })).toBeDisabled();

  const deleteResponse = page.waitForResponse((response) => response.request().method() === "DELETE" && new URL(response.url()).pathname.endsWith(`/candidates/${first.id}`));
  await page.getByRole("button", { name: `${first.name}を一覧から外す` }).click();
  await page.getByRole("button", { name: "一覧から外す", exact: true }).click();
  expect((await deleteResponse).status()).toBe(204);
  await expect(page).toHaveURL(new RegExp(`candidate=${second.id}`));
});

test("hot rolling remains a project task and uses task labels", async ({ page }) => {
  await page.goto("/?view=candidates&project=hot-rolling-default");
  await expect(page.locator(".topbar nav")).not.toContainText("熱延");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect(page.getByText("均熱温度", { exact: true }).first()).toBeVisible();
  await expect(
    page.locator(".comparison-detail-table thead").getByRole("columnheader", { name: /引張強さ/ }),
  ).toBeVisible();
});
