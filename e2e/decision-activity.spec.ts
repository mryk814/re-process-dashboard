import { expect, test } from "@playwright/test";
import {
  apiBaseUrl,
  createProjectWithBinding,
  resolveProjectBinding,
  starterCandidate,
} from "./helpers";

type RobustnessReading = {
  prediction: number;
  variationWidth: number;
  runId: string;
};

async function openDecisionActivities(page: import("@playwright/test").Page) {
  const panel = page.locator(".decision-activity-panel");
  if (!(await panel.isVisible())) {
    await page.getByRole("navigation", { name: "プロジェクト内メニュー" })
      .getByRole("button", { name: "候補確認", exact: true }).click();
  }
  await expect(panel).toBeVisible();
}

async function runRobustness(page: import("@playwright/test").Page): Promise<RobustnessReading> {
  const sensitivityOnly = page.getByRole("button", { name: "目標なしでばらつきだけ見る" });
  const tolerance = page.getByRole("spinbutton", { name: "Cの公差幅" });
  await expect(sensitivityOnly.or(tolerance)).toBeVisible();
  if (await sensitivityOnly.isVisible()) await sensitivityOnly.click();
  await expect(tolerance).toBeVisible();
  await tolerance.fill("0.01");
  await page.getByRole("spinbutton", { name: "サンプル数" }).fill("64");
  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/decision-activities/robustness-analysis-v1/runs")
  ));
  await page.getByRole("button", { name: /公差内を解析|ばらつきを解析/ }).click();
  const response = await runResponse;
  expect(response.status()).toBe(201);
  const run = await response.json() as { id: string };

  const target = page.locator(".activity-targets article").filter({ hasText: "降伏強さ" });
  await expect(target.getByText("入力ばらつき", { exact: true })).toBeVisible();
  const values = await target.locator("dd").allTextContents();
  const prediction = Number(values[0].match(/-?[\d.]+/)?.[0]);
  const bounds = values[1].match(/-?[\d.]+/g)?.map(Number) ?? [];
  expect(Number.isFinite(prediction)).toBe(true);
  expect(bounds).toHaveLength(2);
  return { prediction, variationWidth: bounds[1] - bounds[0], runId: run.id };
}

test("robustness activity distinguishes a higher average candidate from a steadier candidate", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect(page.locator(".decision-activity-panel")).toHaveCount(0);
  await expect(page.getByRole("tablist", { name: "予測の見方" })).toBeVisible();
  const comparisonCandidateId = new URL(page.url()).searchParams.get("candidate");

  await openDecisionActivities(page);
  await expect(page).toHaveURL(/view=candidate-review/);
  await expect(page.getByRole("heading", { name: /確認する候補/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "入力ばらつきに強いか" })).toBeVisible();
  await expect(page.locator(".activity-context")).toContainText("基準候補");
  await expect(page.locator(".activity-context")).toContainText("ロバストネス／公差解析");
  const activityTop = await page.locator(".decision-activity-panel").evaluate((element) => element.getBoundingClientRect().top);
  const comparisonBottom = await page.getByRole("region", { name: "候補の入力と予測結果比較" }).evaluate((element) => element.getBoundingClientRect().bottom);
  expect(activityTop).toBeGreaterThanOrEqual(comparisonBottom);
  await expect(page.getByRole("tablist", { name: "予測の見方" })).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("candidate")).toBe(comparisonCandidateId);
  await page.getByRole("navigation", { name: "プロジェクト内メニュー" })
    .getByRole("button", { name: "候補比較", exact: true }).click();
  await expect(page.locator(".decision-activity-panel")).toHaveCount(0);
  await expect(page.getByRole("tablist", { name: "予測の見方" })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("candidate")).toBe(comparisonCandidateId);
  await openDecisionActivities(page);
  expect(new URL(page.url()).searchParams.get("candidate")).toBe(comparisonCandidateId);
  const steadier = await runRobustness(page);

  await page.getByRole("button", { name: "高強度案を選択" }).click();
  await expect(page).toHaveURL(/view=candidate-review/);
  await expect(page.getByText("選択中: 高強度案")).toBeVisible();
  await openDecisionActivities(page);
  const higherAverage = await runRobustness(page);

  expect(higherAverage.prediction).toBeGreaterThan(steadier.prediction);
  expect(higherAverage.variationWidth).toBeGreaterThan(steadier.variationWidth);

  await page.getByRole("button", { name: "基準候補を選択" }).click();
  await openDecisionActivities(page);
  await expect(page.getByRole("navigation", { name: "保存済みロバストネス解析" })).toBeVisible();
  await expect(page.locator(".activity-result-meta")).toContainText("64/64件を評価");
});

test("activity run deep links follow same-candidate history and reject an unknown run", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await openDecisionActivities(page);
  await expect(page.getByRole("heading", { name: "入力ばらつきに強いか" })).toBeVisible();

  const runA = await runRobustness(page);
  await expect(page.locator(".activity-result-meta")).toContainText("64/64件を評価");
  const runAHistory = page
    .getByRole("navigation", { name: "保存済みロバストネス解析" })
    .locator('button[aria-current="true"]');
  await expect(runAHistory).toHaveCount(1);
  await runAHistory.click();
  const runAUrl = page.url();
  expect(runAUrl).toContain(`activity_run=${runA.runId}`);

  await page.getByRole("navigation", { name: "検討アクティビティの選択" })
    .getByRole("button", { name: "入力ばらつきに強いか" }).click();
  await page.getByText("条件を変えて再実行", { exact: true }).click();
  await page.getByRole("spinbutton", { name: "Cの公差幅" }).fill("0.01");
  await page.getByRole("spinbutton", { name: "サンプル数" }).fill("128");
  const runBResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/decision-activities/robustness-analysis-v1/runs")
  ));
  await page.getByRole("button", { name: /公差内を解析|ばらつきを解析/ }).click();
  const runB = await (await runBResponse).json() as { id: string };
  await expect(page.locator(".activity-result-meta")).toContainText("128/128件を評価");
  const runBHistory = page
    .getByRole("navigation", { name: "保存済みロバストネス解析" })
    .locator('button[aria-current="true"]');
  await expect(runBHistory).toHaveCount(1);
  await runBHistory.click();
  const runBUrl = page.url();
  expect(runBUrl).toContain(`activity_run=${runB.id}`);
  expect(runBUrl).not.toBe(runAUrl);

  await page.evaluate((url) => {
    window.history.replaceState({}, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, runAUrl);
  await expect(page.locator(".activity-result-meta")).toContainText("64/64件を評価");
  await page.reload();
  await expect(page.locator(".activity-result-meta")).toContainText("64/64件を評価");
  await page.getByRole("navigation", { name: "保存済みロバストネス解析" })
    .getByRole("button", { name: /最新結果/ }).click();
  await expect(page).toHaveURL(runBUrl);
  await expect(page.locator(".activity-result-meta")).toContainText("128/128件を評価");
  await page.reload();
  await expect(page.locator(".activity-result-meta")).toContainText("128/128件を評価");
  await expect(page.getByText("入力のばらつきで予測がどの程度動くか", { exact: true })).toBeVisible();
  const questionPrecedesEvidence = await page.evaluate(() => {
    const question = document.querySelector(".activity-question");
    const evidence = document.querySelector(".activity-result");
    return Boolean(
      question
      && evidence
      && (question.compareDocumentPosition(evidence) & Node.DOCUMENT_POSITION_FOLLOWING),
    );
  });
  expect(questionPrecedesEvidence).toBe(true);
  await expect(page.getByText("条件を変えて再実行", { exact: true })).toBeVisible();
  const currentEvidence = await page.locator(".activity-result").boundingBox();
  const history = await page.getByRole("navigation", { name: "保存済みロバストネス解析" }).boundingBox();
  const controls = await page.locator(".activity-run-controls").boundingBox();
  expect(currentEvidence).not.toBeNull();
  expect(history).not.toBeNull();
  expect(controls).not.toBeNull();
  expect(currentEvidence!.y).toBeLessThan(history!.y);
  expect(history!.y).toBeLessThan(controls!.y);

  await page.evaluate((url) => {
    window.history.replaceState({}, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, runAUrl);
  await expect(page.locator(".activity-result-meta")).toContainText("64/64件を評価");
  await page.evaluate((url) => {
    window.history.pushState({}, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, runBUrl);
  await expect(page.locator(".activity-result-meta")).toContainText("128/128件を評価");

  const unknownUrl = new URL(page.url());
  unknownUrl.searchParams.set("activity_run", "activity-does-not-exist");
  await page.evaluate((url) => {
    window.history.pushState({}, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, unknownUrl.toString());
  await expect(page.getByRole("alert")).toContainText("この候補では見つかりません");
  await expect(page.locator(".activity-result")).toHaveCount(0);

  await page.getByRole("button", { name: "高強度案を選択" }).click();
  await expect(page.getByText("選択中: 高強度案")).toBeVisible();
  await expect(page).not.toHaveURL(/activity_run=/);
  await expect(page.locator(".activity-result")).toHaveCount(0);
  await expect(page.getByText("目標達成率を確認するには、Projectの目標値が必要です")).toBeVisible();
});

test("a delayed activity response cannot overwrite the newly selected candidate", async ({ page, request }) => {
  const project = await createProjectWithBinding(
    request,
    "annealed-properties-v1",
    `delayed activity ${Date.now()}`,
  );
  const starter = await starterCandidate(request, "annealed-properties-v1");
  const baseResponse = await request.post(`${apiBaseUrl}/api/projects/${project.id}/candidates`, {
    data: { ...starter, name: "遅延元候補" },
  });
  expect(baseResponse.status(), await baseResponse.text()).toBe(201);
  const baseCandidate = await baseResponse.json() as { id: string };
  const nextResponse = await request.post(`${apiBaseUrl}/api/projects/${project.id}/candidates`, {
    data: { ...starter, name: "切替先候補" },
  });
  expect(nextResponse.status(), await nextResponse.text()).toBe(201);

  await page.goto(`/?view=candidates&project=${project.id}&candidate=${baseCandidate.id}`);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await openDecisionActivities(page);
  await expect(page.getByRole("heading", { name: "入力ばらつきに強いか" })).toBeVisible();
  await runRobustness(page);
  await expect(page.locator(".activity-result")).toBeVisible();
  await page.getByRole("navigation", { name: "プロジェクト内メニュー" })
    .getByRole("button", { name: "候補比較", exact: true }).click();

  let releaseOldResponses!: () => void;
  const oldResponsesReleased = new Promise<void>((resolve) => {
    releaseOldResponses = resolve;
  });
  let delayedRequests = 0;
  let bothOldRequestsStarted!: () => void;
  const oldRequestsStarted = new Promise<void>((resolve) => {
    bothOldRequestsStarted = resolve;
  });

  await page.route(`**/api/projects/${project.id}/decision-activit**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("candidate_id") === baseCandidate.id) {
      delayedRequests += 1;
      if (delayedRequests === 2) bothOldRequestsStarted();
      const response = await route.fetch();
      await oldResponsesReleased;
      await route.fulfill({ response });
      return;
    }
    await route.continue();
  });

  await openDecisionActivities(page);
  await oldRequestsStarted;
  await page.getByRole("button", { name: "切替先候補を選択" }).click();
  await expect(page.getByText("選択中: 切替先候補")).toBeVisible();
  await expect(page.locator(".activity-result")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "入力ばらつきに強いか" })).toBeVisible();
  releaseOldResponses();

  await expect(page.getByText("選択中: 切替先候補")).toBeVisible();
  await expect(page.locator(".activity-result")).toHaveCount(0);
});

test("goal-less robustness explains the prerequisite and offers sensitivity-only analysis", async ({ page, request }) => {
  const binding = await resolveProjectBinding(request, "annealed-properties-v1");
  const projectResponse = await request.post(`${apiBaseUrl}/api/projects`, {
    data: { name: "目標未設定ロバストネスE2E", ...binding },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as { id: string };
  const starter = await starterCandidate(request, "annealed-properties-v1");
  const candidateResponse = await request.post(`${apiBaseUrl}/api/projects/${project.id}/candidates`, {
    data: { ...starter, name: "基準候補" },
  });
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);

  await page.goto(`/?view=candidates&project=${project.id}`);
  await openDecisionActivities(page);
  await expect(page.getByText("目標達成率を確認するには、Projectの目標値が必要です")).toBeVisible();
  await expect(page.getByRole("button", { name: "目標を設定する" })).toBeVisible();
  await expect(page.locator(".activity-settings")).toHaveCount(0);

  await page.getByRole("button", { name: "目標なしでばらつきだけ見る" }).click();
  await expect(page.getByText("入力のばらつきで予測がどの程度動くか")).toBeVisible();
  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/decision-activities/robustness-analysis-v1/runs")
  ));
  await page.getByRole("button", { name: "ばらつきを解析" }).click();
  expect((await runResponse).status()).toBe(201);
  await expect(
    page.getByText("この解析の実行時に、この特性の目標が未設定だったため達成率は算出していません。").first(),
  ).toBeVisible();

  await page.getByRole("button", { name: "目標を設定する" }).click();
  await expect(page).toHaveURL(/view=project-settings.*project_settings=targets/);
});

test("candidate difference activity attributes the gap and keeps an explicit residual", async ({ page, request }) => {
  const candidatesResponse = await request.get(`${apiBaseUrl}/api/projects/default/candidates`);
  expect(candidatesResponse.status(), await candidatesResponse.text()).toBe(200);
  const candidates = await candidatesResponse.json() as Array<{
    id: string;
    name: string;
    revision: number;
  }>;
  const comparison = candidates.find((candidate) => candidate.name === "高強度案");
  expect(comparison, "高強度案の比較候補").toBeTruthy();

  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  await openDecisionActivities(page);
  const tabs = page.getByRole("navigation", { name: "検討アクティビティの選択" });
  await expect(tabs).toBeVisible();
  await tabs.getByRole("button", { name: "2案の差は何が効いているか" }).click();
  await expect(page.getByRole("heading", { name: "2案の差は何が効いているか" })).toBeVisible();

  await page.getByRole("combobox", { name: "比較候補" }).selectOption(
    `${comparison!.id}@${comparison!.revision}`,
  );
  const comparisonControl = await page.getByRole("combobox", { name: "比較候補" }).boundingBox();
  const differenceAction = await page.getByRole("button", { name: "差分を分解" }).boundingBox();
  expect(comparisonControl).not.toBeNull();
  expect(differenceAction).not.toBeNull();
  expect(comparisonControl!.x + comparisonControl!.width).toBeLessThanOrEqual(differenceAction!.x);

  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/decision-activities/candidate-difference-v1/runs")
  ));
  await page.getByRole("button", { name: "差分を分解" }).click();
  expect((await runResponse).status()).toBe(201);

  const target = page.locator(".activity-targets article").filter({ hasText: "降伏強さ" });
  await expect(target.getByText("置換で説明できた分", { exact: true })).toBeVisible();
  await expect(target.getByText("残差（交互作用）", { exact: true })).toBeVisible();
  await expect(page.getByText("入力の相違", { exact: false })).toBeVisible();
  await expect(page.locator(".activity-warnings")).toContainText("因果効果ではありません");
  const resultCards = page.locator(".activity-targets article");
  expect(await resultCards.count()).toBeGreaterThanOrEqual(2);
  const firstCard = await resultCards.nth(0).boundingBox();
  const secondCard = await resultCards.nth(1).boundingBox();
  expect(firstCard).not.toBeNull();
  expect(secondCard).not.toBeNull();
  expect(Math.abs(firstCard!.y - secondCard!.y)).toBeLessThan(2);
});

test("Decision Activity controls reflow without page-level overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await openDecisionActivities(page);
  await page.getByRole("navigation", { name: "検討アクティビティの選択" })
    .getByRole("button", { name: "2案の差は何が効いているか" }).click();

  const comparisonControl = await page.getByRole("combobox", { name: "比較候補" }).boundingBox();
  const differenceAction = await page.getByRole("button", { name: "差分を分解" }).boundingBox();
  expect(comparisonControl).not.toBeNull();
  expect(differenceAction).not.toBeNull();
  expect(differenceAction!.y).toBeGreaterThan(comparisonControl!.y);

  const width = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(width.scrollWidth).toBeLessThanOrEqual(width.clientWidth);
});

test("counterfactual activity runs, compares, promotes one proposal and reloads it", async ({ page, request }) => {
  const binding = await resolveProjectBinding(request, "annealed-properties-v1");
  const projectResponse = await request.post(`${apiBaseUrl}/api/projects`, {
    data: { name: "反実仮想E2E", ...binding },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as { id: string };
  const starter = await starterCandidate(request, "annealed-properties-v1");
  const candidateResponse = await request.post(`${apiBaseUrl}/api/projects/${project.id}/candidates`, {
    data: { ...starter, name: "基準候補" },
  });
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
  const candidate = await candidateResponse.json() as { id: string; revision: number };
  const previewResponse = await request.post(
    `${apiBaseUrl}/api/projects/${project.id}/candidates/${candidate.id}/preview?expected_revision=${candidate.revision}`,
  );
  expect(previewResponse.status(), await previewResponse.text()).toBe(200);
  const preview = await previewResponse.json() as { predictions: { TS: { value: number } } };
  const projectUpdate = await request.put(`${apiBaseUrl}/api/projects/${project.id}`, {
    data: {
      name: "反実仮想E2E",
      target_values: { TS: preview.predictions.TS.value + 2 },
    },
  });
  expect(projectUpdate.status(), await projectUpdate.text()).toBe(200);

  await page.goto(`/?view=candidates&project=${project.id}`);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await openDecisionActivities(page);
  const tabs = page.getByRole("navigation", { name: "検討アクティビティの選択" });
  await tabs.getByRole("button", { name: "目標へ届くには何を変えるか" }).click();
  await expect(page.getByRole("heading", { name: "目標へ届くには何を変えるか" })).toBeVisible();

  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/decision-activities/counterfactual-target-reach-v1/runs")
  ));
  await page.getByRole("button", { name: "最小変更案を探す" }).click();
  expect((await runResponse).status()).toBe(201);
  await expect(page.locator(".counterfactual-proposal").first()).toBeVisible();
  await expect(page.locator(".counterfactual-proposal").first()).toContainText("変更量");
  await expect(page.locator(".counterfactual-proposal").first()).toContainText("✓ 点予測で目標条件を満たす");
  await expect(page.locator(".counterfactual-targets > span").first()).toContainText("✓ 達成");
  await expect(page.locator(".counterfactual-targets > span").first()).toContainText("予測区間");
  await expect(page.locator(".counterfactual-targets > span").first()).toHaveAccessibleName(/点予測.+達成.+予測区間/);

  const promoteResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.includes("/decision-activity-runs/")
    && new URL(response.url()).pathname.endsWith("/candidate")
  ));
  await page.getByRole("button", { name: "この案を候補に追加" }).first().click();
  expect((await promoteResponse).status()).toBe(201);
  await expect(page.getByText("選択中: 目標到達案 1")).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: "基準候補を選択" }).click();
  await openDecisionActivities(page);
  await page.getByRole("navigation", { name: "検討アクティビティの選択" })
    .getByRole("button", { name: "目標へ届くには何を変えるか" }).click();
  await expect(page.getByRole("navigation", { name: "保存済み目標到達案" })).toBeVisible();
  await expect(page.locator(".counterfactual-proposal").first()).toBeVisible();
});
