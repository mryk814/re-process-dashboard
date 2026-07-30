import { expect, test } from "@playwright/test";

test("Data Library keeps models in the selected dataset context", async ({ page }) => {
  await page.goto("/?view=data-library");

  await expect(page.getByRole("heading", { name: "使うデータを選ぶ" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "自分のデータ" })).toBeVisible();
  await expect(page.getByText(/同梱サンプル/).first()).toBeVisible();
  const bundledSamples = page.locator("details.bundled-dataset-group");
  await expect(bundledSamples).not.toHaveAttribute("open", "");
  await expect(page.getByRole("button", { name: /material_workbench_tutorial_v2.*詳細を表示/ })).not.toBeVisible();

  const selectedDataset = page.locator(".dataset-context");
  await expect(selectedDataset.getByRole("heading", { name: /material_workbench_tutorial_v2\.xlsx/ })).toBeVisible();
  await expect(selectedDataset.getByRole("heading", { name: "このデータで使うモデル" })).toBeVisible();
  await expect(selectedDataset.getByText("GP（安定ARD） · v2.1.0-stable-ard", { exact: true })).toBeVisible();
  await expect(selectedDataset.getByText("同梱モデル", { exact: true }).first()).toBeVisible();

  await selectedDataset.getByRole("button", { name: "このデータでモデルを更新" }).click();
  const guide = page.getByRole("region", { name: "モデルを追加する" });
  const commands = guide.getByRole("textbox", { name: "PowerShellモデル更新手順" });
  await expect(commands).toHaveValue(
    /npm run model:diagnose[\s\S]*npm run model:build[\s\S]*npm run model:promote/,
  );
  await expect(commands).toHaveValue(
    /\$datasetOutput = "artifacts\/model-data\/\$packageId\.json"[\s\S]*--dataset-output \$datasetOutput/,
  );
  await expect(commands).toHaveValue(
    /\$modelStore = if \(\$env:WORKBENCH_MODEL_STORE_PATH\)[\s\S]*--store \$modelStore/,
  );
  await expect(commands).toHaveValue(
    /\$profile = [^\r\n]+[\s\S]*model:diagnose[^\r\n]+--profile \$profile[\s\S]*model:build[\s\S]+--profile \$profile[\s\S]*model:promote[\s\S]+--profile \$profile/,
  );
  await expect(commands).not.toHaveValue(/--activate/);
  await expect(commands).not.toHaveValue(/npm run dev/);
  await expect(page.getByRole("button", { name: "個人Taskとモデルを再読込" })).toBeVisible();
  await expect(guide).toContainText("保存済み予測は再計算されません");
});

test("Data Library separates update, mapping, and new Task onboarding", async ({ page }, testInfo) => {
  await page.goto("/?view=data-library");

  const paths = page.getByRole("region", { name: "追加するデータはどれですか" });
  await expect(paths.getByRole("button", { name: /更新版/ })).toBeEnabled();
  await expect(paths.getByRole("button", { name: /列名・構造が違う/ })).toBeVisible();
  await expect(paths.getByRole("button", { name: /新しい予測問題/ })).toBeVisible();

  await paths.getByRole("button", { name: /新しい予測問題/ }).click();
  const newTask = page.getByRole("region", { name: "完全に新しいTaskを準備" });
  await expect(newTask).toContainText("任意コードは生成せず");
  await expect(newTask).toContainText("再読込し、そのままProjectを作成");
  await expect(newTask).not.toContainText("再起動");
  await expect(newTask.getByRole("button", { name: "個人Taskとモデルを再読込" })).toBeVisible();
  await newTask.screenshot({ path: testInfo.outputPath("new-task-onboarding.png") });

  await paths.getByRole("button", { name: /更新版/ }).click();
  await expect(page).toHaveURL(/view=profile-workbench.*onboarding=revision.*base_dataset=/);
  await expect(page.getByRole("heading", { name: "Datasetの更新版を登録" })).toBeVisible();
  await expect(page.getByLabel("更新元Dataset")).toBeVisible();
  await expect(page.getByLabel("データセットプロファイル")).toBeDisabled();
});

test("Data Library blocks model updates when an exact personal Profile is missing", async ({ page }) => {
  await page.route("**/api/data-library/datasets?*", async (route) => {
    const response = await route.fetch();
    const datasets = await response.json() as Array<Record<string, unknown>>;
    const target = datasets.find((item) => {
      const profile = item.profile_revision as { effective_profile_json?: Record<string, unknown> };
      return profile.effective_profile_json
        && "shared" in profile.effective_profile_json
        && "tasks" in profile.effective_profile_json;
    });
    expect(target).toBeTruthy();
    target!.profile_locator = null;
    await route.fulfill({ response, json: datasets });
  });
  await page.goto("/?view=data-library");

  const selectedDataset = page.locator(".dataset-context");
  await selectedDataset.getByRole("button", { name: "このデータでモデルを更新" }).click();
  const guide = page.getByRole("region", { name: "モデルを追加する" });

  await expect(guide.getByRole("alert")).toContainText("登録時のProfileが見つからない");
  await expect(guide.getByRole("alert")).toContainText("自動検出へ切り替える");
  await expect(guide.getByRole("alert")).toContainText("WORKBENCH_PROFILE_STORE_PATH");
  await expect(guide.getByRole("alert").locator("code")).toHaveText(/^[0-9a-f]{64}\.json$/);
  await expect(guide.getByRole("textbox", { name: "PowerShellモデル更新手順" })).not.toBeVisible();
  await expect(guide.getByRole("button", { name: "PowerShell手順をコピー" })).not.toBeVisible();
});

test("Data Library distinguishes personal models from bundled models", async ({ page }) => {
  await page.route("**/api/data-library/model-packages?*", async (route) => {
    const response = await route.fetch();
    const packages = await response.json() as Array<Record<string, unknown>>;
    await route.fulfill({
      response,
      json: packages.map((item) => ({ ...item, storage_scope: "personal" })),
    });
  });
  await page.goto("/?view=data-library");

  const selectedDataset = page.locator(".dataset-context");
  await expect(selectedDataset.getByText("自分のモデル", { exact: true }).first()).toBeVisible();
  await expect(selectedDataset.getByText("同梱モデル", { exact: true })).toHaveCount(0);
});

test("Data Library structure has no page-level horizontal overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=data-library");
  await page.getByRole("button", { name: "このデータでモデルを更新" }).click();

  const width = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(width.scrollWidth).toBeLessThanOrEqual(width.clientWidth);
});

test("Data Library opens the exact model training row trace", async ({ page }, testInfo) => {
  await page.goto("/?view=data-library");
  await page.getByRole("button", { name: "学習データの採否を見る" }).first().click();

  await expect(page).toHaveURL(/view=workspace.*admin=developer.*developer_tab=training/);
  await expect(page.getByRole("heading", { name: "このProjectでモデルが使ったデータ" })).toBeVisible();
  const inspector = page.locator(".model-training-data");
  await expect(inspector).toHaveAttribute("open", "");
  await expect(inspector).toContainText("元データ");
  await expect(inspector).toContainText("目的変数を採用");
  await expect(inspector).toContainText("実際のモデル入力");

  await inspector.getByRole("tab", { name: /モデル入力/ }).click();
  await expect(inspector).toContainText("実際のモデル入力です");
  await expect(inspector.getByRole("columnheader", { name: /実測ID/ })).toBeVisible();
  await expect(inspector.locator(".training-data-table tbody").first()).toContainText("TT-");
  await expect(inspector.locator(".training-data-table tbody tr")).not.toHaveCount(0);

  await page.setViewportSize({ width: 720, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(720);
  await page.locator(".project-training-trace").screenshot({
    path: testInfo.outputPath("model-training-row-trace.png"),
  });
});
