import { expect, test } from "@playwright/test";

test("Model Library explains Student-t tails, Data Quality, and predictive intervals", async ({ page }) => {
  test.setTimeout(60_000);
  await page.route("**/api/model-library", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    const first = payload.packages?.[0];
    if (first) {
      first.predictor_families = first.predictor_families.map((predictor: Record<string, unknown>) => ({
        ...predictor,
        runtime_type: "numpyro.dense_posterior.v1",
        predictive_family: "student_t",
      }));
    }
    await route.fulfill({ response, json: payload });
  });

  const catalogResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/model-library"
  ));
  await page.goto("/?view=model-library&asset=packages");
  expect((await catalogResponse).status()).toBe(200);
  const studentPackage = page.locator(".model-asset-card").first();
  await expect(studentPackage).toContainText("Student-t heavy-tail likelihood");
  await expect(studentPackage).toContainText("入力ミスや単位不整合を許容しません");
  await expect(studentPackage).toContainText("dfは2.1–30に制約したposterior");
  await expect(studentPackage).toContainText("新観測の事後予測区間");
  await expect(studentPackage).toContainText("潜在平均の信用区間ではありません");
});

test("Model Library states learned quantile semantics and crossing policy", async ({ page }) => {
  test.setTimeout(60_000);
  await page.route("**/api/model-library", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    const first = payload.packages?.[0];
    if (first) {
      first.predictor_families = first.predictor_families.map((predictor: Record<string, unknown>) => ({
        ...predictor,
        runtime_type: "builtin.quantile_linear.v1",
        predictive_family: "empirical_quantiles",
      }));
    }
    await route.fulfill({ response, json: payload });
  });

  const catalogResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/model-library"
  ));
  await page.goto("/?view=model-library&asset=packages");
  expect((await catalogResponse).status()).toBe(200);
  const quantilePackage = page.locator(".model-asset-card").first();
  await expect(quantilePackage).toContainText("中央値とq05／q95を直接学習");
  await expect(quantilePackage).toContainText("正規分布の90%区間ではありません");
  await expect(quantilePackage).toContainText("分位点交差は補正せず利用不能");
});

test("Model Library compares assets and hands off without changing them", async ({ page }) => {
  test.setTimeout(60_000);
  const catalogResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/model-library"
  ));
  await page.goto("/?view=model-library&asset=packages");
  expect((await catalogResponse).status()).toBe(200);

  await expect(page.getByRole("heading", { name: "モデル資産を確認する" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Model Library" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("tab", { name: /Model Package/ })).toHaveAttribute("aria-selected", "true");

  const availablePackage = page.locator(".model-asset-card").filter({
    has: page.getByRole("button", { name: "Projectを作成", exact: true }),
  }).filter({
    has: page.getByText("利用可能", { exact: true }),
  }).first();
  await availablePackage.getByText("Pipeline・検証・固定参照").click();
  await expect(availablePackage.getByText(/Quality summary/)).toBeVisible();
  await availablePackage.getByRole("button", { name: "Projectを作成", exact: true }).click();
  await expect(page).toHaveURL(/view=project.*model_project_kind=single_task/);
  await expect(page).toHaveURL(/model_dataset_view=.+model_dataset_revision=.+model_task=.+model_package=.+model_package_digest=.+/);
  await expect(page.getByRole("heading", { name: "新しいプロジェクト" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: /Dataset/ })).not.toHaveValue("");
  await expect(page.getByRole("combobox", { name: /予測構成/ })).not.toHaveValue("");
  await expect(page.getByRole("combobox", { name: /Model Package/ })).not.toHaveValue("");
  const selectedDatasetView = await page.getByRole("combobox", { name: /Dataset/ }).inputValue();
  const selectedPrediction = await page.getByRole("combobox", { name: /予測構成/ }).inputValue();
  const selectedPackage = await page.getByRole("combobox", { name: /Model Package/ }).inputValue();

  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=packages/);
  await page.goForward();
  await expect(page).toHaveURL(/view=project.*model_project_kind=single_task/);
  await expect(page.getByRole("heading", { name: "新しいプロジェクト" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("combobox", { name: /Dataset/ })).toHaveValue(selectedDatasetView, { timeout: 15_000 });
  await expect(page.getByRole("combobox", { name: /予測構成/ })).toHaveValue(selectedPrediction, { timeout: 15_000 });
  await expect(page.getByRole("combobox", { name: /Model Package/ })).toHaveValue(selectedPackage, { timeout: 15_000 });
  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=packages/);
  await page.getByRole("tab", { name: /Prediction Graph/ }).click();
  await expect(page).toHaveURL(/view=model-library.*asset=graphs/);
  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=packages/);
  await page.goForward();
  await expect(page).toHaveURL(/view=model-library.*asset=graphs/);
  const graph = page.locator(".model-graph-card").first();
  await graph.getByText(/件の固定Revision/).click();
  await expect(graph.getByText(/Branch layers:/)).toBeVisible();
  await expect(graph.getByRole("heading", { name: "Stages / fixed references" })).toBeVisible();
  await expect(graph.getByRole("heading", { name: "Decision outputs" })).toBeVisible();
  await expect(graph.getByText(/Dataset View Revision:/).first()).toBeVisible();
  await expect(graph.getByText(/Project refs:/).first()).toBeVisible();

  const projectRevision = page.getByRole("button", { name: "このRevisionでProjectを作成" }).filter({ visible: true }).and(page.locator(":not([disabled])")).first();
  await projectRevision.click();
  await expect(page).toHaveURL(/view=project.*model_project_kind=graph.*model_graph=.+model_definition=.+model_revision=.+model_revision_digest=.+/);
  await expect(page.getByRole("heading", { name: "新しいプロジェクト" })).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=graphs/);
  let draftDefinition = page.locator("details.model-graph-detail:has(button.primary-button:not([disabled]))").first();
  await draftDefinition.locator("summary").click();
  await expect(draftDefinition.getByText(/複製元のevidenceを保持/)).toBeVisible();
  await draftDefinition.locator(".model-graph-revision-actions button:not([disabled])").first().click();
  await expect(page).toHaveURL(/view=project.*model_project_kind=graph/);
  await expect(page.getByText("Immutable Revision", { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=graphs/);
  draftDefinition = page.locator("details.model-graph-detail:has(button.primary-button:not([disabled]))").first();
  await draftDefinition.locator("summary").click();
  await draftDefinition.getByRole("button", { name: "Studioで新しいRevisionを作成" }).click();
  await expect(page).toHaveURL(/view=chain-studio.*draft=.+/);
  await expect(page.getByRole("heading", { name: "入力・Model・判断出力を直接つなぐ" })).toBeVisible();
  await expect(page.locator(".chain-studio-draft-bar")).toBeVisible();
  await expect(page.getByRole("button", { name: "Model Library" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("button", { name: "Chain Studio" })).toHaveCount(0);
});

test("Model Library and Data Library preserve Dataset context bidirectionally", async ({ page }) => {
  const catalogResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/model-library"
  ));
  await page.goto("/?view=model-library&asset=tasks");
  expect((await catalogResponse).status()).toBe(200);

  const datasetsResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/data-library/datasets"
  ));
  await page.locator(".model-asset-card").first().getByRole("button", { name: "対応データを確認" }).click();
  await expect(page).toHaveURL(/view=data-library.*focus_dataset_revision=.+focus_package=.+/);
  expect((await datasetsResponse).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "データライブラリ" })).toBeVisible();
  const reloadedCatalogResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/model-library"
  ));
  await page.getByRole("button", { name: "利用中のモデル資産を見る" }).click();
  await expect(page).toHaveURL(/view=model-library.*asset=packages.*focus_dataset_revision=.+/);
  expect((await reloadedCatalogResponse).status()).toBe(200);
  await expect(page.getByRole("status")).toContainText(/Package \d+件 \/ Graph \d+件/);
});
