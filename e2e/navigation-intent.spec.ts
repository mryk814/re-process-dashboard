import { expect, test, type APIRequestContext } from "@playwright/test";

const api = "http://127.0.0.1:8875";

async function createCandidateProject(request: APIRequestContext) {
  const catalog = await (await request.get(`${api}/api/task-definitions`)).json() as Array<{ definition: { task_definition: { id: string } }; starter_candidate: Record<string, unknown> }>;
  const task = catalog.find((item) => item.definition.task_definition.id === "annealed-properties-v1")!;
  const created = await request.post(`${api}/api/projects`, { data: { name: `候補操作E2E ${Date.now()}`, task_id: "annealed-properties-v1" } });
  const project = await created.json() as { id: string };
  const candidate = await request.post(`${api}/api/projects/${project.id}/candidates`, { data: { ...task.starter_candidate, name: "操作元候補" } });
  expect(candidate.status()).toBe(201);
  return project.id;
}

test("project hub keeps the active project visible across scoped navigation", async ({ page }) => {
  await page.goto("/?view=project&project=default");
  const projectList = page.getByRole("complementary", { name: "プロジェクト一覧" });
  await expect(projectList).toBeVisible();
  await expect(projectList.locator(".project-list-item[aria-current=page]")).toContainText("焼鈍条件の候補検討");

  await page.getByRole("button", { name: "候補比較", exact: true }).click();
  await expect(page.locator(".context-breadcrumb")).toContainText("プロジェクト");
  await expect(page.locator(".context-breadcrumb")).toContainText("候補比較");
  await expect(page.locator(".context-bar h1")).toHaveText("焼鈍条件の候補検討");

  await page.getByRole("button", { name: "プロジェクト", exact: true }).click();
  await expect(page).toHaveURL(/view=project/);
  await expect(projectList).toBeVisible();
});

test("quality finding opens the selected lineage node and returns with filters", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto("/?view=quality&project=default");
  await expect(page.getByRole("heading", { name: "問題から探す" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "データ探索" })).toHaveClass(/active/);

  await page.getByLabel("種別").selectOption("duplicate_key");
  await expect(page).toHaveURL(/quality_type=duplicate_key/);
  const issueRow = page.locator(".quality-table tbody tr").filter({ has: page.getByRole("button", { name: "系譜で確認" }) }).first();
  const entityKey = (await issueRow.locator("td").nth(1).textContent())?.trim();
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

  await page.getByRole("button", { name: "開発・管理", exact: true }).click();
  await expect(page.getByRole("heading", { name: "データ品質集計" })).toBeVisible();
  const beforeDownloadUrl = page.url();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "検出結果をCSV出力" }).click();
  expect((await download).suggestedFilename()).toBe("detected-data-quality.csv");
  await expect(page).toHaveURL(beforeDownloadUrl);
  expect(pageErrors).toEqual([]);
});

test("lineage candidate remains in exploration and round-trips through stock", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=AN-00001");
  await expect(page.getByRole("heading", { name: "AN-00001" })).toBeVisible();

  const stock = page.locator(".stock-button");
  const before = await stock.locator("b").textContent();
  await page.getByRole("button", { name: "候補ストックへ追加" }).click();
  await expect(page).toHaveURL(/view=lineage/);
  await expect(page).toHaveURL(/candidate=/);
  const stockedCandidateId = new URL(page.url()).searchParams.get("candidate");
  expect(stockedCandidateId).toBeTruthy();
  await expect(stock.locator("b")).not.toHaveText(before ?? "");

  await stock.click();
  await expect(page).toHaveURL(/view=candidates/);
  await expect(page.locator(".candidate-origin")).toContainText("工程系譜 AN-00001");
  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page).toHaveURL(/view=lineage.*entity=AN-00001/);

  await page.reload();
  await expect(page.getByRole("heading", { name: "AN-00001" })).toBeVisible();
  await page.locator(".stock-button").click();
  await expect(page).toHaveURL(new RegExp(`candidate=${stockedCandidateId}`));
  await page.goBack();
  await expect(page).toHaveURL(/view=lineage/);
});

test("lineage opens without a fixed node and renders real selectable edges", async ({ page }) => {
  await page.goto("/?view=lineage&project=default");
  await expect(page.getByRole("heading", { name: "調べるノードを選択してください" })).toBeVisible();
  await expect(page).not.toHaveURL(/entity=/);

  await page.getByLabel("ノードを検索").fill("AN-00001");
  await page.getByRole("button", { name: /AN-00001/ }).click();
  await expect(page).toHaveURL(/view=lineage.*entity=AN-00001/);
  await expect(page.getByTestId("lineage-real-graph")).toBeVisible();
  await expect(page.getByText("熱延用の試験・組織", { exact: true })).toBeVisible();
  const annealedTestGroups = page.locator('.lineage-graph-group-toggle[aria-label^="焼鈍 AN-00001 の"]');
  await expect(annealedTestGroups).toHaveCount(2);
  const annealedTestGroup = page.locator(".lineage-graph-group.group-annealed-microstructure .lineage-graph-group-toggle");
  await expect(annealedTestGroup).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".lineage-graph-node").filter({ hasText: "AMS-00001" })).toHaveCount(0);
  await annealedTestGroup.click();
  await expect(page.getByRole("button", { name: "焼鈍 AN-00001 の組織を折りたたむ" })).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".lineage-graph-node").filter({ hasText: "AMS-00001" })).toBeVisible();
  await annealedTestGroup.click();
  const collapsedAnnealedTestGroup = page.getByRole("button", { name: "焼鈍 AN-00001 の組織を展開する" });
  await expect(collapsedAnnealedTestGroup).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".lineage-graph-node").filter({ hasText: "AMS-00001" })).toHaveCount(0);
  await collapsedAnnealedTestGroup.click();
  await expect(page.getByRole("button", { name: "焼鈍 AN-00001 の組織を折りたたむ" })).toHaveAttribute("aria-expanded", "true");
  const annealedHoleGroup = page.locator(".lineage-graph-group.group-annealed-hole-expansion .lineage-graph-group-toggle");
  await annealedHoleGroup.click();
  await expect(page.getByRole("button", { name: "焼鈍 AN-00001 の穴広げを折りたたむ" })).toHaveAttribute("aria-expanded", "true");
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

test("lineage separates each process condition and test type into its own group", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=HR-00001");
  await expect(page.getByTestId("lineage-real-graph")).toBeVisible();

  const groups = page.locator(".lineage-graph-group-toggle");
  await expect(groups).toHaveCount(4);
  await expect(page.getByRole("button", { name: "熱延 HR-00001 の熱延引張を展開する" })).toBeVisible();
  await expect(page.getByRole("button", { name: "熱延 HR-00001 の熱延組織を展開する" })).toBeVisible();
  await expect(page.getByRole("button", { name: "焼鈍 AN-00001 の組織を展開する" })).toBeVisible();
  await expect(page.getByRole("button", { name: "焼鈍 AN-00001 の穴広げを展開する" })).toBeVisible();
  await expect(page.locator(".lineage-graph-group.group-hot-tensile")).toHaveCount(1);
  await expect(page.locator(".lineage-graph-group.group-hot-microstructure")).toHaveCount(1);
  await expect(page.locator(".lineage-graph-group.group-annealed-microstructure")).toHaveCount(1);
  await expect(page.locator(".lineage-graph-group.group-annealed-hole-expansion")).toHaveCount(1);
  await expect(page.locator(".lineage-graph-edge.process-route")).toHaveCount(1);

  await page.getByRole("button", { name: "熱延 HR-00001 の熱延引張を展開する" }).click();
  await expect(page.getByRole("button", { name: "熱延 HR-00001 の熱延引張を折りたたむ" })).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".lineage-graph-node.group-hot-tensile")).toHaveCount(2);
  await expect(page.locator(".lineage-graph-node").filter({ hasText: "HT-00001" })).toBeVisible();
  await page.getByRole("button", { name: "熱延 HR-00001 の熱延組織を展開する" }).click();
  await expect(page.locator(".lineage-graph-node.group-hot-microstructure")).toHaveCount(1);
  await expect(page.locator(".lineage-graph-node").filter({ hasText: "HMS-00001" })).toBeVisible();
  await page.getByRole("button", { name: "焼鈍 AN-00001 の組織を展開する" }).click();
  await page.getByRole("button", { name: "焼鈍 AN-00001 の穴広げを展開する" }).click();
  await expect(page.locator(".lineage-graph-node.group-annealed-microstructure")).toHaveCount(2);
  await expect(page.locator(".lineage-graph-node.group-annealed-hole-expansion")).toHaveCount(3);
});

test("lineage orders test groups by process condition before test type", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=ME-00001");
  await expect(page.getByTestId("lineage-real-graph")).toBeVisible();
  await expect(page.locator(".lineage-graph-group-toggle")).toHaveCount(9);
  expect(await page.locator(".lineage-graph-group-toggle").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label")))).toEqual([
    "熱延 HR-00001 の熱延引張を展開する",
    "熱延 HR-00001 の熱延組織を展開する",
    "熱延 HR-00002 の熱延引張を展開する",
    "熱延 HR-00002 の熱延組織を展開する",
    "焼鈍 AN-00001 の穴広げを展開する",
    "焼鈍 AN-00001 の組織を展開する",
    "焼鈍 AN-00002 の引張を展開する",
    "焼鈍 AN-00002 の穴広げを展開する",
    "焼鈍 AN-00002 の組織を展開する",
  ]);
});

test("lineage marks implausible observations without hiding raw values", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=HT-00024");
  const detail = page.getByRole("complementary", { name: "選択ノード詳細" });
  await expect(detail.getByText("⚠ 物理範囲外").first()).toBeVisible();
  await expect(detail).toContainText("5223.3");
  await expect(detail).toContainText("妥当範囲 100–2500");
});

test("lineage search recovers from an empty result and opens an exact key", async ({ page }) => {
  await page.goto("/?view=lineage&project=default");
  await page.getByLabel("ノードを検索").fill("does-not-exist");
  await expect(page.getByText("一致するキーはありません。")).toBeVisible();

  await page.getByLabel("ノードを検索").fill("AN-00003");
  await page.getByRole("button", { name: /AN-00003/ }).click();
  await expect(page).toHaveURL(/entity=AN-00003/);
  await expect(page.getByRole("complementary", { name: "選択ノード詳細" })).toContainText("AN-00003");
  await expect(page.getByLabel("ノードを検索")).toHaveValue("AN-00003");
});

test("lineage expands the 40-node window and distinguishes data issues", async ({ page }) => {
  await page.goto("/?view=lineage&project=default&entity=ME-00063");
  await expect(page.getByText("40/62ノード表示")).toBeVisible();
  await page.getByRole("button", { name: /さらに40件読み込む/ }).click();
  await expect(page.getByText("62/62ノード表示")).toBeVisible();
  await expect(page.getByRole("button", { name: /さらに40件読み込む/ })).toHaveCount(0);

  await page.goto("/?view=lineage&project=default&entity=HT-NOT-FOUND");
  const missing = page.locator(".lineage-graph-node.selected");
  await expect(missing).toHaveClass(/missing/);
  await expect(missing).toHaveClass(/invalid-reference/);
  await expect(missing).toContainText("欠損先 / 参照切れ");

  await page.goto("/?view=lineage&project=default&entity=CR-00010");
  await expect(page.locator(".lineage-graph-node.selected")).toHaveClass(/orphan/);
  await expect(page.locator(".lineage-graph-edge")).toHaveCount(0);

  await page.goto("/?view=lineage&project=default&entity=HR-00001");
  await expect(page.locator(".lineage-graph-node.selected")).toHaveClass(/duplicate/);
});

test("copied candidate keeps its source and reports a deleted source", async ({ page, request }) => {
  const projectId = await createCandidateProject(request);
  await page.goto(`/?view=candidates&project=${projectId}`);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  const sourceId = new URL(page.url()).searchParams.get("candidate");
  expect(sourceId).toBeTruthy();
  const sourceName = await page.locator(".candidate-name-table tbody tr.selected-row input").inputValue();

  await page.getByRole("button", { name: `${sourceName}を複製` }).click();
  await expect(page.locator(".candidate-origin")).toContainText("候補コピー");
  const copiedId = new URL(page.url()).searchParams.get("candidate");
  expect(copiedId).toBeTruthy();

  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
  await page.getByRole("button", { name: `${sourceName}を削除`, exact: true }).click();
  await page.goto(`/?view=candidates&project=${projectId}&candidate=${copiedId}`);
  await expect(page.locator(".candidate-origin")).toContainText("コピー元は削除済みか参照できません");
});

test("archived copy source remains navigable", async ({ page, request }) => {
  const projectId = await createCandidateProject(request);
  await page.goto(`/?view=candidates&project=${projectId}`);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  const sourceId = new URL(page.url()).searchParams.get("candidate");
  expect(sourceId).toBeTruthy();
  const sourceName = await page.locator(".candidate-name-table tbody tr.selected-row input").inputValue();

  await page.getByRole("button", { name: `${sourceName}の詳細予測を保存` }).click();
  await expect(page.locator(".notice")).toContainText("詳細予測を保存しました");
  await page.getByRole("button", { name: `${sourceName}を複製` }).click();
  await expect(page.locator(".candidate-origin")).toContainText("候補コピー");
  const copiedId = new URL(page.url()).searchParams.get("candidate");
  expect(copiedId).toBeTruthy();

  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
  await page.getByRole("button", { name: `${sourceName}を削除`, exact: true }).click();
  await page.goto(`/?view=candidates&project=${projectId}&candidate=${copiedId}`);
  await expect(page.locator(".candidate-origin")).not.toContainText("削除済みか参照できません");
  await page.getByRole("button", { name: "作成元へ戻る" }).click();
  await expect(page.locator(".candidate-origin")).toContainText("archive済み候補を参照中");
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`candidate=${sourceId}`));
  await expect(page.locator(".candidate-origin")).toContainText("archive済み候補を参照中");
});
