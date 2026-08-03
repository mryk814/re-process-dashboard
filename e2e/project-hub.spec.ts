import { expect, test } from "@playwright/test";

import { apiBaseUrl, createProjectWithCandidate, openCandidateInputs } from "./helpers";

async function candidateRevision(
  page: import("@playwright/test").Page,
  candidateId: string,
): Promise<number> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await page.request.get(
        `${apiBaseUrl}/api/projects/default/candidates/${candidateId}`,
      );
      if (response.status() === 200) {
        return (await response.json() as { revision: number }).revision;
      }
      lastError = new Error(
        `candidate revision request returned ${response.status()}: ${await response.text()}`,
      );
    } catch (error) {
      lastError = error;
    }
    await page.waitForTimeout(250 * (attempt + 1));
  }
  throw lastError;
}

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

async function gotoProjectAfterCreationOptions(
  page: import("@playwright/test").Page,
  projectId: string,
) {
  await Promise.all([
    page.waitForResponse((response) => (
      new URL(response.url()).pathname === "/api/project-creation-options"
      && response.status() === 200
    )),
    page.goto(`/?view=project&project=${projectId}`),
  ]);
}

async function gotoProjectAfterSessionReady(
  page: import("@playwright/test").Page,
  projectId: string,
  expectedName: string,
) {
  await Promise.all([
    page.waitForResponse((response) => (
      response.request().method() === "GET"
      && new URL(response.url()).pathname === "/api/projects"
      && response.status() === 200
    )),
    page.goto(`/?view=project&project=${projectId}`),
  ]);
  const projectName = page.getByRole("textbox", { name: "プロジェクト名" });
  await expect(projectName).toBeEnabled();
  await expect(projectName).toHaveValue(expectedName);
}

test("project series keep the active series open and let other series expand", async ({ page }) => {
  await gotoProjectAfterSessionReady(page, "default", "焼鈍条件の候補検討");

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

test("long bundled dataset names stay inside the project list on the overview", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await gotoProjectAfterSessionReady(page, "default", "焼鈍条件の候補検討");

  const projectList = page.getByRole("complementary", { name: "プロジェクト一覧" });
  await expect(projectList).toBeVisible();
  await expect(page.getByRole("textbox", { name: "プロジェクト名" })).toHaveValue("焼鈍条件の候補検討");

  const dimensions = await projectList.evaluate((element) => {
    const items = element.querySelector<HTMLElement>(".project-list-items");
    return {
      panelClientWidth: element.clientWidth,
      panelScrollWidth: element.scrollWidth,
      itemsClientWidth: items?.clientWidth ?? 0,
      itemsScrollWidth: items?.scrollWidth ?? 0,
    };
  });
  expect(dimensions.panelScrollWidth).toBeLessThanOrEqual(dimensions.panelClientWidth);
  expect(dimensions.itemsScrollWidth).toBeLessThanOrEqual(dimensions.itemsClientWidth);
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
  await gotoProjectAfterCreationOptions(page, created.id);

  await expect(page.locator(".api-state")).toHaveCount(0);
  await expect(page.locator(".project-reference-strip").getByText("検討グループ", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "設定", exact: true }).click();
  const groupEntry = page.locator(".project-settings-panel").getByRole("button", { name: "ほかの検討とまとめる" });
  await expect(groupEntry).toBeVisible();
  await expect(page.locator(".group-membership-setting")).toHaveCount(0);
  await groupEntry.click();
  await expect(page.getByRole("combobox", { name: "所属グループ" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "グループ名" })).toHaveValue(groupName);

  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
});

test("a project can leave its group and become ungrouped again", async ({ page }) => {
  const groupName = `解除するグループ ${Date.now()}`;
  const createdResponse = await createProjectFromDefault(
    page,
    `所属解除 ${Date.now()}`,
    { name: groupName, description: "" },
  );
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json() as { id: string; project_series_id: string };
  expect(created.project_series_id).not.toBeNull();
  await gotoProjectAfterCreationOptions(page, created.id);

  await expect(page.locator(".api-state")).toHaveCount(0);
  await page.getByRole("button", { name: "設定", exact: true }).click();
  const groupEntry = page.locator(".project-settings-panel").getByRole("button", { name: "ほかの検討とまとめる" });
  await expect(groupEntry).toBeVisible();
  await groupEntry.click();
  const membership = page.locator(".group-membership-setting");
  const select = membership.getByRole("combobox", { name: "所属グループ" });
  await expect(membership.getByRole("button", { name: "このプロジェクトを移動" })).toBeDisabled();

  await select.selectOption({ label: "グループなし" });
  await expect(membership.locator(".warning-note")).toContainText(groupName);
  const leave = membership.getByRole("button", { name: "このプロジェクトをグループから外す" });
  await expect(leave).toBeEnabled();
  await leave.click();

  await expect(membership.getByRole("option", { name: "グループなし" })).toHaveCount(0);
  await expect(page.locator(".panel-error")).toHaveCount(0);
  const after = await (await page.request.get(`${apiBaseUrl}/api/projects/${created.id}`)).json() as {
    project_series_id: string | null;
  };
  expect(after.project_series_id).toBeNull();

  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
});

test("new project creation can be cancelled or left by selecting an existing project", async ({ page }) => {
  await gotoProjectAfterCreationOptions(page, "default");
  const createPanel = page.getByRole("region", { name: "新規プロジェクトの開始方法" });

  await page.getByRole("button", { name: "新規プロジェクト" }).click();
  await expect(createPanel).toBeVisible();
  await expect(page.getByRole("button", { name: "新規プロジェクト" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "作成をやめる" })).toHaveCount(1);
  await createPanel.getByRole("button", { name: "作成をやめる" }).click();
  await expect(createPanel).toBeHidden();
  await expect(page.getByRole("button", { name: "新規プロジェクト" })).toBeEnabled();
  await expect(page.getByRole("textbox", { name: "プロジェクト名" })).toHaveValue("焼鈍条件の候補検討");

  await page.getByRole("button", { name: "新規プロジェクト" }).click();
  const collapsedGroup = page.locator('.project-list-group-toggle[aria-expanded="false"]').first();
  if (await collapsedGroup.count()) await collapsedGroup.click();
  const otherProject = page.locator(".project-list-item:not(.active):visible").first();
  const otherProjectName = await otherProject.locator("strong").innerText();
  await otherProject.click();

  await expect(createPanel).toBeHidden();
  await expect(page.getByRole("textbox", { name: "プロジェクト名" })).toHaveValue(otherProjectName);
});

test("inline Project rename does not overwrite the next Project after a delayed save", async ({ page }) => {
  const firstResponse = await createProjectFromDefault(page, `名称変更元 ${Date.now()}`);
  const secondResponse = await createProjectFromDefault(page, `切替先 ${Date.now()}`);
  expect(firstResponse.status()).toBe(201);
  expect(secondResponse.status()).toBe(201);
  const first = await firstResponse.json() as { id: string; name: string };
  const second = await secondResponse.json() as { id: string; name: string };
  const renamed = `${first.name} 更新`;
  let requestedName = "";
  let routedStatus = 0;
  let routedBody = "";

  await page.route("**/api/projects/**", async (route) => {
    if (
      route.request().method() !== "PUT"
      || new URL(route.request().url()).pathname !== `/api/projects/${first.id}`
    ) {
      await route.continue();
      return;
    }
    const payload = route.request().postDataJSON() as { name: string };
    requestedName = payload.name;
    const response = await page.request.put(route.request().url(), { data: payload });
    routedStatus = response.status();
    routedBody = await response.text();
    await new Promise((resolve) => setTimeout(resolve, 750));
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      body: routedBody,
    });
  });

  await page.goto(`/?view=project&project=${first.id}`);
  await page.waitForLoadState("networkidle");
  const nameInput = page.getByRole("textbox", { name: "プロジェクト名" });
  await nameInput.fill(renamed);
  await expect(nameInput).toHaveValue(renamed);
  const saveName = page.getByRole("button", { name: "名前を保存" });
  await expect(saveName).toBeEnabled();
  await saveName.click();
  await page.locator(".project-list-item", { hasText: second.name }).click();
  await expect(page).toHaveURL(new RegExp(`project=${second.id}`));
  await expect(page.getByRole("textbox", { name: "プロジェクト名" })).toHaveValue(second.name);
  await expect(page.getByRole("textbox", { name: "プロジェクト名" })).toHaveValue(second.name);
  await expect.poll(() => routedStatus).not.toBe(0);
  expect(routedStatus, routedBody).toBe(200);
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBaseUrl}/api/projects/${first.id}`);
    return ((await response.json()) as { name: string }).name;
  }).toBe(renamed);
  expect(requestedName).toBe(renamed);

  await page.unrouteAll({ behavior: "wait" });
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${first.id}`)).status()).toBe(204);
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${second.id}`)).status()).toBe(204);
});

test("Project name and target drafts save independently", async ({ page }) => {
  const createdResponse = await createProjectFromDefault(page, `独立保存 ${Date.now()}`);
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json() as { id: string; name: string };
  await page.goto(`/?view=project&project=${created.id}`);
  await page.waitForLoadState("networkidle");

  const nameInput = page.getByRole("textbox", { name: "プロジェクト名" });
  const pendingName = `${created.name} 名前編集中`;
  await nameInput.fill(pendingName);
  const goal = page.getByRole("region", { name: "プロジェクトの目標値" });
  const targetInput = goal.getByRole("spinbutton").first();
  await targetInput.fill("500");
  await page.getByRole("button", { name: "設定", exact: true }).click();
  await page.getByRole("button", { name: "科学設定" }).click();
  await page.locator(".input-range-settings").getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBaseUrl}/api/projects/${created.id}`);
    return ((await response.json()) as { target_values: Record<string, unknown> }).target_values;
  }).toEqual({});
  await page.getByRole("button", { name: "概要", exact: true }).click();
  await expect(goal.getByRole("spinbutton").first()).toHaveValue("500");
  await page.getByRole("button", { name: "名前を保存" }).click();
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBaseUrl}/api/projects/${created.id}`);
    return (await response.json()) as { name: string; target_values: Record<string, unknown> };
  }).toMatchObject({ name: pendingName, target_values: {} });

  const secondPendingName = `${pendingName} まだ未保存`;
  await nameInput.fill(secondPendingName);
  await goal.getByRole("button", { name: "目標値を保存" }).click();
  await expect(nameInput).toHaveValue(secondPendingName);
  await expect(page.getByRole("button", { name: "名前を保存" })).toBeEnabled();
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBaseUrl}/api/projects/${created.id}`);
    return (await response.json()) as { name: string; target_values: Record<string, unknown> };
  }).toMatchObject({ name: pendingName });

  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
});

test("a delayed scientific settings save cannot replace the next Project", async ({ page }) => {
  const firstResponse = await createProjectFromDefault(page, `範囲保存元 ${Date.now()}`);
  const secondResponse = await createProjectFromDefault(page, `範囲切替先 ${Date.now()}`);
  const first = await firstResponse.json() as { id: string; name: string };
  const second = await secondResponse.json() as { id: string; name: string };
  await page.route("**/api/projects/**", async (route) => {
    if (route.request().method() !== "PUT" || new URL(route.request().url()).pathname !== `/api/projects/${first.id}`) {
      await route.continue();
      return;
    }
    const response = await page.request.put(route.request().url(), { data: route.request().postDataJSON() });
    await new Promise((resolve) => setTimeout(resolve, 750));
    await route.fulfill({ status: response.status(), headers: response.headers(), body: await response.body() });
  });

  await page.goto(`/?view=project-settings&project=${first.id}&project_settings=ranges`);
  await page.waitForLoadState("networkidle");
  const rangeSettings = page.locator(".input-range-settings");
  const minimum = rangeSettings.getByRole("spinbutton").first();
  await minimum.fill(String(Number(await minimum.inputValue()) + 0.0001));
  await rangeSettings.getByRole("button", { name: "保存", exact: true }).click();
  await page.locator(".project-list-item", { hasText: second.name }).click();
  await expect(page).toHaveURL(new RegExp(`project=${second.id}`));
  await expect(page.getByRole("heading", { name: `${second.name}の設定` })).toBeVisible();
  await page.waitForTimeout(900);
  await expect(page.getByRole("heading", { name: `${second.name}の設定` })).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${first.id}`)).status()).toBe(204);
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${second.id}`)).status()).toBe(204);
});

test("switching from single-Task scientific settings to a Chain normalizes the section", async ({ page }) => {
  const chainsResponse = await page.request.get(`${apiBaseUrl}/api/chains`);
  const chains = await chainsResponse.json() as Array<{
    definition: { chain_id: string };
    revisions: Array<{ revision: number; revision_digest: string }>;
  }>;
  const chain = chains.find((item) => item.definition.chain_id === "welding-consumable-a-b-c-v1")!;
  const revision = chain.revisions[0];
  const createdResponse = await page.request.post(`${apiBaseUrl}/api/projects`, { data: {
    name: `設定切替Chain ${Date.now()}`,
    scientific_identity: {
      identity_kind: "chain",
      chain_revision_id: `${chain.definition.chain_id}:r${revision.revision}`,
      chain_revision_digest: revision.revision_digest,
    },
  } });
  const createdBody = await createdResponse.text();
  expect(createdResponse.status(), createdBody).toBe(201);
  const created = JSON.parse(createdBody) as { id: string; name: string };

  await page.goto("/?view=project&project=default");
  await page.getByRole("button", { name: "設定", exact: true }).click();
  await page.getByRole("button", { name: "科学設定" }).click();
  await expect(page).toHaveURL(/project_settings=scientific/);
  await page.locator(".project-list-item", { hasText: created.name }).click();
  await expect(page).toHaveURL(new RegExp(`project=${created.id}.*project_settings=general`));
  await expect(page.getByRole("navigation", { name: "Project設定カテゴリ" }).getByRole("button", { name: "通常設定" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("region", { name: "プロジェクト設定" })).toBeVisible();
  await expect(page.getByRole("button", { name: "科学設定" })).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(/view=project-settings&project=default/);
  await expect(page.getByRole("button", { name: "通常設定" })).toHaveAttribute("aria-current", "page");
  await page.goBack();
  await expect(page).toHaveURL(/view=project&project=default/);
  await expect(page.getByRole("heading", { name: "次の作業" })).toBeVisible();
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
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
  await expect(page.getByRole("textbox", { name: "プロジェクト名" })).toHaveValue(/続き元/);
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
  const projectName = `アーカイブ位置確認 ${Date.now()}`;
  const createdResponse = await createProjectFromDefault(page, projectName);
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json() as { id: string };
  await page.goto(`/?view=project&project=${created.id}`);
  await expect(page.locator('.project-list-item[aria-current="page"]')).toContainText(projectName);
  await page.getByRole("button", { name: "設定", exact: true }).click();
  await expect(page).toHaveURL(/view=project-settings/);
  const evidenceSettings = page.getByRole("navigation", { name: "Project設定カテゴリ" })
    .getByRole("button", { name: "証拠・管理" });
  await evidenceSettings.click();
  await expect(page).toHaveURL(/project_settings=evidence/);
  await expect(evidenceSettings).toHaveAttribute("aria-current", "page");
  const content = page.locator(".project-hub-content");
  const archiveButton = content.getByRole("button", { name: "プロジェクトをアーカイブ" });
  await expect(archiveButton).toBeVisible();
  await expect(content.locator(":scope > :last-child")).toHaveClass(/project-danger-zone/);

  await expect(page.locator(".project-reference-strip")).toHaveCount(1);
  await expect(page.locator(".project-fixed-bindings")).toHaveCount(0);
  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
});

test("project hub separates current revision from fixed snapshot and restores a new candidate", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  await expect(page.getByRole("heading", { name: "次の作業" })).toBeVisible();
  await page.getByRole("button", { name: /条件範囲から候補を探す/ }).click();
  await expect(page).toHaveURL(/view=explore/);
  await expect(page.getByRole("heading", { name: /範囲探索/ })).toBeVisible();

  await page.getByRole("button", { name: "候補比較", exact: true }).click();
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await openCandidateInputs(page);
  const selectedRow = page.locator(".candidate-name-table tbody tr.selected-row");
  const candidateName = await selectedRow.getByRole("textbox").inputValue();
  const candidateId = new URL(page.url()).searchParams.get("candidate");
  expect(candidateId).toBeTruthy();
  const beforeRevision = await candidateRevision(page, candidateId!);

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
  await expect(card).toContainText(`編集版 ${beforeRevision + 1}`);
  await expect(card).toContainText(`編集版 ${beforeRevision}`);
  await expect(card.getByText("現在のpreview", { exact: true })).toBeVisible();
  await expect(card.getByText("固定した予測", { exact: true }).first()).toBeVisible();

  await page.route(/\/api\/projects\/[^/]+\/snapshots\/[^/]+$/, async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    const body = await response.json() as {
      payload: {
        prediction: {
          predictions: Record<string, { sampling_identity?: unknown }>;
        };
      };
    };
    const firstPrediction = Object.values(body.payload.prediction.predictions)[0];
    firstPrediction.sampling_identity = {
      schema_version: "sampling-identity/v1",
      runtime_type: "numpyro.dense_posterior.v1",
      method_id: "numpyro-posterior-predictive",
      method_version: "1.0.0",
      operation: "detailed_prediction",
      request_policy_id: "detailed-prediction/v1",
      request_policy_digest: `sha256:${"1".repeat(64)}`,
      seed: 17,
      requested_sample_count: 512,
      effective_sample_count: 512,
      posterior_draw_count: 1000,
      draw_selection_policy: "seeded_without_replacement",
      predictive_resampling_policy: "numpy-default-rng-likelihood/v1",
      aggregation_policy: "central-90-linear-quantiles/v1",
      approximation: null,
      fallback: null,
      parameter_digest: `sha256:${"2".repeat(64)}`,
    };
    await route.fulfill({ response, json: body });
  });
  await card.getByRole("button", { name: "詳細" }).first().click();
  const samplingDetail = page.locator(".sampling-identity-details");
  await expect(samplingDetail.getByText("17", { exact: true })).not.toBeVisible();
  await samplingDetail.locator("> summary").click();
  await expect(samplingDetail).toContainText("sampling-identity/v1");
  await expect(samplingDetail).toContainText("numpyro.dense_posterior.v1");
  await expect(samplingDetail).toContainText("numpyro-posterior-predictive / 1.0.0");
  await expect(samplingDetail).toContainText(`sha256:${"1".repeat(64)}`);
  await expect(samplingDetail).toContainText("requested 512 / effective 512");
  await expect(samplingDetail).toContainText("seeded_without_replacement");
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
  await expect(
    page.locator(".candidate-name-table tbody tr.selected-row").getByRole("textbox"),
  ).toHaveValue(/復元/);
});

test("new project creation requires an explicit empty or copy choice", async ({ page }) => {
  await gotoProjectAfterCreationOptions(page, "default");
  await page.getByRole("button", { name: "新規プロジェクト" }).click();
  const panel = page.getByRole("region", { name: "新規プロジェクトの開始方法" });
  await expect(panel.getByRole("radio", { name: /空から開始/ })).toBeVisible();
  await expect(panel.getByRole("radio", { name: /現在候補をコピー/ })).toBeVisible();
  await expect(panel.getByRole("radio", { name: /グループなし/ })).toBeChecked();
  await expect(panel.getByRole("radio", { name: /既存グループ/ })).toBeVisible();
  await expect(panel.getByRole("radio", { name: /新しい検討グループ/ })).toBeVisible();
  await panel.getByLabel("プロジェクト名").fill(`空の検討 ${Date.now()}`);
  const datasetSelect = panel.getByRole("combobox", { name: "Dataset", exact: true });
  const tutorialDatasetOption = datasetSelect.locator("option").filter({
    hasText: "material_workbench_tutorial_v2",
  }).first();
  await tutorialDatasetOption.waitFor({ state: "attached" });
  const tutorialDatasetValue = await tutorialDatasetOption.getAttribute("value");
  expect(tutorialDatasetValue).toBeTruthy();
  await datasetSelect.selectOption(tutorialDatasetValue!);
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
  await page.locator(".project-next-actions").getByRole("button", { name: /条件範囲から候補を探す/ }).click();
  await page.getByRole("button", { name: "最初の候補を作る" }).click();
  await page.getByRole("button", { name: "候補比較", exact: true }).click();
  await expect(page.locator(".comparison-prediction-table thead .decision-output-col").filter({ hasText: "引張強さ" })).toBeVisible();
  await expect(page.locator(".comparison-prediction-table thead .decision-output-col").filter({ hasText: "降伏強さ" })).toBeVisible();
});
