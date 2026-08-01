import { expect, test } from "@playwright/test";
import { apiBaseUrl } from "./helpers";

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

test("Data Library keeps Dataset and Task evidence when only Model Package refresh fails, then retries that resource", async ({ page }) => {
  let modelPackagesAvailable = true;
  await page.route("**/api/data-library/model-packages?*", async (route) => {
    if (!modelPackagesAvailable) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Model Package API is temporarily unavailable" }),
      });
      return;
    }
    await route.continue();
  });
  await page.route("**/api/data-library/tasks/refresh", async (route) => {
    await route.fulfill({
      json: {
        task_ids: [],
        added_task_ids: [],
        model_package_ids: [],
        added_model_package_ids: [],
        warnings: [],
      },
    });
  });

  await page.goto("/?view=data-library");
  const selectedDataset = page.locator(".dataset-context");
  await expect(page.getByRole("heading", { name: "使うデータを選ぶ" })).toBeVisible();
  await expect(selectedDataset.getByRole("heading", { name: "このデータで使うモデル" })).toBeVisible();
  await expect(selectedDataset.getByText("GP（安定ARD） · v2.1.0-stable-ard", { exact: true })).toBeVisible();

  modelPackagesAvailable = false;
  await selectedDataset.getByRole("button", { name: "このデータでモデルを更新" }).click();
  await page.getByRole("button", { name: "個人Taskとモデルを再読込" }).click();

  const modelError = selectedDataset.getByRole("alert");
  await expect(modelError).toContainText("Model Packageを更新できませんでした");
  await expect(modelError).toContainText("前回取得時点");
  await expect(modelError).toContainText("Projectを作成");
  await expect(selectedDataset.getByText("GP（安定ARD） · v2.1.0-stable-ard", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "使うデータを選ぶ" })).toBeVisible();

  modelPackagesAvailable = true;
  await modelError.getByRole("button", { name: "Model Packageを再試行" }).click();
  await expect(selectedDataset.getByRole("alert")).toHaveCount(0);
});

test("Task refresh only announces Project availability for a matching Dataset", async ({ page }) => {
  await page.goto("/?view=data-library");
  const [packagesResponse, datasetsResponse] = await Promise.all([
    page.request.get(`${apiBaseUrl}/api/data-library/model-packages?include_archived=true`),
    page.request.get(`${apiBaseUrl}/api/data-library/datasets?include_archived=true`),
  ]);
  expect(packagesResponse.status()).toBe(200);
  expect(datasetsResponse.status()).toBe(200);
  const packages = await packagesResponse.json() as Array<Record<string, unknown>>;
  const datasets = await datasetsResponse.json() as Array<Record<string, unknown>>;
  const matchingPackageId = packages.find((modelPackage) => {
    const manifest = modelPackage.manifest_json as { provenance?: Record<string, unknown> } | undefined;
    const provenance = manifest?.provenance;
    const trainingDataId = provenance?.training_data_id;
    const profileDigest = provenance?.dataset_profile_id;
    return typeof trainingDataId === "string" && datasets.some((dataset) => {
      const asset = dataset.data_asset as { sha256?: string } | undefined;
      const profile = dataset.profile_revision as { profile_digest?: string } | undefined;
      return `sha256:${asset?.sha256}` === trainingDataId
        && (profileDigest == null || profile?.profile_digest === profileDigest);
    });
  })?.id as string | undefined;
  expect(matchingPackageId).toBeTruthy();

  let addedPackageId = "without-dataset-package";
  await page.route("**/api/data-library/tasks/refresh", async (route) => {
    await route.fulfill({
      json: {
        task_ids: ["without-dataset-task-v1"],
        added_task_ids: ["without-dataset-task-v1"],
        model_package_ids: [addedPackageId],
        added_model_package_ids: [addedPackageId],
        warnings: [],
      },
    });
  });
  const selectedDataset = page.locator(".dataset-context");
  await selectedDataset.getByRole("button", { name: "このデータでモデルを更新" }).click();
  const guide = page.getByRole("region", { name: "モデルを追加する" });
  const refresh = guide.getByRole("button", { name: "個人Taskとモデルを再読込" });
  await refresh.click();
  await expect(guide.getByRole("status")).toContainText("対応するDatasetが登録されていないためProject作成にはまだ使えません");
  await expect(guide.getByRole("status")).not.toContainText("Project作成で選べます");

  addedPackageId = matchingPackageId!;
  await refresh.click();
  await expect(guide.getByRole("status")).toContainText("Project作成で選べます");
});

test("Data Library separates update, mapping, and new Task onboarding", async ({ page }, testInfo) => {
  let inspected = 0;
  await page.route("**/api/data-library/csv-onboarding/inspect", async (route) => {
    inspected += 1;
    await route.fulfill({
        json: {
          rows: 103,
          relations: 0,
          task_id_contract: { pattern: "^[a-z][a-z0-9-]{2,79}-v[1-9][0-9]*$", min_length: 6, example: "concrete-slump-v1" },
          notice: "観測最小値・最大値は要約です。物理的な許容範囲や目標値には自動で使いません。",
        columns: Array.from({ length: 10 }, (_, index) => ({
          name: index < 7 ? `input_${index + 1}` : `output_${index - 6}`,
          kind: "number",
          non_empty: index === 0 ? 101 : 103,
          observed_min: index < 7 ? 1 : 310,
          observed_max: index < 7 ? 10 : 590,
          choices: [],
        })),
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
  await expect(newTask).toContainText("103行・10列・relations 0件");
  await expect(newTask).toContainText("103行");
  await expect(newTask).toContainText("入力 0項目");
  await expect(newTask).toContainText("出力 0項目");
  await expect(newTask).toContainText("欠損 2件 / 103件");
  for (let index = 0; index < 10; index += 1) {
    await newTask.locator(".csv-task-columns article").nth(index).getByLabel("役割").selectOption(index < 7 ? "composition" : "output");
  }
  await expect(newTask).toContainText("入力 7項目");
  await expect(newTask).toContainText("出力 3項目");
  await expect(newTask).toContainText("relationsなしだけを扱います");
  await expect(newTask).toContainText("物理範囲には自動設定しません");
  expect(inspected).toBe(1);
  await newTask.screenshot({ path: testInfo.outputPath("new-task-onboarding.png") });

  await paths.getByRole("button", { name: /更新版/ }).click();
  await expect(page).toHaveURL(/view=profile-workbench.*onboarding=revision.*base_dataset=/);
  await expect(page.getByRole("heading", { name: "Datasetの更新版を登録" })).toBeVisible();
  await expect(page.getByLabel("更新元Dataset")).toBeVisible();
  await expect(page.getByLabel("データセットプロファイル")).toBeDisabled();
});

test("new Task onboarding explains unresolved domain ranges before preparation", async ({ page }) => {
  await page.route("**/api/data-library/csv-onboarding/inspect", async (route) => {
    await route.fulfill({
        json: {
          rows: 103,
          relations: 0,
          task_id_contract: { pattern: "^[a-z][a-z0-9-]{2,79}-v[1-9][0-9]*$", min_length: 6, example: "concrete-slump-v1" },
          notice: "観測最小値・最大値は要約です。物理的な許容範囲や目標値には自動で使いません。",
        columns: Array.from({ length: 10 }, (_, index) => ({
          name: index < 7 ? `input_${index + 1}` : `output_${index - 6}`,
          kind: "number",
          non_empty: 103,
          observed_min: index < 7 ? 1 : 310,
          observed_max: index < 7 ? 10 : 590,
          choices: [],
        })),
      },
    });
  });
  await page.goto("/?view=data-library");
  await page.getByRole("region", { name: "追加するデータはどれですか" }).getByRole("button", { name: /新しい予測問題/ }).click();
  const onboarding = page.getByRole("region", { name: "完全に新しいTaskを準備" });
  await onboarding.locator('input[type="file"]').setInputFiles({
    name: "unresolved-ranges.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("input_1,input_2,input_3,input_4,input_5,input_6,input_7,output_1,output_2,output_3\n1,1,1,1,1,1,1,310,320,330\n2,2,2,2,2,2,2,590,580,570\n"),
  });
  await onboarding.getByRole("button", { name: "CSVをプレビュー" }).click();
  await onboarding.getByLabel("Task ID").fill("unresolved-ranges-v1");
  await onboarding.getByLabel("表示名").fill("範囲未確定Task");
  const cards = onboarding.locator(".csv-task-columns article");
  for (let index = 0; index < 7; index += 1) {
    const card = cards.nth(index);
    await card.getByLabel("役割").selectOption("composition");
    await card.getByLabel("単位").fill("%");
  }
  for (let index = 7; index < 10; index += 1) {
    const card = cards.nth(index);
    await card.getByLabel("役割").selectOption("output");
    await card.getByLabel("単位").fill("mm");
  }
  await onboarding.getByLabel("1行=1観測であることを確認した").check();
  await onboarding.getByLabel("relationsなしであることを確認した").check();

  const prepare = onboarding.getByRole("button", { name: "Task・モデル・Datasetを準備してProject作成へ" });
  await expect(prepare).toBeDisabled();
  const status = onboarding.getByRole("region", { name: "準備条件" });
  await expect(status).toContainText("入力の物理的許容範囲");
  await expect(status).toContainText("input_1");
  await expect(status).toContainText("入力の通常範囲");
  await expect(status).toContainText("入力の学習範囲");
  await expect(status).toContainText("出力の妥当範囲");
  await expect(status).toContainText("出力の表示範囲");
  await expect(status).toContainText("観測最小値・最大値はデータの要約");
  await expect(prepare).toHaveAttribute("aria-describedby", "csv-task-preparation-status");
});

test("CSV onboarding validates Japanese canonical keys, task ID, observed training range, and typed storage recovery", async ({ page }) => {
  await page.route("**/api/data-library/csv-onboarding/inspect", async (route) => {
    await route.fulfill({
      json: {
        source_filename: "日本語列.csv",
        source_sha256: "a".repeat(64),
        rows: 2,
        relations: 0,
        task_id_contract: { pattern: "^[a-z][a-z0-9-]{2,79}-v[1-9][0-9]*$", min_length: 6, example: "concrete-slump-v1" },
        grain: "one-row-one-observation",
        notice: "観測範囲は要約です。",
        columns: [
          { name: "温度", kind: "number", non_empty: 2, observed_min: 700, observed_max: 850, choices: [] },
          { name: "強度", kind: "number", non_empty: 2, observed_min: 310, observed_max: 590, choices: [] },
        ],
      },
    });
  });
  await page.route("**/api/data-library/csv-onboarding/prepare", async (route) => {
    await route.fulfill({
      status: 422,
      json: {
        code: "model-store-unconfigured",
        message: "個人Model / Packageの保存先がこのWorkspaceに設定されていません。",
        next_action: "保存先を確認してください。",
      },
    });
  });
  await page.goto("/?view=data-library");
  await page.getByRole("region", { name: "追加するデータはどれですか" }).getByRole("button", { name: /新しい予測問題/ }).click();
  const onboarding = page.getByRole("region", { name: "完全に新しいTaskを準備" });
  await onboarding.locator('input[type="file"]').setInputFiles({
    name: "日本語列.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("温度,強度\n700,310\n850,590\n"),
  });
  await onboarding.getByRole("button", { name: "CSVをプレビュー" }).click();
  const cards = onboarding.locator(".csv-task-columns article");
  await cards.nth(0).getByLabel("役割").selectOption("process");
  await cards.nth(1).getByLabel("役割").selectOption("output");
  await expect(cards.nth(0).getByLabel("canonical key")).toHaveValue("field_1");
  await expect(cards.nth(1).getByLabel("canonical key")).toHaveValue("field_2");

  await onboarding.getByLabel("Task ID").fill("日本語-v1");
  await expect(onboarding.getByRole("region", { name: "準備条件" })).toContainText("Task IDは利用可能文字と形式を確認してください");
  await onboarding.getByLabel("Task ID").fill("japanese-columns-v1");
  await onboarding.getByLabel("表示名").first().fill("日本語列のTask");

  await cards.nth(0).getByLabel("単位").fill("°C");
  await cards.nth(0).getByLabel("物理的許容範囲 min,max").fill("0, 1200");
  await cards.nth(0).getByLabel("通常範囲 min,max").fill("600, 900");
  await cards.nth(0).getByRole("button", { name: "観測範囲を学習範囲へ使用" }).click();
  await expect(cards.nth(0).getByLabel("学習範囲 min,max")).toHaveValue("700, 850");
  await expect(cards.nth(0).getByLabel("物理的許容範囲 min,max")).toHaveValue("0, 1200");
  await expect(cards.nth(0)).toContainText("観測値由来");

  await cards.nth(1).getByLabel("単位").fill("MPa");
  await cards.nth(1).getByLabel("妥当範囲 min,max").fill("0, 1000");
  await cards.nth(1).getByLabel("表示範囲 min,max").fill("0, 1000");
  await onboarding.getByLabel("1行=1観測であることを確認した").check();
  await onboarding.getByLabel("relationsなしであることを確認した").check();
  await onboarding.getByRole("button", { name: "Task・モデル・Datasetを準備してProject作成へ" }).click();
  await expect(onboarding.getByRole("alert")).toContainText("個人Model / Packageの保存先");
  await expect(onboarding.getByRole("button", { name: "保存場所を管理して再確認" })).toBeVisible();
});

test("private CSV is prepared into the exact Dataset, Task, and Package binding", async ({ page }) => {
  test.setTimeout(120_000);
  const rows = Array.from({ length: 103 }, (_, index) => [
    (0.01 + index * 0.01).toFixed(2),
    String(1 + index),
    String(700 + index * 2),
    (10 + index * 0.5).toFixed(1),
    String(100 + index),
    String(200 + index * 3),
    index % 2 ? "B" : "A",
    String(300 + index * 4),
    String(100 + index * 2),
    String(40 + index),
  ].join(","));
  await page.goto("/?view=data-library");
  const paths = page.getByRole("region", { name: "追加するデータはどれですか" });
  await paths.getByRole("button", { name: /新しい予測問題/ }).click();
  const onboarding = page.getByRole("region", { name: "完全に新しいTaskを準備" });
  await expect(onboarding.getByRole("button", { name: "CSVをプレビュー" })).toBeDisabled();
  await onboarding.locator('input[type="file"]').setInputFiles({
    name: "private-103-row-task.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(`composition_a,composition_b,process_a,process_b,process_c,process_d,route,target_a,target_b,target_c\n${rows.join("\n")}\n`),
  });
  await onboarding.getByRole("button", { name: "CSVをプレビュー" }).click();
  await expect(onboarding).toContainText("103行・10列・relations 0件");
  const prepare = onboarding.getByRole("button", { name: "Task・モデル・Datasetを準備してProject作成へ" });
  await expect(prepare).toBeDisabled();
  await onboarding.getByLabel("Task ID").fill("browser-private-103-row-v1");
  await onboarding.getByLabel("表示名").first().fill("ブラウザ103行の新規Task");

  const inputColumns = [
    { role: "composition", unit: "%", training: "0.01,1.03" },
    { role: "composition", unit: "%", training: "1,103" },
    { role: "process", unit: "°C", training: "700,904" },
    { role: "process", unit: "s", training: "10,61" },
    { role: "process", unit: "N", training: "100,202" },
    { role: "process", unit: "rpm", training: "200,506" },
  ];
  const cards = onboarding.locator(".csv-task-columns article");
  for (const [index, input] of inputColumns.entries()) {
    const card = cards.nth(index);
    await card.getByLabel("役割").selectOption(input.role);
    await card.getByLabel("単位").fill(input.unit);
    await card.getByLabel("物理的許容範囲 min,max").fill("0,2000");
    await card.getByLabel("通常範囲 min,max").fill("0,2000");
    await card.getByLabel("学習範囲 min,max").fill(input.training);
  }
  await cards.nth(6).getByLabel("役割").selectOption("categorical");
  for (const index of [7, 8, 9]) {
    const card = cards.nth(index);
    await card.getByLabel("役割").selectOption("output");
    await card.getByLabel("単位").fill("MPa");
    await card.getByLabel("妥当範囲 min,max").fill("0,2000");
    await card.getByLabel("表示範囲 min,max").fill("0,2000");
  }
  await expect(onboarding).toContainText("入力 7項目");
  await expect(onboarding).toContainText("出力 3項目");

  await expect(prepare).toBeDisabled();
  await onboarding.getByLabel("1行=1観測であることを確認した").check();
  await expect(prepare).toBeDisabled();
  await onboarding.getByLabel("relationsなしであることを確認した").check();
  await expect(prepare).toBeEnabled();

  const prepared = page.waitForResponse((response) => response.url().includes("/api/data-library/csv-onboarding/prepare") && response.status() === 200);
  await prepare.click();
  const binding = await (await prepared).json() as {
    dataset_view_revision_id: string;
    dataset_revision_id: string;
    task_id: string;
    model_package_ref_id: string;
    source_sha256: string;
  };
  expect(binding.source_sha256).toMatch(/^[a-f0-9]{64}$/);
  await expect(page).toHaveURL(/view=project/);
  const creation = page.getByRole("region", { name: "新規プロジェクトの開始方法" });
  await expect(creation).toBeVisible();
  const receipt = creation.getByRole("status", { name: "CSV onboardingの準備結果" });
  await expect(receipt).toContainText("Task・Dataset・Model Packageを準備し、再読込しました");
  await expect(receipt).toContainText(binding.task_id);
  await expect(receipt).toContainText(binding.dataset_revision_id);
  await expect(receipt).toContainText(binding.model_package_ref_id);
  await expect(receipt).toContainText(binding.source_sha256);
  const selects = creation.locator(".project-binding-flow select");
  await expect(selects.nth(0)).toHaveValue(binding.dataset_view_revision_id);
  await expect(selects.nth(1)).toHaveValue(`task:${binding.task_id}`);
  await expect(selects.nth(2)).toHaveValue(binding.model_package_ref_id);
  await creation.getByLabel("プロジェクト名").fill("103行CSV UI-only Project");
  const createdResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith("/api/projects")
    && response.status() === 201
  ));
  await creation.getByRole("button", { name: "固定してプロジェクトを作成" }).click();
  const project = await (await createdResponse).json() as {
    id: string;
    task_id: string;
    dataset_view_revision_id: string;
    model_package_ref_id: string;
  };
  expect(project.task_id).toBe(binding.task_id);
  expect(project.dataset_view_revision_id).toBe(binding.dataset_view_revision_id);
  expect(project.model_package_ref_id).toBe(binding.model_package_ref_id);
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
    target!.profile_available = false;
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
