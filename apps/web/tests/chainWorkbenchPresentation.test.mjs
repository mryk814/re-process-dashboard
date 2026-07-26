import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(
  new URL("../src/features/workbench/ChainWorkbenchPage.tsx", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../src/features/workbench/chain-workbench.css", import.meta.url),
  "utf8",
);
const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const session = readFileSync(
  new URL("../src/features/workbench/useWorkbenchSession.ts", import.meta.url),
  "utf8",
);

test("Chain project has a dedicated candidate work surface", () => {
  assert.match(app, /tab === "candidates" && chainProject/);
  assert.match(app, /<ChainWorkbenchPage/);
  assert.match(source, /CHAIN WORKBENCH/);
  assert.match(source, /\(\["A", "B", "C"\] as const\)/);
});

test("freshness and actual source are separate labels", () => {
  for (const label of ["最新", "再計算中", "古い", "失敗"]) {
    assert.match(source, new RegExp(label));
  }
  assert.match(source, /実測照合あり/);
  assert.match(source, /別analysisあり/);
  assert.match(source, /実測を使用した別分析/);
  assert.match(source, /通常Chainを上書きしません/);
});

test("Chain output tables use pinned presentation metadata and state uncertainty beside values", () => {
  assert.match(source, /stageB\?\.output_definitions/);
  assert.match(source, /stageC\?\.output_definitions/);
  assert.match(source, /definition\.label/);
  assert.match(source, /definition\.unit\.trim\(\)/);
  assert.match(source, /definition\.display_decimals/);
  assert.match(source, /displayDecimalOverrides\?\.\[`output\.\$\{definition\.key\}`\]/);
  assert.match(source, /useProjectOverride\s*\?\s*displayDecimalOverrides/);
  assert.match(source, /predictionCell\(stageCPredictions\[definition\.key\], definition, true\)/);
  assert.match(source, /標準偏差 ±/);
  assert.doesNotMatch(source, /モデル由来 ±/);
  assert.match(source, /区間なし/);
  assert.doesNotMatch(source, /<th>\{key\}<\/th>/);
  assert.doesNotMatch(source, /Object\.keys\(stageCPredictions\)/);
  assert.match(source, /chain-snapshot-output-table/);
  assert.match(source, /stage\.stage_id === "A"[\s\S]*JSON\.stringify\(stage\.result/);
  assert.match(source, /execution\?\.chain_revision_digest === viewedSnapshot\.identity\.chain_revision_digest/);
  assert.match(source, /stageBDefinitions\.map\(\(definition\)/);
  assert.doesNotMatch(source, /actual-value-grid[^]*<span>\{key\}<\/span>/);
  assert.match(app, /displayDecimalOverrides=\{activeProject\?\.display_decimals\}/);
});

test("editing keeps reserved layout surfaces while recomputation changes state", () => {
  assert.match(source, /編集停止後に自動保存・再計算します/);
  assert.match(source, /window\.setTimeout/);
  assert.match(styles, /\.chain-status-line\s*\{[^}]*min-height:/s);
  assert.match(styles, /\.chain-result-card\s*\{[^}]*min-height:/s);
  assert.match(styles, /\.chain-stage-rail\s*\{[^}]*position:\s*sticky/s);
  assert.match(source, /candidateRequests\.current\.isCurrent\(token\)/);
  assert.match(source, /requestSequence\.current \+= 1/);
});

test("actual-conditioned analysis requires an immutable comparison snapshot", () => {
  assert.match(source, /snapshot\.identity\.candidate_revision === selected\?\.revision/);
  assert.match(source, /variant\.identity\.base_candidate_revision === selected\?\.revision/);
  assert.match(source, /comparison_snapshot_id:\s*comparisonSnapshot\.snapshot_id/);
  assert.match(source, /実測Bを使ってStage Cを別分析/);
  assert.match(source, /不足分を予測値で補いません/);
  assert.match(source, /stageBKeys\.map\(\(key\) => \[key, ""\]\)/);
  assert.doesNotMatch(source, /String\(stageBPredictions\[key\]/);
  assert.match(source, /!actualDraft\[key\]\?\.trim\(\)/);
  assert.match(source, /<details className="chain-actual-panel">/);
  assert.match(source, /<details className="chain-variant-history">/);
});

test("blank numeric drafts never become zero and Stage A reuses the sparse blend editor", () => {
  assert.match(source, /if \(!rawValue\.trim\(\)\)/);
  assert.match(source, /空欄は0として保存しません/);
  assert.match(source, /LatestSaveQueue<ApiCandidate>/);
  assert.match(source, /rebaseChangedFields/);
  assert.match(source, /saveQueue\.current\.supersede\(selected\.id\)/);
  assert.match(source, /<BlendEditorPanel/);
  assert.match(source, /chainMode/);
  assert.match(source, /contract\.starter_candidate/);
  assert.match(source, /固定契約から基準配合を作成/);
});

test("every Chain external input is rendered and edited from the resolved contract", () => {
  assert.match(source, /contract\?\.external_inputs/);
  assert.match(source, /scalarInputDefinitions\.map\(\(definition\)/);
  assert.match(source, /data-chain-external-path=\{definition\.external_path\}/);
  assert.match(source, /data-chain-external-path=\{blendInputDefinition\.external_path\}/);
  assert.match(source, /definition\.candidate_path/);
  assert.match(source, /definition\.label/);
  assert.match(source, /definition\.unit/);
  assert.match(source, /definition\.allowed_range/);
  assert.match(source, /definition\.choices/);
  assert.match(source, /definition\.editable/);
  assert.match(source, /definition\.first_affected_stage_id/);
  assert.match(source, /getCandidateInputValue/);
  assert.match(source, /setCandidateInputValue/);
  assert.match(
    source,
    /readOnly[\s\S]*workbenchApi\.chainCandidateContract\(projectId\)[\s\S]*workbenchApi\.listChainCandidates\(projectId\)/,
  );
  assert.doesNotMatch(source, /editProcess/);
  assert.doesNotMatch(
    source,
    /heat_input_kj_per_mm|voltage_v|gas_flow_l_per_min|shielding_gas|welding_position|preheat_temp_c|test_temperature_c|test_solution/,
  );
  assert.doesNotMatch(source, /path ===/);
});

test("Chain candidate identity survives reload and same-project history navigation", () => {
  assert.match(session, /onLocationReplace\(projectId, candidateId\)/);
  assert.match(
    session,
    /projectId === activeProjectIdRef\.current[\s\S]*identity_kind === "chain"[\s\S]*return;/,
  );
  assert.match(source, /initialCandidateId === selectedId/);
  assert.match(
    source,
    /candidateRequests\.current\.activate\(projectId, initialCandidateId\)/,
  );
  assert.match(source, /loadCandidateEvidence\(initialCandidateId, candidateToken\)/);
});
