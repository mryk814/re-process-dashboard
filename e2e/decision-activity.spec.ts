import { expect, test } from "@playwright/test";
import { apiBaseUrl, resolveProjectBinding, starterCandidate } from "./helpers";

type RobustnessReading = {
  prediction: number;
  variationWidth: number;
};

async function runRobustness(page: import("@playwright/test").Page): Promise<RobustnessReading> {
  const sensitivityOnly = page.getByRole("button", { name: "目標なしでばらつきだけ見る" });
  if (await sensitivityOnly.isVisible()) await sensitivityOnly.click();
  await page.getByRole("spinbutton", { name: "Cの公差幅" }).fill("0.01");
  await page.getByRole("spinbutton", { name: "サンプル数" }).fill("64");
  const runResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/decision-activities/robustness-analysis-v1/runs")
  ));
  await page.getByRole("button", { name: /公差内を解析|ばらつきを解析/ }).click();
  expect((await runResponse).status()).toBe(201);

  const target = page.locator(".activity-targets article").filter({ hasText: "降伏強さ" });
  await expect(target.getByText("入力ばらつき", { exact: true })).toBeVisible();
  const values = await target.locator("dd").allTextContents();
  const prediction = Number(values[0].match(/-?[\d.]+/)?.[0]);
  const bounds = values[1].match(/-?[\d.]+/g)?.map(Number) ?? [];
  expect(Number.isFinite(prediction)).toBe(true);
  expect(bounds).toHaveLength(2);
  return { prediction, variationWidth: bounds[1] - bounds[0] };
}

test("robustness activity distinguishes a higher average candidate from a steadier candidate", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  await page.getByRole("button", { name: "検討アクティビティ" }).click();
  await expect(page.getByRole("heading", { name: "ロバストネス／公差解析" })).toBeVisible();
  const higherAverage = await runRobustness(page);

  await page.getByRole("button", { name: "高強度案を選択" }).click();
  await expect(page.getByText("選択中: 高強度案")).toBeVisible();
  const steadier = await runRobustness(page);

  expect(higherAverage.prediction).toBeGreaterThan(steadier.prediction);
  expect(higherAverage.variationWidth).toBeGreaterThan(steadier.variationWidth);

  await page.getByRole("button", { name: "基準候補を選択" }).click();
  await expect(page.getByRole("navigation", { name: "保存済みロバストネス解析" })).toBeVisible();
  await expect(page.locator(".activity-result-meta")).toContainText("64/64件を評価");
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
  await page.getByRole("button", { name: "検討アクティビティ" }).click();
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
  await expect(page).toHaveURL(/view=project.*project_settings=targets/);
});

test("candidate difference activity attributes the gap and keeps an explicit residual", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  await page.getByRole("button", { name: "検討アクティビティ" }).click();
  const tabs = page.getByRole("navigation", { name: "検討アクティビティの選択" });
  await expect(tabs).toBeVisible();
  await tabs.getByRole("button", { name: "候補差分の要因分解" }).click();
  await expect(page.getByRole("heading", { name: "候補差分の要因分解" })).toBeVisible();

  await page.getByRole("combobox", { name: "比較候補" }).selectOption({ label: "高強度案" });
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
  await page.getByRole("button", { name: "検討アクティビティ" }).click();
  const tabs = page.getByRole("navigation", { name: "検討アクティビティの選択" });
  await tabs.getByRole("button", { name: "目標へ届く最小変更" }).click();
  await expect(page.getByRole("heading", { name: "目標へ届く最小変更" })).toBeVisible();

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
  await page.getByRole("button", { name: "検討アクティビティ" }).click();
  await page.getByRole("navigation", { name: "検討アクティビティの選択" })
    .getByRole("button", { name: "目標へ届く最小変更" }).click();
  await expect(page.getByRole("navigation", { name: "保存済み目標到達案" })).toBeVisible();
  await expect(page.locator(".counterfactual-proposal").first()).toBeVisible();
});
