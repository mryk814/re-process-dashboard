import { useEffect, useState } from "react";
import { numericTaskInputs, type ResolvedTaskDefinition, type TaskDefinitionContract } from "../candidates";
import { workbenchApi, type ApiModelPackage, type ApiProject } from "../../shared/api/workbench-api";
import { ModelTrainingDataInspector } from "./ModelTrainingDataInspector";
import { DeveloperControlCenter } from "./DeveloperControlCenter";
import { suggestedInputRange } from "../../shared/inputRangeDefaults";
import { formatTaskNumber, orderedTaskItems, taskOutputUnit } from "../../shared/taskPresentation";

export type AdminSection = "developer" | "ranges" | "display" | "task" | "model";
export type ProjectSettingsSection = "ranges" | "display" | "task";
type DeveloperTab = "overview" | "training" | "guide" | "diagnostics";

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function rangeNumber(value: number) {
  return value.toLocaleString("ja-JP", { maximumFractionDigits: 4 });
}

function coverageMethodLabel(method: string | null | undefined) {
  if (method === "cross-fitted-oof-residual-quantiles") return "fold外残差分位で評価";
  if (method === "cross-fitted-oof-normal-scale") return "fold外残差尺度で評価";
  if (method === "loo-predictive-interval") return "LOO予測区間で評価";
  if (method === "posterior-predictive-interval") return "事後予測区間で評価";
  return "算出方法未記録";
}

function availabilityStageLabel(
  stage: ResolvedTaskDefinition["availability"]["stage"] | undefined,
) {
  if (stage === "source") return "データソース";
  if (stage === "package") return "モデルPackage";
  if (stage === "runtime") return "実行環境";
  return "利用可否の確認";
}

function FixedReferenceValue({ value }: { value?: string | null }) {
  return value
    ? <code title={value}>{value}</code>
    : <span>記録なし</span>;
}

function CoverageCell({
  quality,
  predictiveFamily,
}: {
  quality: ApiModelPackage["quality_report"]["targets"][number] | undefined;
  predictiveFamily: string;
}) {
  if (predictiveFamily.startsWith("bernoulli")) {
    return <td className="quality-coverage-cell">
      <strong>—</strong>
      <small>分類では非該当</small>
      {quality && <small>独立条件 n={quality.parent_conditions.toLocaleString("ja-JP")}</small>}
    </td>;
  }
  if (!quality) return <td>—</td>;
  const smallSample = quality.parent_conditions < 20;
  return <td className="quality-coverage-cell">
    <span><strong>{number(quality.interval_coverage_90 * 100, 0)}%</strong>{smallSample && <b>参考値</b>}</span>
    <small>
      独立条件 n={quality.parent_conditions.toLocaleString("ja-JP")}
      {quality.interval_coverage_observations ? ` · 評価点 n=${quality.interval_coverage_observations.toLocaleString("ja-JP")}` : ""}
    </small>
    <small>{coverageMethodLabel(quality.interval_coverage_method)}</small>
  </td>;
}

export function DeveloperAdminPage({
  project,
  taskDefinition,
  resolvedTaskDefinition,
  availability,
  readOnly = false,
  initialSection,
  developerTab,
  developerTabError,
  developerGuideId,
  onSectionChange,
  onDeveloperLocationChange,
  onProjectChanged,
  onOpenProfileWorkbench,
}: {
  project: ApiProject | undefined;
  taskDefinition: TaskDefinitionContract | null;
  resolvedTaskDefinition: ResolvedTaskDefinition | null;
  availability?: ResolvedTaskDefinition["availability"];
  readOnly?: boolean;
  initialSection?: AdminSection;
  developerTab?: DeveloperTab;
  developerTabError?: string;
  developerGuideId?: string;
  onSectionChange: (section: AdminSection) => void;
  onDeveloperLocationChange: (tab: DeveloperTab, guideId?: string) => void;
  onProjectChanged: (project: ApiProject) => void;
  onOpenProfileWorkbench: () => void;
}) {
  const [section, setSection] = useState<AdminSection>(initialSection ?? "developer");
  const [modelPackage, setModelPackage] = useState<ApiModelPackage | null>(null);
  const [modelError, setModelError] = useState("");
  useEffect(() => {
    if (!project?.id || section !== "model" || readOnly) return;
    const controller = new AbortController();
    setModelPackage(null);
    setModelError("");
    workbenchApi.modelPackage(project.id)
      .then((item) => { if (!controller.signal.aborted) setModelPackage(item); })
      .catch((cause) => { if (!controller.signal.aborted) setModelError(cause instanceof Error ? cause.message : "モデル情報を取得できませんでした。"); });
    return () => controller.abort();
  }, [project?.id, readOnly, section]);
  useEffect(() => setSection(initialSection ?? "developer"), [initialSection]);
  const allSections: Array<{ id: AdminSection; label: string }> = [
    { id: "developer", label: "開発者ガイド" },
    { id: "ranges", label: "入力範囲" },
    { id: "display", label: "表示桁数" },
    { id: "task", label: "予測タスク定義" },
    { id: "model", label: "モデルと実行環境" },
  ];
  const sections = allSections;
  const visibleSection = section;
  return <div className="admin-workspace">
    <aside className="admin-navigation">
      <span className="overline">開発・管理</span><h2>検証と構成</h2><p>{readOnly ? "利用停止中の構成を読み取り専用で確認します。" : "通常の候補検討では使わない管理情報です。"}</p>
      <nav aria-label="開発・管理メニュー">{sections.map((item) => <button key={item.id} className={visibleSection === item.id ? "active" : ""} onClick={() => { setSection(item.id); onSectionChange(item.id); }}>{item.label}</button>)}</nav>
    </aside>
    <div className="admin-content">
      {readOnly && <section className="admin-availability-diagnostic" role="status" aria-label="予測タスクの利用停止診断">
        <div>
          <span className="overline">読み取り専用</span>
          <h2>固定参照と停止段階を確認してください</h2>
          <p>{availability?.message || "予測タスクを利用できないため、変更操作を停止しています。"}</p>
        </div>
        <dl>
          <div><dt>対象Task</dt><dd><FixedReferenceValue value={taskDefinition?.id ?? project?.task_id} /></dd></div>
          <div><dt>停止段階</dt><dd>{availabilityStageLabel(availability?.stage)}</dd></div>
          <div><dt>Dataset View</dt><dd><FixedReferenceValue value={project?.dataset_view_revision_id} /></dd></div>
          <div><dt>Model Package参照</dt><dd><FixedReferenceValue value={project?.model_package_ref_id} /></dd></div>
          <div><dt>Manifest digest</dt><dd><FixedReferenceValue value={project?.model_package_manifest_digest} /></dd></div>
          <div><dt>診断対象</dt><dd><FixedReferenceValue value={availability?.resource_id} /></dd></div>
          <div><dt>期待する場所</dt><dd><FixedReferenceValue value={availability?.expected_locator} /></dd></div>
        </dl>
        {availability?.recovery_hint && <p><b>復旧の手掛かり:</b> {availability.recovery_hint}</p>}
        <small>保存済み情報と診断は参照できます。入力範囲・表示桁数など、このプロジェクトを変更する操作は無効です。</small>
      </section>}
      {visibleSection === "developer" && <DeveloperControlCenter
        onOpenProfileWorkbench={onOpenProfileWorkbench}
        initialTab={developerTab}
        invalidTabId={developerTabError}
        initialGuideId={developerGuideId}
        projectId={project?.id}
        taskDefinition={taskDefinition}
        onLocationChange={onDeveloperLocationChange}
      />}
      {visibleSection === "ranges" && <InputRangeSettingsPage project={project} taskDefinition={taskDefinition} readOnly={readOnly} onProjectChanged={onProjectChanged} />}
      {visibleSection === "display" && <DisplayDecimalSettingsPage project={project} taskDefinition={taskDefinition} readOnly={readOnly} onProjectChanged={onProjectChanged} />}
      {visibleSection === "task" && <div className="page-panel admin-contract-page">
        <div className="page-intro"><div><h2>予測タスク定義</h2><p>{taskDefinition?.label ?? "読み込み中"}で利用者が入力・確認する項目です。</p></div></div>
        {taskDefinition ? <>
          {taskDefinition.input_groups.map((group) => <section key={group.key}><h3>{group.label}</h3><table className="quality-table"><thead><tr><th>項目</th><th>単位</th><th>入力</th><th>標準範囲</th></tr></thead><tbody>{group.fields.map((field) => <tr key={field.path}><th>{field.label}</th><td>{field.unit ?? "—"}</td><td>{field.editable ? "編集可" : "固定"}</td><td>{field.default_range ? `${rangeNumber(field.default_range.min)}–${rangeNumber(field.default_range.max)}` : field.choices.join(" / ") || "—"}</td></tr>)}</tbody></table></section>)}
          <section><h3>予測特性</h3><div className="admin-output-list">{taskDefinition.outputs.map((output) => <span key={output.key}><b>{output.label}</b>{output.unit} · {output.goal_direction === "at_most" ? "小さい側を目標" : output.goal_direction === "at_least" ? "大きい側を目標" : "方向なし"}</span>)}</div></section>
          <details className="technical-contract"><summary>技術契約を表示</summary><code>{resolvedTaskDefinition?.task_definition.id}</code><span>{resolvedTaskDefinition?.task_definition.schema_version}</span><span>{resolvedTaskDefinition?.runtime_capability.model_package_schema_version}</span></details>
        </> : <p className="empty-evidence">予測タスク定義を読み込んでいます。</p>}
      </div>}
      {visibleSection === "model" && <div className="page-panel admin-model-page">
        <div className="page-intro"><div><h2>モデルと実行環境</h2><p>有効なモデルPackage、検証指標、利用可能なruntimeを確認します。</p></div></div>
        {modelError && <p className="panel-error">{modelError}</p>}
        {modelPackage ? <>
          <div className="admin-model-identity"><span>有効</span><strong>{modelPackage.id}</strong><b>v{modelPackage.version}</b></div>
          <table className="quality-table"><thead><tr><th>特性</th><th>実行方式</th><th>RMSE</th><th>90%予測区間の包含率</th></tr></thead><tbody>{(taskDefinition ? orderedTaskItems(taskDefinition, modelPackage.predictors.map((predictor) => ({ ...predictor, key: predictor.target }))) : modelPackage.predictors.map((predictor) => ({ ...predictor, key: predictor.target }))).map((predictor) => { const quality = modelPackage.quality_report.targets.find((item) => item.target === predictor.target); const unit = taskDefinition ? taskOutputUnit(taskDefinition, predictor.target) : ""; return <tr key={predictor.target}><th>{taskDefinition?.outputs.find((item) => item.key === predictor.target)?.label ?? predictor.target}</th><td>{predictor.runtime_type}</td><td>{quality ? `${taskDefinition ? formatTaskNumber(quality.rmse, taskDefinition, `output.${predictor.target}`, project?.display_decimals) : number(quality.rmse, 2)}${unit ? ` ${unit}` : ""}` : "—"}</td><CoverageCell quality={quality} predictiveFamily={predictor.predictive_family} /></tr>; })}</tbody></table>
          <div className="runtime-list">{modelPackage.supported_runtimes.map((item) => <span className={item.available ? "available" : "optional"} key={item.runtime_type}>{item.runtime_type}{item.available ? " ✓" : " (追加導入)"}</span>)}</div>
          {project?.id && <ModelTrainingDataInspector projectId={project.id} modelPackage={modelPackage} taskDefinition={taskDefinition} />}
          <details className="technical-contract"><summary>識別情報を表示</summary><code>{modelPackage.manifest_sha256}</code></details>
        </> : readOnly
          ? <p className="empty-evidence">利用停止中のruntimeには接続せず、上の固定参照を表示しています。</p>
          : !modelError && <p className="empty-evidence">モデル情報を読み込んでいます。</p>}
      </div>}
    </div>
  </div>;
}

export function DisplayDecimalSettingsPage({ project, taskDefinition, readOnly = false, onProjectChanged }: {
  project: ApiProject | undefined;
  taskDefinition: TaskDefinitionContract | null;
  readOnly?: boolean;
  onProjectChanged: (project: ApiProject) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const items = taskDefinition ? [
    ...numericTaskInputs(taskDefinition).map((input) => ({ key: input.path, label: input.label, unit: input.unit ?? "—", group: input.group === "composition" ? "成分" : "工程条件" })),
    ...taskDefinition.outputs.map((output) => ({ key: `output.${output.key}`, label: output.label, unit: output.unit, group: "予測特性" })),
  ] : [];
  useEffect(() => {
    if (!project || !taskDefinition) return;
    setDraft(Object.fromEntries(items.map((item) => [item.key, String(project.display_decimals?.[item.key] ?? taskDefinition.display_decimals[item.key])])));
    setError("");
  }, [project, taskDefinition]);
  if (!project || !taskDefinition) return <div className="page-panel"><p className="empty-evidence">設定を読み込んでいます。</p></div>;
  const resetDefaults = () => {
    if (readOnly) return;
    setDraft(Object.fromEntries(items.map((item) => [item.key, String(taskDefinition.display_decimals[item.key])])));
  };
  const update = (key: string, value: string) => {
    if (readOnly) return;
    setDraft((current) => ({ ...current, [key]: value }));
  };
  const save = async () => {
    if (readOnly) return;
    const overrides: Record<string, number> = {};
    for (const item of items) {
      const value = Number(draft[item.key]);
      if (!Number.isInteger(value) || value < 0 || value > 8) {
        setError(`${item.label}は0〜8の整数で指定してください。`);
        return;
      }
      if (value !== taskDefinition.display_decimals[item.key]) overrides[item.key] = value;
    }
    setSaving(true);
    setError("");
    try {
      onProjectChanged(await workbenchApi.updateProject(project.id, { ...project, display_decimals: overrides }, { invalidateInference: false }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "表示桁数を保存できませんでした。");
    } finally {
      setSaving(false);
    }
  };
  return <div className="page-panel display-decimal-settings">
    <div className="page-intro"><div><h2>表示桁数</h2><p>表で表示する小数点以下の桁数です。保存値と計算精度は変わりません。</p></div><div className="project-actions"><button className="outline-button" disabled={readOnly} onClick={resetDefaults}>デフォルトに戻す</button><button className="primary-button" disabled={readOnly || saving} onClick={() => void save()}>{saving ? "保存中…" : "保存"}</button></div></div>
    {error && <p className="empty-evidence">{error}</p>}
    <table className="display-decimal-table"><thead><tr><th>区分</th><th>変数</th><th>単位</th><th>既定</th><th>このプロジェクト</th><th>表示例</th></tr></thead><tbody>{items.map((item) => {
      const value = Number(draft[item.key]);
      return <tr key={item.key}><td>{item.group}</td><th>{item.label}</th><td>{item.unit}</td><td className="numeric-cell">{taskDefinition.display_decimals[item.key]}</td><td className="numeric-cell"><input type="number" min="0" max="8" step="1" disabled={readOnly} aria-label={`${item.label}の表示桁数`} value={draft[item.key] ?? ""} onChange={(event) => update(item.key, event.target.value)} /></td><td className="numeric-cell">{Number.isInteger(value) && value >= 0 && value <= 8 ? (0.001245).toFixed(value) : "—"}</td></tr>;
    })}</tbody></table>
  </div>;
}

export function InputRangeSettingsPage({ project, taskDefinition, readOnly = false, onProjectChanged }: {
  project: ApiProject | undefined;
  taskDefinition: TaskDefinitionContract | null;
  readOnly?: boolean;
  onProjectChanged: (project: ApiProject) => void;
}) {
  const [draft, setDraft] = useState<Record<string, { min: string; max: string }>>({});
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!project || !taskDefinition) return;
    setDraft(Object.fromEntries(numericTaskInputs(taskDefinition).filter((input) => input.editable).map((input) => {
      const configured = project.input_ranges?.[input.id] ?? suggestedInputRange(input);
      return [input.id, { min: String(configured.min), max: String(configured.max) }];
    })));
    setError("");
  }, [project, taskDefinition]);
  if (!project || !taskDefinition) return <div className="page-panel"><p className="empty-evidence">設定を読み込んでいます。</p></div>;
  const inputs = numericTaskInputs(taskDefinition).filter((input) => input.editable);
  const update = (id: string, side: "min" | "max", value: string) => {
    if (readOnly) return;
    setDraft((current) => ({ ...current, [id]: { ...current[id], [side]: value } }));
  };
  const resetDefaults = () => {
    if (readOnly) return;
    setDraft(Object.fromEntries(inputs.map((input) => {
      const range = suggestedInputRange(input);
      return [input.id, { min: String(range.min), max: String(range.max) }];
    })));
  };
  const save = async () => {
    if (readOnly) return;
    const inputRanges: Record<string, { min: number; max: number }> = {};
    for (const input of inputs) {
      const range = draft[input.id];
      const min = Number(range?.min);
      const max = Number(range?.max);
      if (!Number.isFinite(min) || !Number.isFinite(max) || min >= max) {
        setError(`${input.label}の許容範囲を確認してください。`);
        return;
      }
      inputRanges[input.id] = { min, max };
    }
    setSaving(true);
    setError("");
    try {
      onProjectChanged(await workbenchApi.updateProject(project.id, { ...project, input_ranges: inputRanges }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存できませんでした。");
    } finally {
      setSaving(false);
    }
  };
  return <div className="page-panel input-range-settings">
    <div className="page-intro"><div><h2>入力範囲設定</h2><p>スライダーと数値入力で使う実用的な範囲を、プロジェクトごとに設定します。</p></div><div className="project-actions"><button className="outline-button" disabled={readOnly} onClick={resetDefaults}>初期値に戻す</button><button className="primary-button" disabled={readOnly || saving} onClick={() => void save()}>{saving ? "保存中…" : "保存"}</button></div></div>
    <p className="settings-explanation"><b>許容範囲</b>は入力可能域、<b>既定の検討範囲</b>はTask側の初期表示です。実際のモデル学習範囲は選択Packageの学習Datasetから実行時に解決します。</p>
    {error && <p className="empty-evidence">{error}</p>}
    <table className="input-range-table"><thead><tr><th>入力項目</th><th>許容最小</th><th>許容最大</th><th>初期値</th><th>既定の検討範囲</th></tr></thead><tbody>{inputs.map((input) => {
      const range = suggestedInputRange(input);
      const training = input.training_range;
      return <tr key={input.id}><th>{input.label}<small>{input.unit}</small></th><td><input type="number" step="any" disabled={readOnly} aria-label={`${input.label}の許容最小`} value={draft[input.id]?.min ?? ""} onChange={(event) => update(input.id, "min", event.target.value)} /></td><td><input type="number" step="any" disabled={readOnly} aria-label={`${input.label}の許容最大`} value={draft[input.id]?.max ?? ""} onChange={(event) => update(input.id, "max", event.target.value)} /></td><td>{rangeNumber(range.min)}–{rangeNumber(range.max)}</td><td>{training ? `${rangeNumber(training.min)}–${rangeNumber(training.max)}` : "—"}</td></tr>;
    })}</tbody></table>
  </div>;
}

export function ProjectScopedSettings({
  project,
  taskDefinition,
  resolvedTaskDefinition,
  availability,
  readOnly = false,
  initialSection = "ranges",
  onSectionChange,
  onProjectChanged,
}: {
  project: ApiProject | undefined;
  taskDefinition: TaskDefinitionContract | null;
  resolvedTaskDefinition: ResolvedTaskDefinition | null;
  availability?: ResolvedTaskDefinition["availability"];
  readOnly?: boolean;
  initialSection?: ProjectSettingsSection;
  onSectionChange: (section: ProjectSettingsSection) => void;
  onProjectChanged: (project: ApiProject) => void;
}) {
  const [section, setSection] = useState<ProjectSettingsSection>(initialSection);
  useEffect(() => setSection(initialSection), [initialSection]);
  const select = (next: ProjectSettingsSection) => {
    setSection(next);
    onSectionChange(next);
  };
  return <section className="project-scientific-settings" aria-label="このProjectの科学設定">
    <div className="page-intro">
      <div>
        <span className="overline">このPROJECT</span>
        <h3>入力・表示・Task設定</h3>
        <p>現在のProjectだけに適用する設定です。workspace内のほかのProjectは変更しません。</p>
      </div>
    </div>
    {readOnly && availability?.status === "unavailable" && (
      <section className="admin-availability-diagnostic" role="status" aria-label="予測タスクの利用停止診断">
        <div>
          <span className="overline">読み取り専用</span>
          <h2>固定参照と停止段階を確認してください</h2>
          <p>{availability.message}</p>
        </div>
        <dl>
          <div><dt>対象Task</dt><dd><FixedReferenceValue value={taskDefinition?.id ?? project?.task_id} /></dd></div>
          <div><dt>停止段階</dt><dd>{availabilityStageLabel(availability.stage)}</dd></div>
          <div><dt>Dataset View</dt><dd><FixedReferenceValue value={project?.dataset_view_revision_id} /></dd></div>
          <div><dt>Model Package参照</dt><dd><FixedReferenceValue value={project?.model_package_ref_id} /></dd></div>
          <div><dt>Manifest digest</dt><dd><FixedReferenceValue value={project?.model_package_manifest_digest} /></dd></div>
          <div><dt>診断対象</dt><dd><FixedReferenceValue value={availability.resource_id} /></dd></div>
          <div><dt>期待する場所</dt><dd><FixedReferenceValue value={availability.expected_locator} /></dd></div>
        </dl>
        {availability.recovery_hint && <p><b>復旧の手掛かり:</b> {availability.recovery_hint}</p>}
        <small>保存済み情報と診断は参照できます。入力範囲・表示桁数など、このProjectを変更する操作は無効です。</small>
      </section>
    )}
    <nav className="developer-tabs" aria-label="Project設定メニュー">
      <button type="button" className={section === "ranges" ? "active" : ""} onClick={() => select("ranges")}>入力範囲</button>
      <button type="button" className={section === "display" ? "active" : ""} onClick={() => select("display")}>表示桁数</button>
      <button type="button" className={section === "task" ? "active" : ""} onClick={() => select("task")}>予測タスク定義</button>
    </nav>
    {section === "ranges" && <InputRangeSettingsPage project={project} taskDefinition={taskDefinition} readOnly={readOnly} onProjectChanged={onProjectChanged} />}
    {section === "display" && <DisplayDecimalSettingsPage project={project} taskDefinition={taskDefinition} readOnly={readOnly} onProjectChanged={onProjectChanged} />}
    {section === "task" && <div className="page-panel admin-contract-page">
      <div className="page-intro"><div><h2>予測タスク定義</h2><p>{taskDefinition?.label ?? "読み込み中"}で利用者が入力・確認する項目です。</p></div></div>
      {taskDefinition ? <>
        {taskDefinition.input_groups.map((group) => <section key={group.key}><h3>{group.label}</h3><table className="quality-table"><thead><tr><th>項目</th><th>単位</th><th>入力</th><th>標準範囲</th></tr></thead><tbody>{group.fields.map((field) => <tr key={field.path}><th>{field.label}</th><td>{field.unit ?? "—"}</td><td>{field.editable ? "編集可" : "固定"}</td><td>{field.default_range ? `${rangeNumber(field.default_range.min)}–${rangeNumber(field.default_range.max)}` : field.choices.join(" / ") || "—"}</td></tr>)}</tbody></table></section>)}
        <section><h3>予測特性</h3><div className="admin-output-list">{taskDefinition.outputs.map((output) => <span key={output.key}><b>{output.label}</b>{output.unit} · {output.goal_direction === "at_most" ? "小さい側を目標" : output.goal_direction === "at_least" ? "大きい側を目標" : "方向なし"}</span>)}</div></section>
        <details className="technical-contract"><summary>技術契約を表示</summary><code>{resolvedTaskDefinition?.task_definition.id}</code><span>{resolvedTaskDefinition?.task_definition.schema_version}</span><span>{resolvedTaskDefinition?.runtime_capability.model_package_schema_version}</span></details>
      </> : <p className="empty-evidence">予測タスク定義を読み込んでいます。</p>}
    </div>}
  </section>;
}
