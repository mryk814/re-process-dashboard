import { useMemo, useState } from "react";
import {
  comparisonRows,
  formatBytes,
  formatMetric,
  latencyLabel,
  latestAttempts,
  type PlaygroundAttemptView,
} from "./modelPlaygroundPresentation";

export type PlaygroundRecipeView = Readonly<{
  recipeId: string;
  label: string;
  lifecycle: "production" | "candidate" | "experimental" | "unavailable" | "no_adopt" | "specialized";
  availability: string;
  reasons: readonly string[];
  comparisonRole: "baseline" | "candidate" | "specialized";
  requiredDependency?: string | null;
  trainingCost: "light" | "moderate" | "high";
  capabilities: readonly string[];
  taskStructure: "standard_independent_targets" | "task_specific_specialized";
  hypothesisLabel?: string;
  inferenceLabel: string;
  executable: boolean;
}>;

export type ModelPlaygroundRunView = Readonly<{
  runId: string;
  revision: number;
  taskId: string;
  taskLabel: string;
  trainingSnapshotId: string;
  contextDigest: string;
  validationLabel: string;
  targets: readonly string[];
  computeBudget: "quick" | "standard" | "research";
  recipes: readonly PlaygroundRecipeView[];
  attempts: readonly PlaygroundAttemptView[];
  warnings: readonly string[];
  adoptionMemo?: Readonly<{
    decision: "adopt" | "no_adopt" | "continue_research";
    recipeId?: string | null;
    rationale: string;
  }>;
}>;

export type ModelPlaygroundPreviewView = Readonly<{
  taskId: string;
  taskLabel: string;
  trainingSnapshotId: string;
  targets: readonly string[];
  validationLabel: string;
  recipes: readonly PlaygroundRecipeView[];
}>;

export type ModelPlaygroundPageState =
  | Readonly<{ kind: "loading" }>
  | Readonly<{ kind: "error"; message: string }>
  | Readonly<{ kind: "preview"; preview: ModelPlaygroundPreviewView }>
  | Readonly<{ kind: "run"; run: ModelPlaygroundRunView }>;

const lifecycleLabels: Record<PlaygroundRecipeView["lifecycle"], string> = {
  production: "STANDARD",
  candidate: "CANDIDATE",
  experimental: "EXPERIMENTAL",
  unavailable: "UNAVAILABLE",
  no_adopt: "NO ADOPT",
  specialized: "SPECIALIZED",
};

const budgetLabels = {
  quick: ["Quick comparison", "短い比較。採用判断には追加証拠が必要です。"],
  standard: ["Standard evidence", "固定Validationで通常の比較証拠を残します。"],
  research: ["Research", "高コスト。研究候補の診断を厚くします。"],
} as const;

function shortIdentity(value: string): string {
  return value.length > 24 ? `${value.slice(0, 13)}…${value.slice(-8)}` : value;
}

function RecipeCard({
  recipe,
  selected,
  selectionDisabled,
  onSelectionChange,
}: {
  recipe: PlaygroundRecipeView;
  selected: boolean;
  selectionDisabled: boolean;
  onSelectionChange?: (selected: boolean) => void;
}) {
  const reasonId = `playground-recipe-${recipe.recipeId.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`;
  return <article className={`playground-recipe-card lifecycle-${recipe.lifecycle}`}>
    <header>
      <div>
        <span className="playground-status-chip">{lifecycleLabels[recipe.lifecycle]}</span>
        <h3>{recipe.label}</h3>
        <code>{recipe.recipeId}</code>
      </div>
      {onSelectionChange && <label className="playground-recipe-select">
        <input
          type="checkbox"
          checked={selected}
          disabled={selectionDisabled || !recipe.executable}
          aria-describedby={reasonId}
          onChange={(event) => onSelectionChange(event.target.checked)}
        />
        比較する
      </label>}
    </header>
    <p id={reasonId}>{recipe.reasons.join("。")}</p>
    <dl className="playground-compact-facts">
      <div><dt>役割</dt><dd>{recipe.comparisonRole === "baseline" ? "Baseline" : recipe.comparisonRole}</dd></div>
      <div><dt>Cost</dt><dd>{recipe.trainingCost}</dd></div>
      <div><dt>Dependency</dt><dd>{recipe.requiredDependency || "標準環境"}</dd></div>
      <div><dt>Inference</dt><dd>{recipe.inferenceLabel}</dd></div>
    </dl>
    <div className="playground-capabilities" aria-label="予測能力">
      {recipe.capabilities.map((capability) => <span key={capability}>{capability}</span>)}
    </div>
    {recipe.hypothesisLabel && <p className="playground-hypothesis">
      <strong>Hypothesis Card</strong>{recipe.hypothesisLabel}
    </p>}
    {recipe.taskStructure === "task_specific_specialized" && <p className="playground-specialized-note">
      Task固有構造を使います。標準baselineと同じものとして扱いません。
    </p>}
  </article>;
}

function SetupSurface({
  preview,
  busy,
  onCreateRun,
}: {
  preview: ModelPlaygroundPreviewView;
  busy: boolean;
  onCreateRun?: (recipeIds: readonly string[], budget: "quick" | "standard" | "research") => void;
}) {
  const initial = preview.recipes
    .filter((recipe) => recipe.executable)
    .slice(0, 2)
    .map((recipe) => recipe.recipeId);
  const [selected, setSelected] = useState<readonly string[]>(initial);
  const [budget, setBudget] = useState<"quick" | "standard" | "research">("standard");
  const selectedRecipes = preview.recipes.filter((recipe) => selected.includes(recipe.recipeId));
  const hasBaseline = selectedRecipes.some((recipe) => recipe.comparisonRole === "baseline");
  const canCreate = selected.length >= 2 && hasBaseline && !busy;

  return <>
    <section className="playground-fixed-context" aria-labelledby="playground-context-heading">
      <div>
        <span className="overline">FIXED COMPARISON CONTEXT</span>
        <h2 id="playground-context-heading">{preview.taskLabel}</h2>
        <code>{preview.taskId}</code>
      </div>
      <dl>
        <div><dt>Training Snapshot</dt><dd>{preview.trainingSnapshotId}</dd></div>
        <div><dt>Targets</dt><dd>{preview.targets.join(" / ")}</dd></div>
        <div><dt>Validation</dt><dd>{preview.validationLabel}</dd></div>
      </dl>
    </section>
    <section aria-labelledby="playground-recipes-heading">
      <div className="playground-section-heading">
        <div><span className="overline">1 · RECIPES</span><h2 id="playground-recipes-heading">比較する仮説を選ぶ</h2></div>
        <span>{selected.length}件選択</span>
      </div>
      {!hasBaseline && <p className="playground-warning" role="alert">研究候補だけでは比較を開始できません。Baselineを1件含めてください。</p>}
      <div className="playground-recipe-grid">
        {preview.recipes.map((recipe) => <RecipeCard
          key={recipe.recipeId}
          recipe={recipe}
          selected={selected.includes(recipe.recipeId)}
          selectionDisabled={busy}
          onSelectionChange={(checked) => setSelected((current) =>
            checked ? [...current, recipe.recipeId] : current.filter((id) => id !== recipe.recipeId))}
        />)}
      </div>
    </section>
    <section className="playground-budget" aria-labelledby="playground-budget-heading">
      <div><span className="overline">2 · COMPUTE BUDGET</span><h2 id="playground-budget-heading">証拠の深さ</h2></div>
      <div className="playground-budget-options">
        {(Object.keys(budgetLabels) as Array<keyof typeof budgetLabels>).map((preset) => <label key={preset}>
          <input type="radio" name="playground-budget" value={preset} checked={budget === preset} disabled={busy} onChange={() => setBudget(preset)} />
          <span><strong>{budgetLabels[preset][0]}</strong><small>{budgetLabels[preset][1]}</small></span>
        </label>)}
      </div>
      <button className="primary-button" type="button" disabled={!canCreate || !onCreateRun} onClick={() => onCreateRun?.(selected, budget)}>
        {busy ? "Runを作成中…" : "固定identityでRunを作成"}
      </button>
      {selected.length < 2 && <p className="model-action-reason">実行可能なrecipeを2件以上選択してください。</p>}
    </section>
  </>;
}

function RunSurface({
  run,
  selectedTarget,
  busyAttemptId,
  onTargetChange,
  onRetry,
  onCreateNewRun,
  onRegister,
  onSaveMemo,
}: {
  run: ModelPlaygroundRunView;
  selectedTarget?: string;
  busyAttemptId?: string;
  onTargetChange?: (target: string) => void;
  onRetry?: (attempt: PlaygroundAttemptView) => void;
  onCreateNewRun?: () => void;
  onRegister?: (attempt: PlaygroundAttemptView) => void;
  onSaveMemo?: (memo: { decision: "adopt" | "no_adopt" | "continue_research"; recipeId?: string; rationale: string }) => void;
}) {
  const target = run.targets.includes(selectedTarget ?? "") ? selectedTarget! : run.targets[0];
  const attempts = useMemo(() => latestAttempts(run.attempts), [run.attempts]);
  const completed = attempts.filter((attempt) => attempt.status === "completed");
  const rows = comparisonRows(run.attempts, target);
  const [memoDecision, setMemoDecision] = useState<"adopt" | "no_adopt" | "continue_research">(run.adoptionMemo?.decision ?? "continue_research");
  const [memoRecipe, setMemoRecipe] = useState(run.adoptionMemo?.recipeId ?? "");
  const [memoRationale, setMemoRationale] = useState(run.adoptionMemo?.rationale ?? "");
  const [copiedPath, setCopiedPath] = useState("");

  async function copyPath(path: string) {
    await navigator.clipboard.writeText(path);
    setCopiedPath(path);
  }

  return <>
    <section className="playground-fixed-context" aria-labelledby="playground-run-heading">
      <div>
        <span className="overline">MODEL EXPLORATION RUN</span>
        <h2 id="playground-run-heading">{run.taskLabel}</h2>
        <code>{run.runId}</code>
      </div>
      <dl>
        <div><dt>Training Snapshot</dt><dd>{run.trainingSnapshotId}</dd></div>
        <div><dt>Validation</dt><dd>{run.validationLabel}</dd></div>
        <div><dt>Budget</dt><dd>{budgetLabels[run.computeBudget][0]}</dd></div>
        <div><dt>Context digest</dt><dd title={run.contextDigest}>{shortIdentity(run.contextDigest)}</dd></div>
      </dl>
    </section>
    {run.warnings.map((warning) => <p className="playground-warning" role="status" key={warning}>{warning}</p>)}
    <section aria-labelledby="playground-progress-heading">
      <div className="playground-section-heading">
        <div><span className="overline">BUILD RECEIPTS</span><h2 id="playground-progress-heading">Recipeごとの進捗</h2></div>
        <span>{completed.length} / {attempts.length} 完了</span>
      </div>
      <div className="playground-attempt-list">
        {attempts.map((attempt) => <article className={`playground-attempt status-${attempt.status}`} key={attempt.attemptId}>
          <header>
            <div><span className="playground-status-chip">{attempt.status.toUpperCase()}</span><h3>{attempt.recipeLabel}</h3></div>
            <code>attempt {attempt.sequence}</code>
          </header>
          {attempt.status === "running" && <p role="status">固定済みcohortでbuild／verifyしています。完了済みrecipeの結果は保持されます。</p>}
          {attempt.failure && <div className="playground-failure" role="alert"><strong>{attempt.failure.message}</strong><span>{attempt.failure.recoveryHint}</span></div>}
          {attempt.status === "completed" && <dl className="playground-compact-facts">
            <div><dt>Build</dt><dd>{attempt.buildSeconds?.toFixed(1) ?? "—"} s</dd></div>
            <div><dt>Peak memory</dt><dd>{formatBytes(attempt.peakMemoryBytes)}</dd></div>
            <div><dt>Artifact</dt><dd>{formatBytes(attempt.artifactSizeBytes)}</dd></div>
            <div><dt>Prediction latency</dt><dd>{latencyLabel(attempt.predictionLatencyMs)}</dd></div>
          </dl>}
          <div className="model-asset-actions">
            {(attempt.status === "failed" || attempt.status === "interrupted") && <button className="outline-button" type="button" disabled={busyAttemptId === attempt.attemptId || !onRetry} onClick={() => onRetry?.(attempt)}>同じidentityで再試行</button>}
            {(attempt.status === "failed" || attempt.status === "interrupted") && <button className="text-button" type="button" disabled={!onCreateNewRun} onClick={onCreateNewRun}>条件を選び直して別Runを作成</button>}
            {attempt.status === "completed" && !attempt.registration && <button className="outline-button" type="button" disabled={busyAttemptId === attempt.attemptId || !onRegister} onClick={() => onRegister?.(attempt)}>Model Libraryへ登録</button>}
          </div>
          {attempt.registration && attempt.packagePath && <div className="playground-registration" role="status">
            <strong>Model Libraryに登録済み</strong>
            <span>active Packageは変更していません。</span>
            <label>Package locator<code>{attempt.packagePath}</code></label>
            <button className="text-button" type="button" onClick={() => void copyPath(attempt.packagePath!)}>
              {copiedPath === attempt.packagePath ? "コピー済み" : "locatorをコピー"}
            </button>
          </div>}
        </article>)}
      </div>
    </section>
    <section aria-labelledby="playground-comparison-heading">
      <div className="playground-section-heading">
        <div><span className="overline">SAME COHORT COMPARISON</span><h2 id="playground-comparison-heading">結果を並べて読む</h2></div>
        <label className="playground-target-select">Target
          <select value={target} onChange={(event) => onTargetChange?.(event.target.value)}>
            {run.targets.map((item) => <option value={item} key={item}>{item}</option>)}
          </select>
        </label>
      </div>
      {completed.length < 2
        ? <p className="playground-empty-comparison">比較には2件以上の完了結果が必要です。失敗したrecipe以外の証拠は保持されています。</p>
        : <div className="playground-comparison-scroll"><table className="playground-comparison-table">
          <thead><tr><th scope="col">Evidence</th>{completed.map((attempt) => <th scope="col" key={attempt.recipeId}>{attempt.recipeLabel}</th>)}</tr></thead>
          <tbody>
            <tr><th scope="row">Inference</th>{completed.map((attempt) => <td key={attempt.recipeId}>{attempt.targets.find((item) => item.targetKey === target)?.inferenceLabel ?? "—"}</td>)}</tr>
            <tr><th scope="row">Interval semantics</th>{completed.map((attempt) => <td key={attempt.recipeId}>{attempt.targets.find((item) => item.targetKey === target)?.intervalSemantics ?? "—"}</td>)}</tr>
            <tr><th scope="row">Capabilities</th>{completed.map((attempt) => <td key={attempt.recipeId}>{attempt.capabilities.join(" / ") || "—"}</td>)}</tr>
            {rows.map((row) => <tr key={row.metric}><th scope="row">{row.metric}</th>{completed.map((attempt) => <td key={attempt.recipeId}>{formatMetric(row.values[attempt.recipeId] ?? null)}</td>)}</tr>)}
          </tbody>
        </table></div>}
      <p className="playground-comparison-note">単一scoreや自動winnerは作りません。性能、不確かさ、計算量、capabilityを別々に判断します。</p>
    </section>
    <section className="playground-memo" aria-labelledby="playground-memo-heading">
      <div><span className="overline">ADOPTION MEMO</span><h2 id="playground-memo-heading">判断を結果と分けて保存</h2></div>
      <div className="playground-memo-fields">
        <label>判断<select value={memoDecision} onChange={(event) => setMemoDecision(event.target.value as typeof memoDecision)}>
          <option value="continue_research">検討を継続</option><option value="adopt">採用候補</option><option value="no_adopt">採用しない</option>
        </select></label>
        {memoDecision === "adopt" && <label>Recipe<select value={memoRecipe} onChange={(event) => setMemoRecipe(event.target.value)}>
          <option value="">選択してください</option>{completed.map((attempt) => <option key={attempt.recipeId} value={attempt.recipeId}>{attempt.recipeLabel}</option>)}
        </select></label>}
        <label className="playground-rationale">根拠<textarea value={memoRationale} maxLength={4000} rows={3} onChange={(event) => setMemoRationale(event.target.value)} /></label>
      </div>
      <button className="primary-button" type="button" disabled={!onSaveMemo || !memoRationale.trim() || (memoDecision === "adopt" && !memoRecipe)} onClick={() => onSaveMemo?.({ decision: memoDecision, recipeId: memoDecision === "adopt" ? memoRecipe : undefined, rationale: memoRationale.trim() })}>Memoを保存</button>
    </section>
  </>;
}

export function ModelPlaygroundPage({
  state,
  actionError,
  selectedTarget,
  busy = false,
  busyAttemptId,
  onBack,
  onRetryLoad,
  onCreateRun,
  onTargetChange,
  onRetry,
  onCreateNewRun,
  onRegister,
  onSaveMemo,
}: {
  state: ModelPlaygroundPageState;
  actionError?: string;
  selectedTarget?: string;
  busy?: boolean;
  busyAttemptId?: string;
  onBack: () => void;
  onRetryLoad?: () => void;
  onCreateRun?: (recipeIds: readonly string[], budget: "quick" | "standard" | "research") => void;
  onTargetChange?: (target: string) => void;
  onRetry?: (attempt: PlaygroundAttemptView) => void;
  onCreateNewRun?: () => void;
  onRegister?: (attempt: PlaygroundAttemptView) => void;
  onSaveMemo?: (memo: { decision: "adopt" | "no_adopt" | "continue_research"; recipeId?: string; rationale: string }) => void;
}) {
  return <section className="model-playground-page">
    <header className="model-playground-header">
      <div><span className="overline">MODEL PLAYGROUND</span><h1>同じデータでモデル仮説を比較する</h1><p>Task・Training Snapshot・Validationを固定し、active Packageや既存Projectを変更せずに証拠を作ります。</p></div>
      <button className="outline-button" type="button" onClick={onBack}>Model Libraryへ戻る</button>
    </header>
    {actionError && <p className="playground-warning" role="alert">{actionError}</p>}
    {state.kind === "loading" && <div className="model-playground-state" role="status"><strong>固定identityとRunを読み込んでいます</strong><span>完了済みrecipeの証拠は再計算しません。</span></div>}
    {state.kind === "error" && <div className="model-playground-state error" role="alert"><strong>Model Playgroundを読み込めません</strong><span>{state.message}</span><button className="primary-button" type="button" onClick={onRetryLoad}>再試行</button></div>}
    {state.kind === "preview" && <SetupSurface key={`${state.preview.taskId}:${state.preview.trainingSnapshotId}`} preview={state.preview} busy={busy} onCreateRun={onCreateRun} />}
    {state.kind === "run" && <RunSurface key={state.run.runId} run={state.run} selectedTarget={selectedTarget} busyAttemptId={busyAttemptId} onTargetChange={onTargetChange} onRetry={onRetry} onCreateNewRun={onCreateNewRun} onRegister={onRegister} onSaveMemo={onSaveMemo} />}
  </section>;
}
