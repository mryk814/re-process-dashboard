import { expect, test } from "@playwright/test";

const apiBaseUrl = `http://127.0.0.1:${Number(process.env.PLAYWRIGHT_API_PORT ?? 8875)}`;

test("project series keep the active series open and let other series expand", async ({ page }) => {
  await page.goto("/?view=project&project=default");

  const toggles = page.locator(".project-list-group-toggle");
  await expect(toggles).toHaveCount(2);
  const activeGroup = page.locator('.project-list-item[aria-current="page"]').locator("xpath=ancestor::section");
  await expect(activeGroup.locator(".project-list-group-toggle")).toHaveAttribute("aria-expanded", "true");

  const collapsedToggle = page.locator('.project-list-group-toggle[aria-expanded="false"]').first();
  const contentId = await collapsedToggle.getAttribute("aria-controls");
  expect(contentId).toBeTruthy();
  const stableToggle = page.locator(`[aria-controls="${contentId}"]`);
  const projects = page.locator(`#${contentId}`);
  await expect(projects).toBeHidden();
  await stableToggle.click();
  await expect(stableToggle).toHaveAttribute("aria-expanded", "true");
  await expect(projects).toBeVisible();
});

test("new project creation can be cancelled or left by selecting an existing project", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  const createPanel = page.getByRole("region", { name: "新規プロジェクトの開始方法" });

  await page.getByRole("button", { name: "新規プロジェクト" }).click();
  await expect(createPanel).toBeVisible();
  await createPanel.getByRole("button", { name: "作成をやめる" }).click();
  await expect(createPanel).toBeHidden();
  await expect(page.locator(".project-hub-header")).toContainText("焼鈍条件の候補検討");

  await page.getByRole("button", { name: "新規プロジェクト" }).click();
  const collapsedGroup = page.locator('.project-list-group-toggle[aria-expanded="false"]').first();
  if (await collapsedGroup.count()) await collapsedGroup.click();
  const otherProject = page.locator(".project-list-item:not(.active):visible").first();
  const otherProjectName = await otherProject.locator("strong").innerText();
  await otherProject.click();

  await expect(createPanel).toBeHidden();
  await expect(page.locator(".project-hub-header h2")).toHaveText(otherProjectName);
});

test("a continuation can switch prediction task without leaving its series", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  await expect(page.locator(".project-hub-header").getByRole("heading", { name: "焼鈍条件の候補検討" })).toBeVisible();
  await page.getByRole("button", { name: "この検討の続き" }).click();

  const panel = page.getByRole("region", { name: "新規プロジェクトの開始方法" });
  await expect(panel.getByRole("combobox", { name: "Dataset" })).toBeEnabled();
  const task = panel.getByRole("combobox", { name: "予測タスク（Prediction Task）" });
  await expect(task).toBeEnabled();
  await task.selectOption("hot-rolled-properties-v1");
  await expect(panel.getByLabel("続ける理由（任意）")).toBeVisible();
  const series = panel.getByRole("combobox", { name: "検討のつながり" });
  await expect(series).toBeDisabled();
  const seriesId = await series.inputValue();

  const createdResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/projects");
  await panel.getByRole("button", { name: "固定してプロジェクトを作成" }).click();
  const created = await (await createdResponse).json() as { task_id: string; project_series_id: string; predecessor_project_id: string };
  expect(created.task_id).toBe("hot-rolled-properties-v1");
  expect(created.project_series_id).toBe(seriesId);
  expect(created.predecessor_project_id).toBe("default");
});

test("project settings rename the series and keep deletion at the bottom", async ({ page }) => {
  const createdResponse = await page.request.post(`${apiBaseUrl}/api/projects`, {
    data: { name: `削除位置確認 ${Date.now()}`, task_id: "annealed-properties-v1" },
  });
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json() as { id: string };
  await page.goto(`/?view=project&project=${created.id}`);
  const content = page.locator(".project-hub-content");
  const deleteButton = content.getByRole("button", { name: "プロジェクトを削除" });
  await expect(deleteButton).toBeVisible();
  await expect(content.locator(":scope > :last-child")).toHaveClass(/project-danger-zone/);

  await page.getByRole("button", { name: "設定を編集" }).click();
  const seriesName = page.getByLabel("一連の検討名");
  const renamed = `焼鈍条件シリーズ ${Date.now()}`;
  await seriesName.fill(renamed);
  await expect(seriesName).toHaveValue(renamed);
  const response = page.waitForResponse((item) => item.request().method() === "PUT" && new URL(item.url()).pathname.startsWith("/api/project-series/"));
  await page.getByRole("button", { name: "名前を保存" }).click();
  expect((await response).status()).toBe(200);
  await expect(page.locator(".project-list-group-toggle").filter({ hasText: renamed })).toBeVisible();
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
});

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
  const beforeResponse = await page.request.get(`${apiBaseUrl}/api/projects/default/candidates/${candidateId}`);
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
  await expect(page.getByRole("button", { name: `${candidateName}の詳細予測を保存` })).toBeEnabled();

  await page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "概要", exact: true }).click();
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
  await panel.getByRole("combobox", { name: "Dataset", exact: true }).selectOption({ label: "material_workbench_tutorial_v1 · thin-sheet-tutorial-v1" });
  await panel.getByRole("combobox", { name: "予測タスク" }).selectOption("annealed-properties-v1");
  await panel.getByRole("radio", { name: /空から開始/ }).check();
  await panel.getByRole("button", { name: "固定してプロジェクトを作成" }).click();
  await expect(page.getByText("まだ候補がありません", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /条件範囲から探す/ }).first().click();
  await page.getByRole("button", { name: "基準候補を作って探索を始める" }).click();
  await page.getByRole("button", { name: "候補比較", exact: true }).click();
  await expect(page.locator(".comparison-detail-table thead .prediction-col").filter({ hasText: "引張強さ" })).toBeVisible();
  await expect(page.locator(".comparison-detail-table thead .prediction-col").filter({ hasText: "降伏強さ" })).toBeVisible();
});
