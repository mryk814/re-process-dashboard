import { expect, test } from "@playwright/test";

test("Model Library compares assets and hands off without changing them", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/?view=model-library&asset=packages");

  await expect(page.getByRole("heading", { name: "モデル資産を確認する" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Model Library" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("tab", { name: /Model Package/ })).toHaveAttribute("aria-selected", "true");

  const availablePackage = page.locator(".model-asset-card").filter({
    has: page.getByRole("button", { name: "Projectを作成", exact: true }),
  }).filter({
    has: page.getByText("利用可能", { exact: true }),
  }).first();
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
  await expect(page.getByRole("heading", { name: "新しいプロジェクト" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: /Dataset/ })).toHaveValue(selectedDatasetView);
  await expect(page.getByRole("combobox", { name: /予測構成/ })).toHaveValue(selectedPrediction);
  await expect(page.getByRole("combobox", { name: /Model Package/ })).toHaveValue(selectedPackage);
  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=packages/);
  await page.getByRole("tab", { name: /Prediction Graph/ }).click();
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
  await expect(page.getByRole("combobox", { name: "Chain Revision" })).not.toHaveValue("");

  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=graphs/);
  const predictionGraphDefinition = page.locator(
    "details.model-graph-detail:has(button.primary-button:disabled):has(.model-graph-revision-actions button:not([disabled]))",
  ).first();
  if (await predictionGraphDefinition.count()) {
    await predictionGraphDefinition.locator("summary").click();
    await predictionGraphDefinition.locator(".model-graph-revision-actions button:not([disabled])").first().click();
    await expect(page).toHaveURL(/view=project.*model_project_kind=graph/);
    await expect(page.getByText("Immutable Revision", { exact: true })).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/view=model-library.*asset=graphs/);
  }
  const cloneDefinition = page.locator("details.model-graph-detail:has(button.primary-button:not([disabled]))").first();
  await cloneDefinition.locator("summary").click();
  const cloneButton = cloneDefinition.getByRole("button", { name: "Studioで新しいRevisionを作成" });
  await cloneButton.click();
  await expect(page).toHaveURL(/view=chain-studio.*clone_graph=.+clone_definition=.+clone_revision=.+/);
  await expect(page.getByRole("heading", { name: "予測Taskを固定したChainとして公開する" })).toBeVisible();
  await expect(page.getByText(/を新しいdraftへ複製しました/)).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(/view=model-library.*asset=graphs/);
  await page.getByRole("tab", { name: /Prediction Task/ }).click();
  await page.locator(".model-asset-card").first().getByRole("button", { name: "対応データを確認" }).click();
  await expect(page).toHaveURL(/view=data-library.*focus_dataset_revision=.+focus_package=.+/);
  await expect(page.getByRole("heading", { name: "データライブラリ" })).toBeVisible();
});
