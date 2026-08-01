import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { apiBaseUrl as api, createProjectWithCandidate } from "./helpers";

async function runScreening(page: Page) {
  await page.locator(".screening-run-footer .primary-button").click();
}

async function openAdvancedSettings(page: Page) {
  const details = page.locator(".screening-advanced-settings");
  if (!(await details.getAttribute("open"))) {
    await details.locator("> summary").click();
  }
}

async function chooseLandscape(page: Page) {
  await page.locator(".screening-mode-options").getByRole("button", { name: /領域を見る/ }).click();
}

async function createProject(request: APIRequestContext, taskId: string) {
  return createProjectWithCandidate(
    request,
    taskId,
    `探索E2E ${taskId} ${Date.now()}`,
    "探索基準",
  );
}

test("screening variable editor stays compact, validates rows, and contains narrow-width scrolling", async ({ page, request }) => {
  const project = await createProject(request, "annealed-properties-v1");
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/?view=explore&project=${project.id}`);

  const editor = page.locator(".screening-variable-editor");
  const rows = editor.locator("tbody > tr:not(.screening-variable-error-row)");
  const addButton = editor.getByRole("button", { name: "変数を追加" });
  await expect(rows).toHaveCount(2);
  expect((await editor.boundingBox())!.height).toBeLessThan(190);

  for (let expected = 3; expected <= 10; expected += 1) {
    await addButton.click();
    await expect(rows).toHaveCount(expected);
    await expect(rows.nth(expected - 1).getByRole("combobox").first()).toBeFocused();
  }
  expect((await editor.boundingBox())!.height).toBeLessThan(540);
  const tableScroll = editor.locator(".screening-variable-table-scroll");
  await tableScroll.evaluate((element) => { element.scrollTop = 160; });
  const stickyOffset = await page.evaluate(() => {
    const scroller = document.querySelector(".screening-variable-table-scroll")!.getBoundingClientRect();
    const heading = document.querySelector(".variable-table thead th")!.getBoundingClientRect();
    return Math.abs(scroller.top - heading.top);
  });
  expect(stickyOffset).toBeLessThanOrEqual(2);

  const lastDelete = rows.last().getByRole("button", { name: /を削除/ });
  await lastDelete.focus();
  await lastDelete.press("Enter");
  await expect(rows).toHaveCount(9);
  await expect(rows.nth(8).getByRole("combobox").first()).toBeFocused();
  await addButton.click();
  await expect(rows).toHaveCount(10);

  await rows.first().getByRole("combobox").nth(1).selectOption("range");
  const firstMinimum = rows.first().getByRole("textbox").first();
  const firstMaximum = rows.first().getByRole("textbox").nth(1);
  await firstMinimum.fill("10");
  await firstMaximum.fill("5");
  await expect(page.getByRole("alert").filter({ hasText: "最小値は最大値より小さくしてください。" })).toBeVisible();
  await expect(page.locator(".screening-run-footer .primary-button")).toBeDisabled();
  await firstMinimum.fill("0.05");
  await firstMaximum.fill("0.1");
  await expect(page.getByRole("alert").filter({ hasText: "最小値は最大値より小さくしてください。" })).toHaveCount(0);
  await expect(page.locator(".screening-run-footer .primary-button")).toBeEnabled();

  await page.setViewportSize({ width: 900, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  expect((await editor.boundingBox())!.height).toBeLessThan(540);

  // A 900px window at 150% page zoom exposes a 600px CSS layout viewport.
  await page.setViewportSize({ width: 600, height: 600 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await expect(addButton).toBeVisible();

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(page.getByRole("button", { name: "選択候補の入力を開く" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  expect((await page.locator(".central-workspace").boundingBox())!.y).toBeLessThan(260);
  expect(await editor.locator(".screening-variable-table-scroll").evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
});

test("annealed screening keeps draft separate and batches multiple points into stock", async ({ page, request }) => {
  const project = await createProject(request, "annealed-properties-v1");
  await page.goto(`/?view=explore&project=${project.id}`);
  await expect(page.getByRole("heading", { name: "範囲探索" })).toBeVisible();
  await expect(page.getByText("まず、いま知りたいことを選びます。")).toHaveCount(0);
  await expect(page.locator(".screening-run-footer").getByRole("button", { name: "64点を評価" })).toBeVisible();
  const modes = page.locator(".screening-mode-options");
  await expect(modes.getByRole("button", { name: /領域を見る/ })).toBeVisible();
  await expect(modes.getByRole("button", { name: /領域を見る/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".screening-run-footer .primary-button")).toBeEnabled();
  await expect(modes.getByRole("button", { name: /実験バッチを組む/ })).toBeDisabled();
  await expect(page.locator(".screening-advanced-settings")).not.toHaveAttribute("open", "");
  await expect(page.getByLabel("乱数seed")).not.toBeVisible();
  await expect(page.locator("optgroup[label='成分']")).toHaveCount(2);
  await expect(page.locator("optgroup[label='焼鈍条件']")).toHaveCount(2);
  await expect(page.locator("optgroup[label='焼鈍履歴'] option[value='heat_pattern.1.temperature_c']")).toHaveCount(2);
  await modes.getByRole("button", { name: /有望候補を探す/ }).click();
  await page.getByLabel("提案件数").fill("2");

  await page.getByRole("button", { name: "変数を追加" }).click();
  const rows = page.locator(".variable-table tbody tr");
  await rows.nth(2).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(2).locator("input").nth(0).fill("0.8");
  await rows.nth(2).locator("input").nth(1).fill("2.0");
  await page.getByLabel(/主目標: .*の下限/).fill("500");
  await page.getByLabel("副条件: 降伏強さの下限").fill("350");

  const runRequest = page.waitForRequest((request) => request.method() === "POST" && new URL(request.url()).pathname === "/api/screening");
  const runResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening");
  await runScreening(page);
  expect((await runRequest).postDataJSON().proposal.proposal_count).toBe(2);
  const completedRunResponse = await runResponse;
  expect(completedRunResponse.status()).toBe(201);
  const completedRun = await completedRunResponse.json() as {
    points: Array<unknown>;
    proposal_diagnostics: { evaluated_count: number };
  };
  const countStages = page.locator(".screening-count-stage");
  await expect(countStages).toHaveCount(5);
  await expect(countStages.nth(0)).toHaveAttribute("title", "sampling planで生成した条件数");
  await expect(countStages.nth(1)).toHaveAttribute("title", "Design SpaceとTaskの制約を通過した条件数");
  await expect(countStages.nth(2)).toHaveAttribute("title", "予測modelで評価した条件数");
  await expect(countStages.nth(3)).toHaveAttribute("title", "chartとtableへ表示した条件数");
  await expect(countStages.nth(4)).toHaveAttribute("title", "候補確認へ提案した条件数");
  await expect(countStages.nth(4)).toHaveAttribute(
    "aria-describedby",
    "screening-count-proposed-description",
  );
  await countStages.nth(4).focus();
  await expect(countStages.nth(4)).toBeFocused();
  await expect(page.getByRole("heading", { name: "提案候補" })).toBeVisible();
  await expect(page.locator('input[aria-label^="点 "]:checked')).toHaveCount(2);
  await expect(page.getByRole("button", { name: "2件を候補へ追加" })).toBeEnabled();
  const firstProposalCheckbox = page.locator('input[aria-label^="点 "]').first();
  await firstProposalCheckbox.focus();
  await firstProposalCheckbox.press("Space");
  await expect(firstProposalCheckbox).not.toBeChecked();
  await firstProposalCheckbox.press("Space");
  await expect(firstProposalCheckbox).toBeChecked();
  const savedRun = page.locator(".saved-runs button.active");
  await expect(savedRun).not.toContainText(/model |space |objective |strategy /);
  await expect(page.locator(".saved-runs details")).toHaveCount(0);
  const reproducibility = page.locator(".screening-run-evidence");
  const resultsTable = page.locator(".screening-results-table");
  expect(await page.evaluate(() => {
    const table = document.querySelector(".screening-results-table");
    const evidence = document.querySelector(".screening-run-evidence");
    return Boolean(table && evidence && (table.compareDocumentPosition(evidence) & Node.DOCUMENT_POSITION_FOLLOWING));
  })).toBe(true);
  await page.getByRole("button", { name: "地図", exact: true }).click();
  await expect(page.locator(".screen-map-proposal-marker")).toHaveCount(2);
  await expect(page.locator(".screen-map-selection-ring")).toHaveCount(2);
  await expect(page.locator(".screening-interpolation-status")).toContainText("点表示");
  await expect(page.locator(".screen-map-evaluated-marker")).toHaveCount(
    completedRun.proposal_diagnostics.evaluated_count - completedRun.points.length,
  );
  await reproducibility.locator("> summary").click();
  await expect(reproducibility).toContainText("Model Package");
  await expect(reproducibility).toContainText("Design Space");
  await expect(page.locator(".screening-slice-context")).toContainText("Mn");
  await expect(page.locator(".screening-slice-context")).toContainText("固定断面ではありません");
  await expect(page.getByLabel("横軸")).toBeVisible();
  await expect(page.getByLabel("縦軸")).toBeVisible();
  await expect(page.locator(".screening-display-controls").getByLabel("色")).toBeVisible();
  await expect(page.getByRole("button", { name: "サンプルを引き直す" })).toBeVisible();
  const compactMapHeight = (await page.locator(".screen-map").boundingBox())!.height;
  expect(compactMapHeight).toBeLessThanOrEqual(351);
  await page.getByRole("button", { name: "図を拡大" }).click();
  await expect(page.locator(".screen-map")).toHaveClass(/expanded/);
  await expect.poll(async () => (await page.locator(".screen-map").boundingBox())!.height)
    .toBeGreaterThan(compactMapHeight);
  await page.getByRole("button", { name: "図を元の大きさに戻す" }).click();
  await expect(page.getByRole("region", { name: "選択した探索点の詳細" })).toContainText("引張強さ");
  await expect(page.getByRole("region", { name: "選択した探索点の詳細" })).toContainText("降伏強さ");
  await rows.nth(2).locator("input").nth(0).fill("0.9");
  await expect(page.getByText(/未実行の条件変更/)).toBeVisible();

  await page.getByLabel("選別する特性").selectOption("YS");
  await page.getByLabel(/主目標: .*の下限/).fill("400");
  const rerunRequest = page.waitForRequest((request) => request.method() === "POST" && new URL(request.url()).pathname === "/api/screening");
  const rerunResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening");
  await runScreening(page);
  const rerunPayload = (await rerunRequest).postDataJSON() as { target: string; secondary_goals: Record<string, unknown> };
  expect(rerunPayload.target).toBe("YS");
  // The primary target must not also be sent as a secondary goal.
  expect(rerunPayload.secondary_goals ?? {}).not.toHaveProperty("YS");
  expect((await rerunResponse).status()).toBe(201);
  await expect(page.getByText(/未実行の条件変更/)).toHaveCount(0);

  await expect(page.locator('input[aria-label^="点 "]:checked')).toHaveCount(2);
  const batchResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/candidates"));
  await page.getByRole("button", { name: "2件を候補へ追加" }).click();
  expect((await batchResponse).status()).toBe(201);
  await expect(page).toHaveURL(/view=explore/);
  const candidates = await (await request.get(`${api}/api/projects/${project.id}/candidates`)).json() as Array<{ provenance?: { source_kind: string; source_ref: { run_id?: string; point_index?: number } } }>;
  expect(candidates.filter((candidate) => candidate.provenance?.source_kind === "screening")).toHaveLength(2);

  await page.getByRole("button", { name: "候補比較へ" }).click();
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
});

test("range exploration uses the shared candidate table without leaving the screen", async ({ page }) => {
  await page.goto("/?view=explore&project=default");

  await expect(page.getByRole("heading", { name: /探索の基準候補/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "探索の基準候補と入力・予測" })).toBeVisible();
  await expect(page.getByLabel(/を比較の基準にする/)).toHaveCount(0);

  const candidateButtons = page.locator(".candidate-select-button");
  expect(await candidateButtons.count()).toBeGreaterThanOrEqual(3);
  const selectedLabels = await candidateButtons.evaluateAll((buttons) => (
    buttons.map((button) => button.getAttribute("aria-label")!.replace(/を選択$/, ""))
  ));
  await candidateButtons.nth(1).click();

  await expect(page).toHaveURL(/view=explore/);
  await expect(page).toHaveURL(/candidate=/);
  await expect(candidateButtons.nth(1)).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("heading", { name: selectedLabels[1], exact: true })).toBeVisible();

  await candidateButtons.nth(2).click();
  const selectedCandidateId = new URL(page.url()).searchParams.get("candidate");
  await page.locator(".screening-mode-options").getByRole("button", { name: /有望候補を探す/ }).click();
  await page.getByLabel(/主目標: .*の下限/).fill("500");
  const runRequest = page.waitForRequest((request) =>
    request.method() === "POST" && new URL(request.url()).pathname === "/api/screening"
  );
  const runResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening"
  );
  await page.locator(".screening-run-footer .primary-button").click();
  expect((await runRequest).postDataJSON().base_candidate_id).toBe(selectedCandidateId);
  expect((await runResponse).status()).toBe(201);
  await expect(page.locator(".screening-mode-options").getByRole("button", { name: /実験バッチを組む/ })).toBeEnabled();

  await candidateButtons.nth(0).click();
  await expect(page.getByText(/未実行の条件変更/)).toBeVisible();
  await expect(page.locator(".screening-mode-options").getByRole("button", { name: /実験バッチを組む/ })).toBeDisabled();
});

test("Proposal Lab compares aligned seeds and saves evidence without enabling production", async ({ page, request }) => {
  test.setTimeout(90_000);
  const candidateResponse = await request.get(`${api}/api/projects/default/candidates`);
  expect(candidateResponse.status()).toBe(200);
  const [candidate] = await candidateResponse.json() as Array<{ id: string; inputs: Record<string, unknown> }>;
  expect(candidate).toBeTruthy();

  for (const strategyId of ["sobol_ucb_v1", "sobol_ei_v1"]) {
    for (const seed of [31, 47]) {
      const proposal: Record<string, unknown> = {
        strategy_id: strategyId,
        exploration_parameter: 1.5,
        pool_multiplier: 2,
        support_policy: "supported_first",
        fallback_policy: "reject",
        incumbent_value: 500,
      };
      const response = await request.post(`${api}/api/screening?project_id=default`, {
        data: {
          purpose: "goal_search",
          base_candidate_id: candidate.id,
          base_inputs: candidate.inputs,
          samples: 48,
          seed,
          target: "TS",
          target_goal: { direction: "at_least", lower: 500 },
          variables: {
            "composition.C": { mode: "range", min: 0.04, max: 0.12 },
            "process.ls_mpm": { mode: "range", min: 80, max: 130 },
          },
          proposal,
        },
      });
      expect(response.status(), await response.text()).toBe(201);
    }
  }

  await page.goto("/?view=explore&project=default");
  const lab = page.locator(".proposal-lab");
  await lab.locator("> summary").click();
  await expect(lab.getByText("productionへ自動反映しない")).toBeVisible();
  await expect(lab.getByText("acquisition scoreは成功確率ではありません。")).toBeVisible();
  const runChecks = lab.locator(".proposal-lab-run-list input[type=checkbox]");
  await expect(runChecks).toHaveCount(4);
  for (let index = 0; index < 4; index += 1) await runChecks.nth(index).check();
  await expect(lab.getByText(/2 strategies · seed 31, 47/)).toBeVisible();
  const decisionSelects = lab.locator(".proposal-lab-decision select");
  await decisionSelects.nth(0).selectOption("sobol_ei_v1");
  await decisionSelects.nth(1).selectOption("no_adopt");
  await lab.getByLabel("根拠").fill("支持範囲内率を主基準に、現時点では採用しない");
  const reportResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/proposal-lab/reports")
  ));
  await lab.getByRole("button", { name: "評価記録を保存" }).click();
  expect((await reportResponse).status()).toBe(201);
  await expect(lab.getByText("sobol_ei_v1: 不採用")).toBeVisible();
  await expect(lab.getByText("この記録はregistryを変更していません。")).toBeVisible();

  await page.reload();
  await page.locator(".proposal-lab > summary").click();
  await expect(page.locator(".proposal-lab").getByText("sobol_ei_v1: 不採用")).toBeVisible();
  await page.setViewportSize({ width: 600, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  const resumedDecisionTarget = page.locator(".proposal-lab .proposal-lab-decision select").first();
  await resumedDecisionTarget.focus();
  await expect(resumedDecisionTarget).toBeFocused();
});

test("saved exploration can be inspected through evidence and deleted when unused", async ({ page, request }) => {
  const project = await createProject(request, "annealed-properties-v1");
  await page.goto(`/?view=explore&project=${project.id}`);
  await chooseLandscape(page);

  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/screening"
  ));
  await runScreening(page);
  const completedRunResponse = await runResponse;
  expect(completedRunResponse.status()).toBe(201);
  const completedRun = await completedRunResponse.json() as {
    points: Array<unknown>;
    proposal_diagnostics: { evaluated_count: number; proposed_count: number };
  };

  await expect(page.getByRole("button", {
    name: `提案候補 ${completedRun.proposal_diagnostics.proposed_count}`,
    exact: true,
  })).toBeDisabled();
  await expect(page.locator(".screening-interpolation-status")).toContainText("inverse_distance_weighted_display v1.0.0");
  expect(await page.locator(".screen-map-interpolation-cell").count()).toBeGreaterThan(100);
  await expect(page.locator(".screen-map-evaluated-marker")).toHaveCount(
    completedRun.proposal_diagnostics.evaluated_count - completedRun.points.length,
  );
  await expect(page.getByRole("region", { name: "選択した探索点の詳細" })).toContainText("動かした条件:");
  await expect(page.getByRole("region", { name: "選択した探索点の詳細" })).not.toContainText("全変動条件:");

  const evidenceButton = page.getByRole("button", { name: /実績を見る/ }).first();
  await expect(evidenceButton).toBeVisible();
  await evidenceButton.click();
  await expect(page.getByLabel("過去実績の根拠", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("過去実績の根拠", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /全評価点/ }).click();
  const table = page.locator(".screening-evaluated-table");
  await expect(table).toBeVisible();
  await expect(table.locator("tbody tr")).toHaveCount(100);
  await expect(page.getByRole("button", { name: /次の100件を表示/ })).toBeVisible();
  for (const width of [900, 600, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator(".screening-result-tabs")).toBeVisible();
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    )).toBe(true);
  }
  expect(await page.locator(".screening-evaluated-scroll").evaluate(
    (element) => element.scrollWidth > element.clientWidth,
  )).toBe(true);

  const savedRun = page.locator(".saved-run-item").first();
  await savedRun.getByRole("button", { name: "削除", exact: true }).click();
  await expect(savedRun.getByRole("button", { name: "削除する" })).toBeVisible();
  const deleteResponse = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
    && new URL(response.url()).pathname.startsWith("/api/screening/")
  ));
  await savedRun.getByRole("button", { name: "削除する" }).click();
  expect((await deleteResponse).status()).toBe(204);
  await expect(page.locator(".saved-run-item")).toHaveCount(0);
  await expect(page.locator(".screening-results-table")).toHaveCount(0);
  await expect(page.locator(".screening-evaluated-table")).toHaveCount(0);
});

test("hot rolling screening accepts task-defined process fields", async ({ page, request }) => {
  const project = await createProject(request, "hot-rolled-properties-v1");
  await page.goto(`/?view=explore&project=${project.id}`);
  await chooseLandscape(page);
  const rows = page.locator(".variable-table tbody tr");
  await rows.nth(0).getByRole("combobox").first().selectOption("process.soaking_temperature_c");
  await rows.nth(0).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(0).locator("input").nth(0).fill("1170");
  await rows.nth(0).locator("input").nth(1).fill("1190");
  await rows.nth(1).getByRole("combobox").first().selectOption("process.finish_temperature_c");
  await rows.nth(1).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(1).locator("input").nth(0).fill("850");
  await rows.nth(1).locator("input").nth(1).fill("930");

  const runResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening");
  await runScreening(page);
  const response = await runResponse;
  expect(response.status(), await response.text()).toBe(201);
  const body = await response.json() as { points: Array<{ inputs: Record<string, number | string>; predictions: Record<string, unknown> }> };
  expect(body.points[0].inputs["process.soaking_temperature_c"]).toBeGreaterThanOrEqual(1170);
  expect(body.points[0].inputs["process.finish_temperature_c"]).toBeGreaterThanOrEqual(850);
  expect(Object.keys(body.points[0].predictions)).toEqual(["TS"]);
  await expect(page.getByLabel("横軸")).toHaveValue("process.soaking_temperature_c");
});

test("a purpose-less legacy goal run reopens as opportunity search", async ({ page, request }) => {
  const project = await createProject(request, "annealed-properties-v1");
  await page.goto(`/?view=explore&project=${project.id}`);
  await page.locator(".screening-mode-options")
    .getByRole("button", { name: /有望候補を探す/ }).click();
  await page.getByLabel(/主目標: .*の下限/).fill("500");
  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/screening"
  ));
  await runScreening(page);
  const response = await runResponse;
  expect(response.status(), await response.text()).toBe(201);
  const current = await response.json() as Record<string, unknown> & {
    id: string;
    target_goal?: { lower?: number };
  };
  const legacy = {
    ...current,
    schema_version: "screening-run/v3",
    purpose: null,
    source_run_id: null,
    target_value: current.target_goal?.lower ?? 500,
    target_goal: null,
    secondary_goals: {},
    objective_definition: null,
    objective_definition_digest: null,
    objective_execution: null,
  };
  await page.route(`**/api/screening/${current.id}*`, async (route) => {
    await route.fulfill({ json: legacy });
  });
  await page.goto(`/?view=explore&project=${project.id}`);
  await page.locator(".saved-runs").getByRole("button").first().click();

  await expect(
    page.locator(".screening-mode-options")
      .getByRole("button", { name: /有望候補を探す/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("region", { name: "探索条件と提案診断" })).toBeVisible();
  await expect(page.getByRole("button", { name: "地図", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "提案候補 0", exact: true })).toBeDisabled();
  await expect(page.getByRole("region", { name: "探索領域の地図" })).toBeVisible();
  await expect(page.locator('input[aria-label^="点 "]')).toHaveCount(0);
});

test("concrete composition diversity proposals use the composition distance contract", async ({ page, request }) => {
  const project = await createProject(request, "concrete-strength-v1");
  await page.goto(`/?view=explore&project=${project.id}`);
  await page.locator(".screening-mode-options")
    .getByRole("button", { name: /有望候補を探す/ }).click();
  await page.getByLabel(/主目標: .*の下限/).fill("30");
  await openAdvancedSettings(page);

  const fields = [
    ["composition.cement_kg_m3", "200", "400"],
    ["composition.blast_furnace_slag_kg_m3", "10", "150"],
    ["composition.fly_ash_kg_m3", "10", "100"],
    ["composition.water_kg_m3", "140", "220"],
    ["composition.superplasticizer_kg_m3", "2", "20"],
    ["composition.coarse_aggregate_kg_m3", "850", "1100"],
    ["composition.fine_aggregate_kg_m3", "600", "900"],
  ] as const;
  const addButton = page.getByRole("button", { name: "変数を追加" });
  const rows = page.locator(".variable-table tbody tr");
  while (await rows.count() < fields.length) await addButton.click();
  for (const [index, [field, minimum, maximum]] of fields.entries()) {
    const row = rows.nth(index);
    await row.getByRole("combobox").first().selectOption(field);
    await row.getByRole("combobox").nth(1).selectOption("range");
    await row.getByRole("textbox").first().fill(minimum);
    await row.getByRole("textbox").nth(1).fill(maximum);
  }
  await page.getByLabel("提案の選び方").selectOption("greedy_value_diversity_v1");

  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/screening"
  ));
  await runScreening(page);
  const response = await runResponse;
  expect(response.status(), await response.text()).toBe(201);
  const run = await response.json() as {
    proposal_selection: {
      actual_count: number;
      distance_id: string;
      selected: Array<{ point_index: number }>;
    };
    proposal_diagnostics: { proposed_count: number };
  };
  expect(run.proposal_selection.actual_count).toBe(5);
  expect(run.proposal_selection.selected).toHaveLength(5);
  expect(run.proposal_selection.distance_id).toBe("group_weighted_bounded_clr_rms");
  expect(run.proposal_diagnostics.proposed_count).toBe(5);

  const summary = page.getByRole("region", { name: "探索条件と提案診断" });
  await expect(summary).toContainText("条件間の距離");
  await expect(summary).toContainText("組成bounded CLR-RMS + 入力群均等");
  await expect(page.getByRole("heading", { name: "提案候補" })).toBeVisible();
  await expect(page.locator('input[aria-label^="点 "]:checked')).toHaveCount(5);
  const resultTable = page.locator(".screening-results-table");
  await expect(resultTable.locator("tbody tr")).toHaveCount(5);
  await expect(resultTable.locator("thead")).toContainText("支持範囲");
  await expect(resultTable.locator("tbody")).toContainText("MPa");
  await page.screenshot({
    path: "output/playwright/issue-609-concrete-diversity.png",
    fullPage: true,
  });
});

test("bounded simplex display agrees with the persisted proposal evidence", async ({ page, request }) => {
  const project = await createProject(request, "mpea-hardness-process-v1");
  await page.goto(`/?view=explore&project=${project.id}`);
  await page.locator(".screening-mode-options")
    .getByRole("button", { name: /有望候補を探す/ }).click();

  await openAdvancedSettings(page);
  await page.getByLabel("候補の提案方法").selectOption("bounded_simplex_goal_v1");
  await page.getByLabel(/主目標: .*の下限/).fill("300");
  const rows = page.locator(".variable-table tbody tr");
  await rows.nth(0).getByRole("combobox").first().selectOption("composition.Ni");
  await rows.nth(0).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(0).locator("input").nth(0).fill("20");
  await rows.nth(0).locator("input").nth(1).fill("50");
  await rows.nth(1).getByRole("combobox").first().selectOption("composition.Co");
  await rows.nth(1).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(1).locator("input").nth(0).fill("20");
  await rows.nth(1).locator("input").nth(1).fill("50");
  const goalRunResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/screening"
  ));
  await runScreening(page);
  const goalResponse = await goalRunResponse;
  expect(goalResponse.status(), await goalResponse.text()).toBe(201);
  const goalRun = await goalResponse.json() as { id: string };
  const batchMode = page.locator(".screening-mode-options").getByRole("button", { name: /実験バッチを組む/ });
  await expect(batchMode).toBeEnabled();
  await batchMode.click();
  await expect(
    page.getByRole("region", { name: "探索条件と提案診断" })
      .getByRole("button", { name: "サンプルを引き直す" }),
  ).toHaveCount(0);
  await page.getByLabel("バッチ選抜").selectOption("ranked_top_k_v1");

  const batchRequest = page.waitForRequest((request) => (
    request.method() === "POST"
    && new URL(request.url()).pathname === "/api/screening"
  ));
  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/screening"
  ));
  await runScreening(page);
  const requestBody = (await batchRequest).postDataJSON() as { purpose: string; source_run_id: string };
  expect(requestBody).toMatchObject({
    purpose: "experiment_batch",
    source_run_id: goalRun.id,
  });
  const response = await runResponse;
  expect(response.status(), await response.text()).toBe(201);
  const run = await response.json() as {
    id: string;
    seed: number;
    model_provenance: Record<string, unknown>;
    proposal_strategy: {
      generator_id: string;
      generator_version: string;
      distance_id: string;
      distance_version: string;
    };
    proposal_diagnostics: { coverage_by_path: Record<string, unknown> };
    batch_proposal: { distance_id: string; selected: Array<{ order: number }> };
    purpose: string;
    source_run_id: string;
  };
  expect(run.purpose).toBe("experiment_batch");
  expect(run.source_run_id).toBe(goalRun.id);
  await expect(page.getByRole("button", {
    name: `実験バッチ ${run.batch_proposal.selected.length}`,
    exact: true,
  })).toHaveAttribute("aria-pressed", "true");
  const batchSurface = page.getByRole("region", { name: "実験バッチ", exact: true });
  await expect(batchSurface).toBeVisible();
  await expect(batchSurface.getByRole("heading", { name: "実験バッチ", exact: true })).toBeVisible();
  await expect(batchSurface.locator("tbody th").first()).toHaveText("1");
  await expect(batchSurface.getByRole("heading", { name: "提案候補", exact: true })).toHaveCount(0);

  const proposalSummary = page.getByRole("region", { name: "探索条件と提案診断" });
  await expect(proposalSummary.locator(".screening-proposal-headline")).toContainText(
    "硬さの有望点から実験バッチを選定",
  );
  await expect(proposalSummary.locator(".screening-proposal-headline")).not.toContainText(/seed|digest|bounded_simplex/);
  await page.getByText("判断根拠").click();
  await expect(page.getByRole("region", { name: "探索条件と提案診断" })).toContainText(
    "組成bounded CLR-RMS + 入力群均等",
  );
  await page.getByText("計算記録", { exact: true }).click();
  await expect(page.locator(".screening-run-evidence")).toContainText("bounded_simplex_goal_v1");
  await expect(page.locator(".screening-run-evidence")).toContainText("生成coverage:");
  await expect(page.locator(".screening-run-evidence")).toContainText("composition.Ni");

  const persistedResponse = await request.get(
    `${api}/api/screening/${run.id}?project_id=${project.id}`,
  );
  expect(persistedResponse.status(), await persistedResponse.text()).toBe(200);
  const persisted = await persistedResponse.json() as typeof run;
  expect(persisted.seed).toBe(run.seed);
  expect(persisted.model_provenance).toEqual(run.model_provenance);
  expect(run.proposal_strategy).toEqual(persisted.proposal_strategy);
  expect(run.batch_proposal).toEqual(persisted.batch_proposal);
  expect(run.proposal_strategy).toMatchObject({
    generator_id: "bounded_simplex_hit_and_run",
    generator_version: "1.0.0",
    distance_id: "group_weighted_bounded_clr_rms",
    distance_version: "1.0.0",
  });
  expect(run.batch_proposal.distance_id).toBe("group_weighted_bounded_clr_rms");
  expect(Object.keys(run.proposal_diagnostics.coverage_by_path)).toEqual(
    expect.arrayContaining(["composition.Ni", "composition.Co"]),
  );
});

test("a late proposal response cannot replace a newer run", async ({ page, request }) => {
  const project = await createProject(request, "hot-rolled-properties-v1");
  let requestCount = 0;
  await page.route("**/api/screening?project_id=*", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    requestCount += 1;
    const current = requestCount;
    const response = await route.fetch();
    if (current === 1) {
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
    await route.fulfill({ response });
  });
  await page.goto(`/?view=explore&project=${project.id}`);
  await chooseLandscape(page);
  await openAdvancedSettings(page);
  const rows = page.locator(".variable-table tbody tr");
  await rows.nth(0).getByRole("combobox").first().selectOption("process.soaking_temperature_c");
  await rows.nth(0).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(0).locator("input").nth(0).fill("1170");
  await rows.nth(0).locator("input").nth(1).fill("1190");

  await page.getByLabel("乱数seed").fill("101");
  await runScreening(page);
  await page.getByLabel("乱数seed").fill("202");
  await runScreening(page);

  await page.getByText("計算記録", { exact: true }).click();
  await expect(page.locator(".screening-run-evidence")).toContainText("seed 202");
  await page.waitForTimeout(900);
  await expect(page.locator(".screening-run-evidence")).toContainText("seed 202");
  expect(requestCount).toBe(2);
});
