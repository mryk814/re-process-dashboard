import { useEffect, useState } from "react";
import { numericTaskInputs, type NumericTaskInput, type ResolvedTaskDefinition, type TaskDefinitionContract } from "../candidates";
import { LiveDataQualityPage, type QualityFilters } from "../quality";
import { workbenchApi, type ApiModelPackage, type ApiProject, type ApiQuality } from "../../shared/api/workbench-api";
import { ProfileWorkbenchPanel } from "./ProfileWorkbenchPanel";

function allowedRange(input: NumericTaskInput) {
  if (!input.allowed_range) throw new Error(`数値fieldにallowed_rangeがありません: ${input.path}`);
  return input.allowed_range;
}

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function rangeNumber(value: number) {
  return value.toLocaleString("ja-JP", { maximumFractionDigits: 4 });
}

export function DeveloperAdminPage({
  project,
  taskDefinition,
  resolvedTaskDefinition,
  initialSection,
  onSectionChange,
  qualityFilters,
  onQualityFiltersChange,
  onOpenLineage,
  onOpenDataLibrary,
  onProjectChanged,
}: {
  project: ApiProject | undefined;
  taskDefinition: TaskDefinitionContract | null;
  resolvedTaskDefinition: ResolvedTaskDefinition | null;
  initialSection?: "quality" | "ranges" | "display" | "task" | "model" | "profile";
  onSectionChange: (section: "quality" | "ranges" | "display" | "task" | "model" | "profile") => void;
  qualityFilters: QualityFilters;
  onQualityFiltersChange: (filters: QualityFilters) => void;
  onOpenLineage: (issue: ApiQuality["detected_issues"][number], filters: QualityFilters) => void;
  onOpenDataLibrary: () => void;
  onProjectChanged: (project: ApiProject) => void;
}) {
  type AdminSection = "quality" | "ranges" | "display" | "task" | "model" | "profile";
  const [section, setSection] = useState<AdminSection>(initialSection ?? "quality");
  const [modelPackage, setModelPackage] = useState<ApiModelPackage | null>(null);
  const [modelError, setModelError] = useState("");
  useEffect(() => {
    if (!project?.id) return;
    const controller = new AbortController();
    setModelPackage(null);
    setModelError("");
    workbenchApi.modelPackage(project.id)
      .then((item) => { if (!controller.signal.aborted) setModelPackage(item); })
      .catch((cause) => { if (!controller.signal.aborted) setModelError(cause instanceof Error ? cause.message : "モデル情報を取得できませんでした。"); });
    return () => controller.abort();
  }, [project?.id]);
  useEffect(() => setSection(initialSection ?? "quality"), [initialSection]);
  const qualityAvailable = resolvedTaskDefinition?.data_explorer?.quality === true;
  const allSections: Array<{ id: AdminSection; label: string }> = [
    { id: "quality", label: "データ品質集計" },
    { id: "ranges", label: "入力範囲" },
    { id: "display", label: "表示桁数" },
    { id: "task", label: "予測タスク定義" },
    { id: "model", label: "モデルと実行環境" },
    { id: "profile", label: "Profile Workbench" },
  ];
  const sections = allSections.filter((item) => item.id !== "quality" || qualityAvailable);
  const visibleSection: AdminSection = section === "quality" && resolvedTaskDefinition && !qualityAvailable ? "task" : section;
  return <div className="admin-workspace">
    <aside className="admin-navigation">
      <span className="overline">開発・管理</span><h2>検証と構成</h2><p>通常の候補検討では使わない管理情報です。</p>
      <nav aria-label="開発・管理メニュー">{sections.map((item) => <button key={item.id} className={visibleSection === item.id ? "active" : ""} onClick={() => { setSection(item.id); onSectionChange(item.id); }}>{item.label}</button>)}</nav>
    </aside>
    <div className="admin-content">
      {visibleSection === "quality" && (project?.id
        ? <LiveDataQualityPage projectId={project.id} filters={qualityFilters} onFiltersChange={onQualityFiltersChange} onOpenLineage={onOpenLineage} showReferenceScenarios mode="summary" />
        : <p className="empty-evidence">プロジェクトを読み込んでいます。</p>)}
      {visibleSection === "ranges" && <InputRangeSettingsPage project={project} taskDefinition={taskDefinition} onProjectChanged={onProjectChanged} />}
      {visibleSection === "display" && <DisplayDecimalSettingsPage project={project} taskDefinition={taskDefinition} onProjectChanged={onProjectChanged} />}
      {visibleSection === "profile" && <ProfileWorkbenchPanel onOpenDataLibrary={onOpenDataLibrary} />}
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
          <table className="quality-table"><thead><tr><th>特性</th><th>Runtime</th><th>RMSE</th><th>90%区間coverage</th></tr></thead><tbody>{modelPackage.predictors.map((predictor) => { const quality = modelPackage.quality_report.targets.find((item) => item.target === predictor.target); return <tr key={predictor.target}><th>{taskDefinition?.outputs.find((item) => item.key === predictor.target)?.label ?? predictor.target}</th><td>{predictor.runtime_type}</td><td>{quality ? number(quality.rmse, 2) : "—"}</td><td>{quality ? `${number(quality.interval_coverage_90 * 100, 0)}%` : "—"}</td></tr>; })}</tbody></table>
          <div className="runtime-list">{modelPackage.supported_runtimes.map((item) => <span className={item.available ? "available" : "optional"} key={item.runtime_type}>{item.runtime_type}{item.available ? " ✓" : " (追加導入)"}</span>)}</div>
          <details className="technical-contract"><summary>識別情報を表示</summary><code>{modelPackage.manifest_sha256}</code></details>
        </> : !modelError && <p className="empty-evidence">モデル情報を読み込んでいます。</p>}
      </div>}
    </div>
  </div>;
}

export function DisplayDecimalSettingsPage({ project, taskDefinition, onProjectChanged }: {
  project: ApiProject | undefined;
  taskDefinition: TaskDefinitionContract | null;
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
  const resetDefaults = () => setDraft(Object.fromEntries(items.map((item) => [item.key, String(taskDefinition.display_decimals[item.key])])));
  const save = async () => {
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
    <div className="page-intro"><div><h2>表示桁数</h2><p>表で表示する小数点以下の桁数です。保存値と計算精度は変わりません。</p></div><div className="project-actions"><button className="outline-button" onClick={resetDefaults}>デフォルトに戻す</button><button className="primary-button" disabled={saving} onClick={() => void save()}>{saving ? "保存中…" : "保存"}</button></div></div>
    {error && <p className="empty-evidence">{error}</p>}
    <table className="display-decimal-table"><thead><tr><th>区分</th><th>変数</th><th>単位</th><th>既定</th><th>このプロジェクト</th><th>表示例</th></tr></thead><tbody>{items.map((item) => {
      const value = Number(draft[item.key]);
      return <tr key={item.key}><td>{item.group}</td><th>{item.label}</th><td>{item.unit}</td><td className="numeric-cell">{taskDefinition.display_decimals[item.key]}</td><td className="numeric-cell"><input type="number" min="0" max="8" step="1" aria-label={`${item.label}の表示桁数`} value={draft[item.key] ?? ""} onChange={(event) => setDraft((current) => ({ ...current, [item.key]: event.target.value }))} /></td><td className="numeric-cell">{Number.isInteger(value) && value >= 0 && value <= 8 ? (0.001245).toFixed(value) : "—"}</td></tr>;
    })}</tbody></table>
  </div>;
}

export function InputRangeSettingsPage({ project, taskDefinition, onProjectChanged }: {
  project: ApiProject | undefined;
  taskDefinition: TaskDefinitionContract | null;
  onProjectChanged: (project: ApiProject) => void;
}) {
  const [draft, setDraft] = useState<Record<string, { min: string; max: string }>>({});
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!project || !taskDefinition) return;
    setDraft(Object.fromEntries(numericTaskInputs(taskDefinition).filter((input) => input.editable).map((input) => {
      const configured = project.input_ranges?.[input.id] ?? allowedRange(input);
      return [input.id, { min: String(configured.min), max: String(configured.max) }];
    })));
    setError("");
  }, [project, taskDefinition]);
  if (!project || !taskDefinition) return <div className="page-panel"><p className="empty-evidence">設定を読み込んでいます。</p></div>;
  const inputs = numericTaskInputs(taskDefinition).filter((input) => input.editable);
  const update = (id: string, side: "min" | "max", value: string) => setDraft((current) => ({ ...current, [id]: { ...current[id], [side]: value } }));
  const resetDefaults = () => setDraft(Object.fromEntries(inputs.map((input) => {
    const range = input.default_range ?? allowedRange(input);
    return [input.id, { min: String(range.min), max: String(range.max) }];
  })));
  const save = async () => {
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
    <div className="page-intro"><div><h2>入力範囲設定</h2><p>スライダーと数値入力で使う許容範囲を、プロジェクトごとに設定します。</p></div><div className="project-actions"><button className="outline-button" onClick={resetDefaults}>デフォルトに戻す</button><button className="primary-button" disabled={saving} onClick={() => void save()}>{saving ? "保存中…" : "保存"}</button></div></div>
    <p className="settings-explanation"><b>スライダー全体</b>が許容範囲、<b>色付き帯</b>が学習データ範囲です。許容範囲を広げても、学習範囲外は外挿として表示されます。</p>
    {error && <p className="empty-evidence">{error}</p>}
    <table className="input-range-table"><thead><tr><th>入力項目</th><th>許容最小</th><th>許容最大</th><th>デフォルト</th><th>学習範囲</th></tr></thead><tbody>{inputs.map((input) => {
      const range = input.default_range ?? allowedRange(input);
      const training = input.training_range;
      return <tr key={input.id}><th>{input.label}<small>{input.unit}</small></th><td><input type="number" step="any" value={draft[input.id]?.min ?? ""} onChange={(event) => update(input.id, "min", event.target.value)} /></td><td><input type="number" step="any" value={draft[input.id]?.max ?? ""} onChange={(event) => update(input.id, "max", event.target.value)} /></td><td>{rangeNumber(range.min)}–{rangeNumber(range.max)}</td><td>{training ? `${rangeNumber(training.min)}–${rangeNumber(training.max)}` : "—"}</td></tr>;
    })}</tbody></table>
  </div>;
}
