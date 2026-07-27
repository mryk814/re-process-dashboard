import { expect, test } from "@playwright/test";

import { apiBaseUrl } from "./helpers";

async function createProjectFromDefault(
  page: import("@playwright/test").Page,
  name: string,
  newProjectSeries?: { name: string; description: string },
) {
  const referenceResponse = await page.request.get(`${apiBaseUrl}/api/projects/default`);
  expect(referenceResponse.status()).toBe(200);
  const reference = await referenceResponse.json() as {
    task_id: string;
    dataset_view_revision_id: string;
    model_package_ref_id: string;
  };
  return page.request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name,
      task_id: reference.task_id,
      dataset_view_revision_id: reference.dataset_view_revision_id,
      model_package_ref_id: reference.model_package_ref_id,
      new_project_series: newProjectSeries ?? null,
    },
  });
}

test("project series keep the active series open and let other series expand", async ({ page }) => {
  await page.goto("/?view=project&project=default");

  const toggles = page.locator(".project-list-group-toggle");
  // How many projects exist depends on what earlier specs created, so read the
  // group the active project actually sits in instead of assuming its shape.
  await expect.poll(() => page.evaluate(() => {
    const active = document.querySelector('.project-list-item[aria-current="page"]');
    const group = active?.closest("section.project-list-group");
    if (!group) return "no-group";
    if (group.classList.contains("singleton")) return "singleton";
    return group.querySelector(".project-list-group-toggle")?.getAttribute("aria-expanded") ?? "no-toggle";
  })).toMatch(/^(singleton|true)$/);

  const collapsedToggle = page.locator('.project-list-group-toggle[aria-expanded="false"]').first();
  if (await collapsedToggle.count()) {
    const contentId = await collapsedToggle.getAttribute("aria-controls");
    expect(contentId).toBeTruthy();
    const stableToggle = page.locator(`[aria-controls="${contentId}"]`);
    const projects = page.locator(`#${contentId}`);
    await expect(projects).toBeHidden();
    await stableToggle.click();
    await expect(stableToggle).toHaveAttribute("aria-expanded", "true");
    await expect(projects).toBeVisible();
  }
  await expect(toggles).toHaveCount(await page.locator(".project-list-group:not(.singleton)").count());
});

test("a single-project series is shown as a direct project without collapse hierarchy", async ({ page }) => {
  const createdResponse = await createProjectFromDefault(page, `単独プロジェクト ${Date.now()}`);
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json() as { id: string };
  await page.goto(`/?view=project&project=${created.id}`);

  const activeGroup = page.locator('.project-list-item[aria-current="page"]').locator("xpath=ancestor::section");
  await expect(activeGroup).toHaveClass(/singleton/);
  await expect(activeGroup.locator(".project-list-group-toggle")).toHaveCount(0);
  await expect(activeGroup.locator(".project-list-item")).toHaveCount(1);
  await expect(page.getByText("その他の検討", { exact: true })).toHaveCount(0);

  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
});

test("a one-project series stays out of the overview until grouping is requested", async ({ page }) => {
  const groupName = `単独グループ ${Date.now()}`;
  const createdResponse = await createProjectFromDefault(
    page,
    `単独所属 ${Date.now()}`,
    { name: groupName, description: "" },
  );
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json() as { id: string };
  await page.goto(`/?view=project&project=${created.id}`);

  await expect(page.locator(".api-state")).toHaveCount(0);
  await expect(page.locator(".project-reference-strip").getByText("検討グループ", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "設定を編集" }).click();
  const groupEntry = page.locator(".project-settings-panel").getByRole("button", { name: "ほかの検討とまとめる" });
  await expect(groupEntry).toBeVisible();
  await expect(page.locator(".group-membership-setting")).toHaveCount(0);
  await groupEntry.click();
  await expect(page.getByRole("combobox", { name: "所属グループ" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "グループ名" })).toHaveValue(groupName);

  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
});

test("new project creation can be cancelled or left by selecting an existing project", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  const createPanel = page.getByRole("region", { name: "新規プロジェクトの開始方法" });

  await page.getByRole("button", { name: "新規プロジェクト" }).click();
  await expect(createPanel).toBeVisible();
  await expect(page.getByRole("button", { name: "新規プロジェクト" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "作成をやめる" })).toHaveCount(1);
  await createPanel.getByRole("button", { name: "作成をやめる" }).click();
  await expect(createPanel).toBeHidden();
  await expect(page.getByRole("button", { name: "新規プロジェクト" })).toBeEnabled();
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

test("Dataset choices explain use, order, and duplicate identity before Project creation", async ({ page }) => {
  const projectResponse = await page.request.get(`${apiBaseUrl}/api/projects/default`);
  expect(projectResponse.status()).toBe(200);
  const defaultProject = await projectResponse.json() as {
    name: string;
    dataset_view_revision_id: string;
  };
  const optionsResponse = await page.request.get(`${apiBaseUrl}/api/project-creation-options`);
  expect(optionsResponse.status()).toBe(200);
  const options = await optionsResponse.json() as {
    datasets: Array<{
      data_asset: { original_filename: string };
      dataset_revision: { id: string };
      dataset_views?: Array<{ id: string; name: string }>;
      profile_revision: { name: string; revision: number };
    }>;
  };
  const source = options.datasets.find(
    (dataset) => dataset.data_asset.original_filename === "material_workbench_tutorial_v2.xlsx",
  );
  expect(source).toBeTruthy();
  const duplicateLogicalId = `e2e-duplicate-${Date.now()}`;
  const duplicateResponse = await page.request.post(`${apiBaseUrl}/api/data-library/views`, {
    data: {
      view_id: duplicateLogicalId,
      revision: 1,
      name: source!.dataset_views?.[0]?.name ?? "material_workbench_tutorial_v2",
      kind: "single",
      members: [{ dataset_revision_id: source!.dataset_revision.id, ordinal: 0 }],
    },
  });
  expect(duplicateResponse.status()).toBe(201);
  const duplicateView = await duplicateResponse.json() as { id: string };

  await page.goto("/?view=project&project=default");
  await page.getByRole("button", { name: "新規プロジェクト" }).click();
  const panel = page.getByRole("region", { name: "新規プロジェクトの開始方法" });
  const datasetSelect = panel.getByRole("combobox", { name: "Dataset", exact: true });
  const usedOptions = datasetSelect.locator('optgroup[label="利用中のデータ"] > option');
  const unusedOptions = datasetSelect.locator('optgroup[label="未使用のデータ"] > option');

  await expect(datasetSelect.locator("option[value]").first()).toBeAttached();
  await expect(usedOptions.first()).toBeAttached();
  await expect(unusedOptions.first()).toBeAttached();
  expect(await usedOptions.count()).toBeGreaterThan(0);
  expect(await unusedOptions.count()).toBeGreaterThan(0);
  const labels = await datasetSelect.locator("option[value]").allTextContents();
  expect(new Set(labels).size).toBe(labels.length);
  expect(labels.join(" ")).not.toContain("thin-sheet-tutorial-v1");
  const useCounts = (await usedOptions.allTextContents()).map(
    (label) => Number(label.match(/利用中(\d+)件/)?.[1] ?? 0),
  );
  expect(useCounts).toEqual([...useCounts].sort((left, right) => right - left));

  const duplicateLabels = labels.filter((label) => label.includes("material_workbench_tutorial_v2"));
  expect(duplicateLabels.length).toBeGreaterThanOrEqual(2);
  expect(duplicateLabels.every((label) => label.includes("登録") && label.includes("…"))).toBe(true);

  await datasetSelect.selectOption(defaultProject.dataset_view_revision_id);
  const fixedContent = panel.getByRole("region", { name: "作成後に固定される内容" });
  const fixedDataset = options.datasets.find(
    (dataset) => dataset.dataset_views?.some((view) => view.id === defaultProject.dataset_view_revision_id),
  );
  expect(fixedDataset).toBeTruthy();
  await expect(fixedContent).toContainText(`利用中: ${defaultProject.name}`);
  await expect(fixedContent).toContainText(
    `${fixedDataset!.profile_revision.name} · r${fixedDataset!.profile_revision.revision}`,
  );
  await datasetSelect.selectOption(duplicateView.id);
  await expect(datasetSelect).toHaveValue(duplicateView.id);
  await expect(fixedContent).toContainText("このDatasetを使うProjectはありません");
});

test("a continuation can switch prediction task without leaving its series", async ({ page }) => {
  const sourceResponse = await createProjectFromDefault(
    page,
    `続き元 ${Date.now()}`,
    { name: `継承するグループ ${Date.now()}`, description: "" },
  );
  expect(sourceResponse.status()).toBe(201);
  const source = await sourceResponse.json() as { id: string; project_series_id: string };
  await page.goto(`/?view=project&project=${source.id}`);
  await expect(page.locator(".api-state")).toHaveCount(0);
  await expect(page.locator(".project-hub-header").getByRole("heading", { name: /続き元/ })).toBeVisible();
  await page.getByRole("button", { name: "このプロジェクトの続き" }).click();

  const panel = page.getByRole("region", { name: "新規プロジェクトの開始方法" });
  await expect(panel.getByRole("combobox", { name: "Dataset" })).toBeEnabled();
  const task = panel.getByRole("combobox", { name: "予測構成" });
  await expect(task).toBeEnabled();
  await task.selectOption("task:hot-rolled-properties-v1");
  await panel.getByRole("combobox", { name: "Model Package" }).selectOption({ index: 1 });
  await expect(panel.getByLabel("続ける理由（任意）")).toBeVisible();
  await expect(panel.getByRole("radio", { name: /既存グループ/ })).toBeChecked();
  const series = panel.getByRole("combobox", { name: "追加する検討グループ" });
  await expect(series).toBeEnabled();
  const seriesId = await series.inputValue();

  const createdResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/projects");
  await panel.getByRole("button", { name: "固定してプロジェクトを作成" }).click();
  const response = await createdResponse;
  const responseBody = await response.text();
  expect(response.status(), responseBody).toBe(201);
  const created = JSON.parse(responseBody) as {
    id: string;
    task_id: string;
    project_series_id: string;
    predecessor_project_id: string;
    design_space: { task_id: string } | null;
  };
  expect(created.task_id).toBe("hot-rolled-properties-v1");
  expect(created.project_series_id).toBe(seriesId);
  expect(created.predecessor_project_id).toBe(source.id);
  expect(created.design_space?.task_id).toBe("hot-rolled-properties-v1");
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${source.id}`)).status()).toBe(204);
});

test("project settings keep one fixed reference display and archiving at the bottom", async ({ page }) => {
  const createdResponse = await createProjectFromDefault(page, `アーカイブ位置確認 ${Date.now()}`);
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json() as { id: string };
  await page.goto(`/?view=project&project=${created.id}`);
  const content = page.locator(".project-hub-content");
  const archiveButton = content.getByRole("button", { name: "プロジェクトをアーカイブ" });
  await expect(archiveButton).toBeVisible();
  await expect(content.locator(":scope > :last-child")).toHaveClass(/project-danger-zone/);

  await page.getByRole("button", { name: "設定を編集" }).click();
  await expect(page.locator(".project-reference-strip")).toHaveCount(1);
  await expect(page.locator(".project-fixed-bindings")).toHaveCount(0);
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
});

test("project hub separates current revision from fixed snapshot and restores a new candidate", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  await expect(page.getByRole("heading", { name: "次の作業" })).toBeVisible();
  await page.getByRole("button", { name: "範囲探索 目標と入力範囲から候補を生成" }).click();
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
  await expect(panel.getByRole("radio", { name: /グループなし/ })).toBeChecked();
  await expect(panel.getByRole("radio", { name: /既存グループ/ })).toBeVisible();
  await expect(panel.getByRole("radio", { name: /新しい検討グループ/ })).toBeVisible();
  await panel.getByLabel("プロジェクト名").fill(`空の検討 ${Date.now()}`);
  const datasetSelect = panel.getByRole("combobox", { name: "Dataset", exact: true });
  const tutorialDatasetValue = () => datasetSelect.evaluate((select: HTMLSelectElement) => (
    [...select.options].find((option) => option.text.includes("material_workbench_tutorial_v2"))?.value ?? ""
  ));
  await expect.poll(tutorialDatasetValue).not.toBe("");
  await datasetSelect.selectOption(await tutorialDatasetValue());
  await panel.getByRole("combobox", { name: "予測構成" }).selectOption("task:annealed-properties-v1");
  await panel.getByRole("combobox", { name: "Model Package" }).selectOption({ index: 1 });
  await panel.getByRole("radio", { name: /空から開始/ }).check();
  await panel.getByRole("radio", { name: /新しい検討グループ/ }).check();
  await expect(panel.getByRole("button", { name: "固定してプロジェクトを作成" })).toBeDisabled();
  const groupName = `新規グループ ${Date.now()}`;
  await panel.getByRole("textbox", { name: "新しい検討グループ名" }).fill(groupName);
  await expect(panel.getByRole("button", { name: "固定してプロジェクトを作成" })).toBeEnabled();
  const createdResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/projects");
  await panel.getByRole("button", { name: "固定してプロジェクトを作成" }).click();
  const created = await (await createdResponse).json() as { project_series_id: string | null };
  expect(created.project_series_id).not.toBeNull();
  const optionsResponse = await page.request.get(`${apiBaseUrl}/api/project-creation-options`);
  expect(optionsResponse.status()).toBe(200);
  const options = await optionsResponse.json() as { project_series: Array<{ id: string; name: string }> };
  expect(options.project_series.find((series) => series.id === created.project_series_id)?.name).toBe(groupName);
  await expect(page.getByText("まだ候補がありません", { exact: true })).toBeVisible();
  await expect(page.locator(".project-history-section").getByRole("button")).toHaveCount(0);
  await page.locator(".project-next-actions").getByRole("button", { name: /範囲探索/ }).click();
  await page.getByRole("button", { name: "基準候補を作って探索を始める" }).click();
  await page.getByRole("button", { name: "候補比較", exact: true }).click();
  await expect(page.locator(".comparison-prediction-table thead .decision-output-col").filter({ hasText: "引張強さ" })).toBeVisible();
  await expect(page.locator(".comparison-prediction-table thead .decision-output-col").filter({ hasText: "降伏強さ" })).toBeVisible();
});
