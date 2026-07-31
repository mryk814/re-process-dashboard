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
  let inspected = 0;
  await page.route("**/api/data-library/csv-onboarding/inspect", async (route) => {
    inspected += 1;
    await route.fulfill({
      json: {
        rows: 103,
        relations: 0,
        notice: "観測最小値・最大値は要約です。物理的な許容範囲や目標値には自動で使いません。",
        columns: [
          { name: "temperature", kind: "number", non_empty: 103, observed_min: 700, observed_max: 850, choices: [] },
          { name: "strength", kind: "number", non_empty: 103, observed_min: 310, observed_max: 590, choices: [] },
        ],
      },
    });
  });
  await page.goto("/?view=data-library");

  const paths = page.getByRole("region", { name: "追加するデータはどれですか" });
  await expect(paths.getByRole("button", { name: /更新版/ })).toBeEnabled();
  await expect(paths.getByRole("button", { name: /列名・構造が違う/ })).toBeVisible();
  await expect(paths.getByRole("button", { name: /新しい予測問題/ })).toBeVisible();

  await paths.getByRole("button", { name: /新しい予測問題/ }).click();
  const newTask = page.getByRole("region", { name: "完全に新しいTaskを準備" });
  await expect(newTask).toContainText("任意コードは生成せず");
  await expect(newTask).toContainText("build → verify → promote → 再読込");
  await expect(newTask).not.toContainText("再起動");
  await newTask.locator('input[type="file"]').setInputFiles({
    name: "private-demo.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("temperature,strength\n700,310\n850,590\n"),
  });
  await newTask.getByRole("button", { name: "CSVをプレビュー" }).click();
  await expect(newTask).toContainText("103行・2列・relations 0件");
  await expect(newTask).toContainText("物理範囲には自動設定しません");
  expect(inspected).toBe(1);
  await newTask.screenshot({ path: testInfo.outputPath("new-task-onboarding.png") });

  await paths.getByRole("button", { name: /更新版/ }).click();
  await expect(page).toHaveURL(/view=profile-workbench.*onboarding=revision.*base_dataset=/);
  await expect(page.getByRole("heading", { name: "Datasetの更新版を登録" })).toBeVisible();
  await expect(page.getByLabel("更新元Dataset")).toBeVisible();
  await expect(page.getByLabel("データセットプロファイル")).toBeDisabled();
});

test("private CSV is prepared into the exact Dataset, Task, and Package binding", async ({ page }) => {
  const rows = Array.from({ length: 30 }, (_, index) => [
    (0.1 + index * 0.01).toFixed(2),
    String(700 + index * 4),
    index % 2 ? "B" : "A",
    String(300 + index * 7 + (index % 2 ? 12 : 0)),
  ].join(","));
  await page.goto("/?view=data-library");
  const paths = page.getByRole("region", { name: "追加するデータはどれですか" });
  await paths.getByRole("button", { name: /新しい予測問題/ }).click();
  const onboarding = page.getByRole("region", { name: "完全に新しいTaskを準備" });
  const prepare = onboarding.getByRole("button", { name: "Task・モデル・Datasetを準備してProject作成へ" });
  await expect(prepare).toBeDisabled();
  await onboarding.locator('input[type="file"]').setInputFiles({
    name: "private-new-task.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(`carbon,temperature,route,strength\n${rows.join("\n")}\n`),
  });
  await onboarding.getByRole("button", { name: "CSVをプレビュー" }).click();
  await expect(onboarding).toContainText("30行・4列・relations 0件");
  await onboarding.getByLabel("Task ID").fill("browser-private-strength-v1");
  await onboarding.getByLabel("表示名").first().fill("ブラウザ私有強度");

  const carbon = onboarding.locator(".csv-task-columns article").filter({ hasText: "carbon" });
  await carbon.getByLabel("役割").selectOption("composition");
  await carbon.getByLabel("単位").fill("%");
  await carbon.getByLabel("物理的許容範囲 min,max").fill("0,2");
  await carbon.getByLabel("通常範囲 min,max").fill("0.05,0.5");
  await carbon.getByLabel("学習範囲 min,max").fill("0.1,0.39");

  const temperature = onboarding.locator(".csv-task-columns article").filter({ hasText: "temperature" });
  await temperature.getByLabel("役割").selectOption("process");
  await temperature.getByLabel("単位").fill("°C");
  await temperature.getByLabel("物理的許容範囲 min,max").fill("20,1500");
  await temperature.getByLabel("通常範囲 min,max").fill("650,900");
  await temperature.getByLabel("学習範囲 min,max").fill("700,816");

  const route = onboarding.locator(".csv-task-columns article").filter({ hasText: "route" });
  await route.getByLabel("役割").selectOption("categorical");

  const strength = onboarding.locator(".csv-task-columns article").filter({ hasText: "strength" });
  await strength.getByLabel("役割").selectOption("output");
  await strength.getByLabel("単位").fill("MPa");
  await strength.getByLabel("妥当範囲 min,max").fill("0,2000");
  await strength.getByLabel("表示範囲 min,max").fill("250,600");

  const prepared = page.waitForResponse((response) => response.url().includes("/api/data-library/csv-onboarding/prepare") && response.status() === 200);
  await prepare.click();
  const binding = await (await prepared).json() as { dataset_view_revision_id: string; task_id: string; model_package_ref_id: string };
  await expect(page).toHaveURL(/view=project/);
  const creation = page.getByRole("region", { name: "新規プロジェクトの開始方法" });
  await expect(creation).toBeVisible();
  const selects = creation.locator(".project-binding-flow select");
  await expect(selects.nth(0)).toHaveValue(binding.dataset_view_revision_id);
  await expect(selects.nth(1)).toHaveValue(`task:${binding.task_id}`);
  await expect(selects.nth(2)).toHaveValue(binding.model_package_ref_id);
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
