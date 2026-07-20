import { expect, test } from "@playwright/test";

test("project hub separates current revision from fixed snapshot and restores a new candidate", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  await expect(page.getByRole("heading", { name: "次の作業" })).toBeVisible();
  await page.getByRole("button", { name: /条件範囲から探す/ }).click();
  await expect(page).toHaveURL(/view=explore/);
  await expect(page.getByRole("heading", { name: /範囲探索/ })).toBeVisible();

  await page.getByRole("button", { name: "候補比較", exact: true }).click();
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  const selectedRow = page.locator(".candidate-name-table tbody tr.selected-row");
  const candidateName = await selectedRow.getByRole("textbox").inputValue();
  const candidateId = new URL(page.url()).searchParams.get("candidate");
  expect(candidateId).toBeTruthy();
  const beforeResponse = await page.request.get(`http://127.0.0.1:8875/api/projects/default/candidates/${candidateId}`);
  const before = await beforeResponse.json() as { revision: number };

  const detailed = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith(`/candidates/${candidateId}/predict`));
  await page.getByRole("button", { name: new RegExp(`${candidateName}の詳細予測を保存`) }).click();
  expect((await detailed).status()).toBe(200);

  const numeric = page.locator(".comparison-detail-table tbody tr.selected-row input[type=number]").first();
  const value = Number(await numeric.inputValue());
  const saved = page.waitForResponse((response) => response.request().method() === "PUT" && new URL(response.url()).pathname.endsWith(`/candidates/${candidateId}`));
  await numeric.fill(String(value + 0.001));
  await page.locator(".table-heading h2").click();
  expect((await saved).status()).toBe(200);

  await page.getByRole("button", { name: "プロジェクト概要", exact: true }).click();
  const card = page.locator(".project-history-card", { hasText: candidateName });
  await expect(card).toContainText(`編集版 ${before.revision + 1}`);
  await expect(card).toContainText(`編集版 ${before.revision}`);
  await expect(card.getByText("現在のpreview", { exact: true })).toBeVisible();
  await expect(card.getByText("固定した予測", { exact: true }).first()).toBeVisible();

  await card.getByRole("button", { name: "詳細" }).first().click();
  await page.getByLabel("判断理由").fill("r1時点の予測根拠を採用");
  const decisionResponse = page.waitForResponse((response) => response.request().method() === "PUT" && new URL(response.url()).pathname.endsWith("/decision"));
  await page.getByRole("button", { name: "採用判断として固定" }).click();
  expect((await decisionResponse).status()).toBe(200);
  await expect(card).toContainText("採用判断");
  await expect(card).toContainText("r1時点の予測根拠を採用");

  const restoreResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.includes("/snapshots/") && new URL(response.url()).pathname.endsWith("/restore"));
  await card.getByRole("button", { name: "新しい候補として複製" }).first().click();
  const restored = await restoreResponse;
  expect(restored.status()).toBe(201);
  const restoredBody = await restored.json() as { id: string; provenance: { source_kind: string; source_ref: { snapshot_id: string } } };
  await expect(page).toHaveURL(new RegExp(`view=candidates.*candidate=${restoredBody.id}`));
  expect(restoredBody.provenance.source_kind).toBe("snapshot");

  await page.reload();
  await expect(page.locator(".candidate-name-table tbody tr.selected-row input")).toHaveValue(/復元/);
});

test("new project creation requires an explicit empty or copy choice", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  await page.getByRole("button", { name: "新規プロジェクト" }).click();
  const panel = page.getByRole("region", { name: "新規プロジェクトの開始方法" });
  await expect(panel.getByRole("radio", { name: /空から開始/ })).toBeVisible();
  await expect(panel.getByRole("radio", { name: /現在候補をコピー/ })).toBeVisible();
  await panel.getByLabel("プロジェクト名").fill(`空の検討 ${Date.now()}`);
  await panel.getByRole("combobox", { name: "予測タスク" }).selectOption("hot-rolled-properties-v1");
  await panel.getByRole("radio", { name: /空から開始/ }).check();
  await panel.getByRole("button", { name: "この内容で作成" }).click();
  await expect(page.getByText("まだ候補がありません", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /条件範囲から始める/ }).first().click();
  await page.getByRole("button", { name: "基準候補を作って探索を始める" }).click();
  await page.getByRole("button", { name: "候補比較", exact: true }).click();
  await expect(page.locator(".comparison-detail-table thead").getByText("引張強さ", { exact: false })).toBeVisible();
  await expect(page.locator(".comparison-detail-table thead").getByText("降伏強さ", { exact: false })).toHaveCount(0);
});
