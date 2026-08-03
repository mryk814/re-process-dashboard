import { expect, test, type APIRequestContext } from "@playwright/test";
import { join } from "node:path";
import { apiBaseUrl as api, createProjectWithBinding, createProjectWithCandidate } from "./helpers";

async function createCandidateProject(request: APIRequestContext) {
  const project = await createProjectWithCandidate(
    request,
    "annealed-properties-v1",
    `候補操作E2E ${Date.now()}`,
    "操作元候補",
  );
  return project.id;
}

/**
 * The bundled default project is backed by the minimal teaching dataset, which
 * deliberately stays small and clean. Graph windowing, duplicate keys, orphans
 * and implausible values only exist in the process dataset, so the specs that
 * assert those bind a project to it instead of reading `project=default`.
 */
let processProjectId: string | undefined;

async function processLineageProject(request: APIRequestContext) {
  if (processProjectId === undefined) {
    const project = await createProjectWithBinding(
      request,
      "annealed-properties-v1",
      `工程データ系譜E2E ${Date.now()}`,
      {
        datasetFilename: "material_workbench_process_v1.xlsx",
        includeGallery: true,
      },
    );
    processProjectId = project.id;
  }
  return processProjectId;
}

test("startup restores the last opened location", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=AN-01");
  await expect(page.getByRole("heading", { name: "AN-01" })).toBeVisible();

  await page.goto("/");
  await expect(page).toHaveURL(/view=lineage.*project=default.*entity=AN-01/);
  await expect(page.getByRole("heading", { name: "AN-01" })).toBeVisible();

  await page.goto("/?view=project&project=default");
  await expect(page).toHaveURL(/view=project/);
  await expect(page.getByRole("complementary", { name: "プロジェクト一覧" })).toBeVisible();

  await page.goto("/?view=lineage&project=missing-project&entity=AN-01");
  await expect(page).toHaveURL(/view=lineage.*project=default/);
  await expect(page).not.toHaveURL(/entity=/);
  await expect(page.getByRole("heading", { name: "調べるノードを選択してください" })).toBeVisible();
});

test("dataset import stays in the global data library context", async ({ page }) => {
  await page.goto("/?view=data-library");
  await page.getByRole("button", { name: "データを追加" }).click();

  await expect(page).toHaveURL(/view=profile-workbench/);
  expect(new URL(page.url()).searchParams.get("project")).toBeNull();
  expect(new URL(page.url()).searchParams.get("admin")).toBeNull();
  await expect(page.getByRole("heading", { name: "既存Taskへデータを対応付け" })).toBeVisible();
  await expect(page.getByRole("button", { name: "データライブラリ", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "開発・管理メニュー" })).toHaveCount(0);

  await page.reload();
  await expect(page).toHaveURL(/view=profile-workbench/);
  expect(new URL(page.url()).searchParams.get("project")).toBeNull();
  await expect(page.getByRole("heading", { name: "既存Taskへデータを対応付け" })).toBeVisible();

  await page.getByRole("button", { name: "データライブラリに戻る" }).click();
  await expect(page).toHaveURL(/view=data-library/);
  await expect(page.getByRole("heading", { name: "データライブラリ" })).toBeVisible();

  await page.goto("/?view=settings&project=default&admin=profile");
  await expect(page.getByRole("navigation", { name: "開発・管理メニュー" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "新しいDatasetを準備" })).toHaveCount(0);
});

test("Data Library tab state is shareable and browser history restores it", async ({ page }) => {
  let modelPackagesAvailable = true;
  await page.route("**/api/data-library/model-packages?*", async (route) => {
    if (!modelPackagesAvailable) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Model Package service is unavailable" }),
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
  const browseTab = page.getByRole("tab", { name: "閲覧" });
  const updateTab = page.getByRole("tab", { name: "データ更新" });
  await expect(browseTab).toHaveAttribute("aria-selected", "true");
  await expect(browseTab).toHaveAttribute("tabindex", "0");
  await expect(updateTab).toHaveAttribute("tabindex", "-1");
  await expect(browseTab).toHaveAttribute("aria-controls", "data-library-panel-browse");
  await browseTab.press("ArrowRight");
  await expect(updateTab).toBeFocused();
  await expect(updateTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel", { name: "データ更新" })).toBeVisible();
  await updateTab.press("Home");
  await expect(browseTab).toBeFocused();
  await expect(browseTab).toHaveAttribute("aria-selected", "true");
  const selectedDataset = page.locator(".dataset-context");
  await selectedDataset.getByRole("button", { name: "このデータでモデルを更新" }).click();
  modelPackagesAvailable = false;
  await page.getByRole("button", { name: "個人Taskとモデルを再読込" }).click();
  await expect(selectedDataset.getByRole("alert")).toContainText("Model Packageを更新できませんでした");
  await expect(page.getByRole("heading", { name: "使うデータを選ぶ" })).toBeVisible();
  await page.getByRole("tab", { name: "データ更新" }).click();
  await expect(page).toHaveURL(/view=data-library.*tab=update/);
  await expect(page.getByRole("tab", { name: "データ更新" })).toHaveAttribute("aria-selected", "true");

  await page.goBack();
  await expect(page).toHaveURL(/view=data-library/);
  await expect(page).not.toHaveURL(/tab=update/);
  await expect(page.getByRole("tab", { name: "閲覧" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "使うデータを選ぶ" })).toBeVisible();

  await page.goForward();
  await expect(page).toHaveURL(/view=data-library.*tab=update/);
  await page.reload();
  await expect(page.getByRole("tab", { name: "データ更新" })).toHaveAttribute("aria-selected", "true");
});

test("developer guide continues through Profile Workbench to project creation", async ({ page }) => {
  await page.goto("/?view=settings&project=default&admin=developer");
  await page.getByRole("button", { name: "変更ガイド" }).click();
  await page.getByRole("button", { name: "Profile WorkbenchでExcelを確認" }).click();

  await expect(page).toHaveURL(/view=profile-workbench/);
  const steps = page.getByRole("list", { name: "Dataset登録からProject作成まで" });
  await expect(steps).toContainText("Excel");
  await expect(steps).toContainText("Base Profile");
  await expect(steps).toContainText("対応付け");
  await expect(steps).toContainText("Project作成");

  await page.locator('input[accept^=".xlsx"]').setInputFiles(
    join(process.cwd(), "data", "source", "material_workbench_tutorial_v1.xlsx"),
  );
  await page.getByRole("button", { name: "内容を確認" }).click();
  await expect(page.getByRole("heading", { name: "Canonical preview" })).toBeVisible();
  await expect(page.getByText("必須構造はProfileに対応")).toBeVisible();
  await expect(page.locator(".profile-candidate-summary").getByText("Profile候補", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "この内容で登録" }).click();
  await expect(page.getByRole("button", { name: "このDatasetでプロジェクト作成" })).toBeVisible();
  await page.getByRole("button", { name: "このDatasetでプロジェクト作成" }).click();

  await expect(page).toHaveURL(/view=project/);
  await expect(page.getByRole("heading", { name: "新しいプロジェクト" })).toBeVisible();
  await expect(page.getByLabel("Dataset")).not.toHaveValue("");
});

test("developer guide deep link survives reload and browser history", async ({ page }) => {
  await page.goto("/?view=settings&project=default&admin=developer&developer_tab=guide&developer_guide=decision-activity-new");
  const intent = page.getByLabel("何を変更したいですか？");
  await expect(intent).toHaveValue("decision-activity-new");
  await expect(page.getByRole("link", { name: /contract-through-stack\.qmd/ })).toHaveAttribute(
    "href",
    /github\.com\/mryk814\/re-process-dashboard\/blob\/main\//,
  );

  await intent.selectOption("decision-activity-change");
  await expect(page).toHaveURL(/developer_guide=decision-activity-change/);
  await page.reload();
  await expect(intent).toHaveValue("decision-activity-change");
  await page.goBack();
  await expect(intent).toHaveValue("decision-activity-new");
  await page.goForward();
  await expect(intent).toHaveValue("decision-activity-change");
});

test("developer diagnostics shows runtime checks without repository tooling", async ({ page }) => {
  await page.goto("/?view=settings&project=default&admin=developer");
  await page.getByRole("button", { name: "診断" }).click();

  await expect(page.getByRole("heading", { name: "実行環境の診断" })).toBeVisible();
  // The diagnostics run against every registered runtime, which takes longer than
  // the default expect timeout.
  await expect(page.locator(".doctor-summary")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Projectの固定参照", { exact: true })).toBeVisible();
  await expect(page.getByText("API／sidecar状態")).toBeVisible();
  await expect(page.getByText(/npm|uv|OpenAPI check/)).toHaveCount(0);
});

test("project hub keeps the active project visible across scoped navigation", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  const projectList = page.getByRole("complementary", { name: "プロジェクト一覧" });
  await expect(projectList).toBeVisible();
  await expect(projectList.locator(".project-list-item[aria-current=page]")).toContainText("焼鈍条件の候補検討");
  await expect(page.getByText("プロジェクトを切り替えました", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "候補比較", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "候補比較" })).toHaveClass(/active/);
  await expect(page.locator(".context-bar h1")).toHaveText("焼鈍条件の候補検討");

  await page.getByRole("button", { name: "プロジェクト", exact: true }).click();
  await expect(page).toHaveURL(/view=project/);
  await expect(projectList).toBeVisible();
});

test("quality finding opens the selected lineage node and returns with filters", async ({ page, request }) => {
  const project = await processLineageProject(request);
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto(`/?view=quality&project=${project}`);
  // The first process-dataset load parses a larger workbook than the bundled
  // teaching sample and can cross the default 5 second assertion budget.
  await expect(page.getByRole("heading", { name: "データ品質" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "データ探索" })).toHaveClass(/active/);

  await page.getByLabel("種別").selectOption("duplicate_key");
  await expect(page).toHaveURL(/quality_type=duplicate_key/);
  const issueRow = page.locator(".quality-table tbody tr").filter({ has: page.getByRole("button", { name: "系譜で確認" }) }).first();
  const entityKey = (await issueRow.locator("td").first().textContent())?.trim();
  expect(entityKey).toBeTruthy();
  await issueRow.getByRole("button", { name: "系譜で確認" }).click();

  await expect(page).toHaveURL(new RegExp(`view=lineage.*entity=${entityKey}`));
  await expect(page.locator(".lineage-graph-node.selected")).toContainText(entityKey!);
  await expect(page.locator(".investigation-context")).toContainText("データ品質の検出結果から調査中");
  await page.reload();
  await expect(page.locator(".lineage-graph-node.selected")).toContainText(entityKey!);

  await page.getByRole("button", { name: "品質一覧へ戻る" }).click();
  await expect(page).toHaveURL(/view=quality/);
  await expect(page).toHaveURL(/quality_type=duplicate_key/);
  await expect(page.locator(".quality-focus-row")).toContainText(entityKey!);

  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("permission denied")) },
    });
  });
  await page.locator(".quality-focus-row").getByRole("button", { name: "キーをコピー" }).click();
  await expect(page.getByRole("alert")).toContainText("クリップボード権限を確認してください");

  const beforeDownloadUrl = page.url();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "検出結果をCSV出力" }).click();
  expect((await download).suggestedFilename()).toBe("detected-data-quality.csv");
  await expect(page).toHaveURL(beforeDownloadUrl);
  expect(pageErrors).toEqual([]);
});

test("lineage candidate remains in exploration and round-trips through stock", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=AN-01");
  await expect(page.getByRole("heading", { name: "AN-01" })).toBeVisible();

  const stock = page.locator(".stock-button");
  const before = await stock.locator("b").textContent();
  await page.getByRole("button", { name: "候補ストックへ追加" }).click();
  await expect(page).toHaveURL(/view=lineage/);
  await expect(page).toHaveURL(/candidate=/);
  await expect(page.locator(".lineage-candidate-added")).toContainText("候補ストックに1件追加しました");
  await expect(page.locator(".lineage-candidate-added")).toContainText("候補を比較");
  const stockedCandidateId = new URL(page.url()).searchParams.get("candidate");
  expect(stockedCandidateId).toBeTruthy();
  await expect(stock.locator("b")).not.toHaveText(before ?? "");

  await stock.click();
  await expect(page).toHaveURL(/view=candidates/);
  await expect(page.locator(".candidate-origin")).toContainText("工程系譜 AN-01");
  await page.getByRole("button", { name: "作成元の実績を見る" }).click();
  const evidenceDrawer = page.getByRole("complementary", { name: "過去実績の根拠" });
  await expect(evidenceDrawer).toBeVisible();
  await expect(evidenceDrawer).toContainText("AN-01");
  await expect(page).toHaveURL(/view=candidates/);
  await evidenceDrawer.getByRole("button", { name: "データ探索で系譜全体を見る" }).click();
  await expect(page).toHaveURL(/view=lineage.*entity=AN-01/);

  await page.reload();
  await expect(page.getByRole("heading", { name: "AN-01" })).toBeVisible();
  await page.locator(".stock-button").click();
  await expect(page).toHaveURL(/view=candidates/);
  await page.goBack();
  await expect(page).toHaveURL(/view=lineage/);

  // Reopening the stock after a reload lands on the project's current candidate,
  // but the one lineage stocked is still there with its origin intact.
  await page.goto(`/?view=candidates&project=default&candidate=${stockedCandidateId}`);
  await expect(page.locator(".candidate-origin")).toContainText("工程系譜 AN-01");
});

test("a delayed lineage candidate cannot leave the next node in a creating state", async ({ page }) => {
  let releaseRequest!: () => void;
  const requestHeld = new Promise<void>((resolve) => {
    releaseRequest = resolve;
  });
  await page.route("**/api/projects/default/lineage/AN-01/candidate**", async (route) => {
    await requestHeld;
    await route.continue();
  });
  await page.goto("/?view=lineage&project=default&entity=AN-01");
  const detail = page.getByRole("complementary", { name: "選択ノード詳細" });
  await detail.getByRole("button", { name: "候補ストックへ追加" }).click();
  await expect(detail.getByRole("button", { name: "追加中…" })).toBeDisabled();

  await page.getByRole("button", { name: /^AN-02 焼鈍/ }).click();
  await expect(page).toHaveURL(/entity=AN-02/);
  await expect(detail.getByRole("heading", { name: "AN-02" })).toBeVisible();
  const nextCandidateAction = detail.getByRole("button", { name: /候補へ追加|候補ストックへ追加/ });
  await expect(nextCandidateAction).toBeEnabled();
  await expect(nextCandidateAction).not.toHaveText("追加中…");

  releaseRequest();
  await expect(detail.getByRole("heading", { name: "AN-02" })).toBeVisible();
  await expect(page.locator(".lineage-candidate-added")).toHaveCount(0);
});

test("lineage opens without a fixed node and renders real selectable edges", async ({ page, request }) => {
  const project = await processLineageProject(request);
  await page.goto(`/?view=lineage&project=${project}`);
  await expect(page.getByRole("heading", { name: "調べるノードを選択してください" })).toBeVisible();
  await expect(page).not.toHaveURL(/entity=/);

  await page.getByLabel("ノードを検索").fill("AN-00001");
  await page.getByRole("button", { name: /AN-00001/ }).click();
  await expect(page).toHaveURL(/view=lineage.*entity=AN-00001/);
  await expect(page.getByTestId("lineage-real-graph")).toBeVisible();
  await expect(page.getByText("熱延用の試験・組織", { exact: true })).toBeVisible();
  const annealedTestGroups = page.locator('.lineage-graph-group-toggle[aria-label^="焼鈍条件-3CGL AN-00001 の"]');
  await expect(annealedTestGroups).toHaveCount(2);
  const annealedTestGroup = page.locator(".lineage-graph-group.group-annealed-microstructure .lineage-graph-group-toggle");
  await expect(annealedTestGroup).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".lineage-graph-node").filter({ hasText: "AMS-00001" })).toHaveCount(0);
  await annealedTestGroup.click();
  await expect(page.getByRole("button", { name: "焼鈍条件-3CGL AN-00001 の組織を折りたたむ" })).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".lineage-graph-node").filter({ hasText: "AMS-00001" })).toBeVisible();
  await annealedTestGroup.click();
  const collapsedAnnealedTestGroup = page.getByRole("button", { name: "焼鈍条件-3CGL AN-00001 の組織を展開する" });
  await expect(collapsedAnnealedTestGroup).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".lineage-graph-node").filter({ hasText: "AMS-00001" })).toHaveCount(0);
  await collapsedAnnealedTestGroup.click();
  await expect(page.getByRole("button", { name: "焼鈍条件-3CGL AN-00001 の組織を折りたたむ" })).toHaveAttribute("aria-expanded", "true");
  const annealedHoleGroup = page.locator(".lineage-graph-group.group-annealed-hole-expansion .lineage-graph-group-toggle");
  await annealedHoleGroup.click();
  await expect(page.getByRole("button", { name: "焼鈍条件-3CGL AN-00001 の穴広げを折りたたむ" })).toHaveAttribute("aria-expanded", "true");
  const holeGroupFacts = page.locator(".lineage-group-facts");
  await expect(holeGroupFacts).toContainText("焼鈍穴広げ 3件");
  await expect(holeGroupFacts).toContainText("HE-00001");
  await expect(holeGroupFacts).toContainText("116.8");
  await expect(holeGroupFacts.locator("tbody tr")).toHaveCount(3);
  await expect(holeGroupFacts).not.toContainText("このグループの実績値はありません。");
  await expect(page.locator(".lineage-graph-edge")).not.toHaveCount(0);
  await expect(page.locator('.lineage-graph-node[aria-current="true"]')).toContainText("AN-00001");
  await expect(page.locator(".lineage-graph-node.upstream").first()).toBeVisible();
  await expect(page.locator(".lineage-graph-node.downstream")).toHaveCount(5);
  await expect(page.locator(".lineage-graph-edge.downstream")).toHaveCount(5);

  const upstreamKey = await page.locator(".lineage-graph-node.upstream").first().locator("b").textContent();
  expect(upstreamKey).toBeTruthy();
  await page.locator(".lineage-graph-node.upstream").first().click();
  await expect(page).toHaveURL(new RegExp(`entity=${upstreamKey}`));
  await expect(page.getByRole("complementary", { name: "選択ノード詳細" })).toContainText(upstreamKey ?? "");
  await page.evaluate(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete("entity");
    window.history.pushState({}, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page).not.toHaveURL(/entity=/);
  await expect(page.getByRole("heading", { name: "調べるノードを選択してください" })).toBeVisible();
});

test("lineage orders test groups by process condition before test type", async ({ page, request }) => {
  const project = await processLineageProject(request);
  await page.goto(`/?view=lineage&project=${project}&entity=ME-00001`);
  await expect(page.getByTestId("lineage-real-graph")).toBeVisible();
  await expect(page.locator(".lineage-graph-group-toggle")).toHaveCount(9);
  expect(await page.locator(".lineage-graph-group-toggle").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label")))).toEqual([
    "熱延条件 HR-00001 の熱延引張を展開する",
    "熱延条件 HR-00001 の熱延組織を展開する",
    "熱延条件 HR-00002 の熱延引張を展開する",
    "熱延条件 HR-00002 の熱延組織を展開する",
    "焼鈍条件-3CGL AN-00001 の穴広げを展開する",
    "焼鈍条件-3CGL AN-00001 の組織を展開する",
    "焼鈍条件-3CGL AN-00002 の引張を展開する",
    "焼鈍条件-3CGL AN-00002 の穴広げを展開する",
    "焼鈍条件-3CGL AN-00002 の組織を展開する",
  ]);
});

test("lineage marks implausible observations without hiding raw values", async ({ page, request }) => {
  const project = await processLineageProject(request);
  await page.goto(`/?view=lineage&project=${project}&entity=HT-00024`);
  const detail = page.getByRole("complementary", { name: "選択ノード詳細" });
  await expect(detail.getByText("⚠ 物理範囲外").first()).toBeVisible();
  await expect(detail).toContainText("5223.3");
  await expect(detail).toContainText("妥当範囲 100–2,500 MPa");
});

test("lineage search recovers from an empty result and opens an exact key", async ({ page }) => {
  await page.goto("/?view=lineage&project=default");
  await page.getByLabel("ノードを検索").fill("does-not-exist");
  await expect(page.getByText("一致するキーはありません。")).toBeVisible();

  await page.getByLabel("ノードを検索").fill("AN-03");
  await page.getByRole("button", { name: /AN-03/ }).click();
  await expect(page).toHaveURL(/entity=AN-03/);
  await expect(page.getByRole("complementary", { name: "選択ノード詳細" })).toContainText("AN-03");
  await expect(page.getByLabel("ノードを検索")).toHaveValue("AN-03");
});

test("lineage expands the 40-node window and distinguishes data issues", async ({ page, request }) => {
  const project = await processLineageProject(request);
  await page.goto(`/?view=lineage&project=${project}&entity=ME-00063`);
  await expect(page.getByText("40/62ノード表示")).toBeVisible();
  await page.getByRole("button", { name: /さらに40件読み込む/ }).click();
  await expect(page.getByText("62/62ノード表示")).toBeVisible();
  await expect(page.getByRole("button", { name: /さらに40件読み込む/ })).toHaveCount(0);

  await page.goto(`/?view=lineage&project=${project}&entity=HT-NOT-FOUND`);
  const missing = page.locator(".lineage-graph-node.selected");
  await expect(missing).toHaveClass(/missing/);
  await expect(missing).toHaveClass(/invalid-reference/);
  await expect(missing).toContainText("欠損先 / 参照切れ");

  await page.goto(`/?view=lineage&project=${project}&entity=CR-00010`);
  await expect(page.locator(".lineage-graph-node.selected")).toHaveClass(/orphan/);
  await expect(page.locator(".lineage-graph-edge")).toHaveCount(0);

  await page.goto(`/?view=lineage&project=${project}&entity=HR-00001`);
  await expect(page.locator(".lineage-graph-node.selected")).toHaveClass(/duplicate/);
});

test("copied candidate keeps its source even after the source is deleted", async ({ page, request }) => {
  const projectId = await createCandidateProject(request);
  await page.goto(`/?view=candidates&project=${projectId}`);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  const sourceId = new URL(page.url()).searchParams.get("candidate");
  expect(sourceId).toBeTruthy();
  const sourceName = await page
    .locator(".candidate-name-table tbody tr.selected-row")
    .getByRole("textbox")
    .inputValue();

  await page.getByRole("button", { name: `${sourceName}を複製` }).click();
  await expect(page.locator(".candidate-origin")).toContainText("候補コピー");
  const copiedId = new URL(page.url()).searchParams.get("candidate");
  expect(copiedId).toBeTruthy();

  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
  await page.getByRole("button", { name: `${sourceName}を一覧から外す`, exact: true }).click();
  await page.getByRole("button", { name: "一覧から外す", exact: true }).click();
  // A copy references its source, so deleting the source archives it instead of
  // removing it. The copy must never end up unable to name where it came from.
  await expect(page.getByRole("button", { name: `${sourceName}を選択`, exact: true })).toHaveCount(0);
  await page.goto(`/?view=candidates&project=${projectId}&candidate=${copiedId}`);
  await expect(page.locator(".candidate-origin")).toContainText("候補コピー");
  await expect(page.locator(".candidate-origin")).not.toContainText("コピー元は削除済みか参照できません");
  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
});

test("archived copy source remains navigable", async ({ page, request }) => {
  const projectId = await createCandidateProject(request);
  await page.goto(`/?view=candidates&project=${projectId}`);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  const sourceId = new URL(page.url()).searchParams.get("candidate");
  expect(sourceId).toBeTruthy();
  const sourceName = await page
    .locator(".candidate-name-table tbody tr.selected-row")
    .getByRole("textbox")
    .inputValue();

  await page.getByRole("button", { name: `${sourceName}の詳細予測を保存` }).click();
  await expect(page.getByRole("button", { name: `${sourceName}の詳細予測を保存済み` })).toBeVisible();
  await page.getByRole("button", { name: `${sourceName}を複製` }).click();
  await expect(page.locator(".candidate-origin")).toContainText("候補コピー");
  const copiedId = new URL(page.url()).searchParams.get("candidate");
  expect(copiedId).toBeTruthy();

  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
  await page.getByRole("button", { name: `${sourceName}を一覧から外す`, exact: true }).click();
  await page.getByRole("button", { name: "一覧から外す", exact: true }).click();
  await page.goto(`/?view=candidates&project=${projectId}&candidate=${copiedId}`);
  await expect(page.locator(".candidate-origin")).not.toContainText("削除済みか参照できません");
  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page.locator(".candidate-origin")).toContainText("archive済み候補を参照中");
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
  await expect(page.locator(".candidate-origin")).toContainText("archive済み候補を参照中");
});
