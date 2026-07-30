import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = (relativePath) => readFile(new URL(relativePath, import.meta.url), "utf8");

test("developer overview uses a semantic staged flow and collapsible filtered projects", async () => {
  const content = await source("../src/features/admin/DeveloperControlCenter.tsx");
  assert.match(content, /<ol className="developer-flow"/);
  assert.match(content, /filterDeveloperOverviewItems/);
  assert.match(content, /type="search"/);
  assert.match(content, /filteredOverviewItems\.map\(\(item\) => <details/);
  assert.match(content, /条件に合うProjectはありません/);
  assert.doesNotMatch(content, /flow-down|flow-project-row|↘|↙/);
});

test("training data distinguishes unopened, loading, empty, and loaded states", async () => {
  const content = await source("../src/features/admin/ModelTrainingDataInspector.tsx");
  assert.match(content, /"未読込"/);
  assert.match(content, /"読み込み中"/);
  assert.match(content, /"0行 · データなし"/);
  assert.match(content, /page && page\.total > 0 && !error/);
  assert.match(content, /この段階に該当する行はありません/);
});

test("observation training inspector exposes family cohorts, split groups, and exclusions", async () => {
  const center = await source("../src/features/admin/DeveloperControlCenter.tsx");
  const inspector = await source("../src/features/admin/ObservationTrainingInspector.tsx");
  assert.match(center, /\["training", "学習View"\]/);
  assert.match(inspector, /観測family別 学習View/);
  assert.match(inspector, /relationは結合索引としてだけ使い/);
  assert.match(inspector, /施工group/);
  assert.match(inspector, /目的変数で除外/);
  assert.match(inspector, /page\.exclusion_reasons/);
  assert.match(inspector, /Object\.keys\(page\?\.rows\[0\]\?\.inputs/);
  assert.match(inspector, /row\.provenance\.entity_keys\.weld_metal/);
  assert.match(inspector, /学習Profile/);
  assert.match(inspector, /profiles\.find\(\(item\) => item\.profile_id === profileId\)/);
  assert.match(inspector, /disabled=\{profiles\.length <= 1\}/);
  assert.match(inspector, /setProfileId\(nextProfileId\)/);
  assert.match(inspector, /setFamily\(nextFamily\?\.family \?\? ""\)/);
  assert.match(inspector, /setTarget\(nextFamily\?\.targets\[0\]\?\.target \?\? ""\)/);
  assert.match(inspector, /resetPage\(\)/);
  assert.match(inspector, /current\.profile_id !== next\.profile_id/);
  assert.match(inspector, /\[page\?\.family, page\?\.profile_id, page\?\.target\]/);
});

test("data library collapses an empty comparison area and moves state changes into management menus", async () => {
  const content = await source("../src/features/data-library/DataLibraryPage.tsx");
  assert.match(content, /comparisonSets\.length === 0 \? "comparison-empty"/);
  assert.match(content, /className="resource-manage-menu"/);
  assert.match(content, /const \[samplesOpen, setSamplesOpen\] = useState\(false\)/);
  assert.doesNotMatch(content, /managedDatasets\.length === 0\) setSamplesOpen\(true\)/);
  assert.match(content, /利用停止にする/);
  assert.match(content, /利用可能に戻す/);
  assert.match(content, /件のプロジェクトが参照中のため利用停止できません/);
  assert.match(content, /item\.storage_scope === "personal"/);
  assert.match(content, /自分のモデル/);
  assert.match(content, /同梱モデル/);
  assert.match(content, /WORKBENCH_MODEL_STORE_PATH/);
  assert.match(content, /--store \$modelStore/);
  assert.match(content, /個人Taskとモデルを再読込/);
  assert.doesNotMatch(content, /昇格済みモデルを再読込/);
  for (const field of [
    "connector_id",
    "training_snapshot_id",
    "training_snapshot_digest",
    "training_selection_policy_digest",
  ]) {
    assert.match(
      content,
      new RegExp(`typeof identity\\.${field} === "string"`),
      `${field} is required before exposing the Package to Snapshot link`,
    );
  }
  assert.match(content, /snapshotDetail\.snapshot\.snapshot_digest === link\.snapshotDigest/);
  assert.match(content, /snapshotDetail\.snapshot\.selection_policy_digest === link\.selectionPolicyDigest/);
  assert.doesNotMatch(content, /!link\.snapshotDigest|!link\.selectionPolicyDigest/);
  const lifecycle = await source("../src/features/data-library/SourceLifecycleSection.tsx");
  assert.match(lifecycle, /Snapshot採用 \/ 対象外/);
  assert.match(lifecycle, /Snapshot対象外/);
  assert.match(lifecycle, /policyによる追加除外/);
  assert.doesNotMatch(lifecycle, /<span>追加除外 <b>\{selectedTraining\.excluded_row_count\}/);
});

test("Profile Workbench keeps numbering in one stepper and states the next action", async () => {
  const content = await source("../src/features/data-library/ProfileWorkbenchPage.tsx");
  assert.match(content, /className="profile-next-action"/);
  assert.match(content, /aria-current=\{currentStep === index \+ 1 \? "step"/);
  assert.doesNotMatch(content, /<b>1<\/b><span><strong>/);
  assert.doesNotMatch(content, />3  内容を確認</);
  assert.doesNotMatch(content, />4  この内容で登録</);
});

test("every Profile Workbench step is a state the flow can actually reach", async () => {
  const content = await source("../src/features/data-library/ProfileWorkbenchPage.tsx");
  const steps = content.match(/const steps = \[([^\]]*)\];/)?.[1];
  assert.ok(steps, "step labels are declared in one place");
  const stepCount = steps.split(",").length;
  const currentStep = content.match(/const currentStep = ([^;]*);/)?.[1];
  assert.ok(currentStep, "the current step is derived in one place");
  const reachable = new Set([...currentStep.matchAll(/\d+/g)].map((match) => Number(match[0])));
  assert.deepEqual(
    [...reachable].sort(),
    Array.from({ length: stepCount }, (_, index) => index + 1),
    "each declared step is reachable by currentStep",
  );
  assert.match(steps, /"対応付け"/);
  assert.match(steps, /"検証"/);
});

test("the data library names prediction tasks with the contract label, not the internal id", async () => {
  const hook = await source("../src/shared/useTaskLabels.ts");
  const library = await source("../src/features/data-library/DataLibraryPage.tsx");
  const workbench = await source("../src/features/data-library/ProfileWorkbenchPage.tsx");
  assert.match(hook, /listTaskDefinitions/);
  assert.match(hook, /labels\.get\(taskId\) \?\? taskId/);
  for (const content of [library, workbench]) {
    assert.match(content, /useTaskLabels\(\)/);
  }
  assert.doesNotMatch(library, /<span>\{item\.task_id\}<\/span>/);
  assert.doesNotMatch(library, /value=\{taskId\}>\{taskId\}</);
  assert.doesNotMatch(library, /item\.supported_task_ids\.join\(" \/ "\) : "未定義"/);
  assert.doesNotMatch(workbench, /対応Prediction Task/);
  assert.match(workbench, /対応する予測タスク/);
});
