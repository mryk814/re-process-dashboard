import { useEffect, useMemo, useState } from "react";
import { LAB_FIXTURES, calculateLabCurves, calculateLabPreview } from "./fixtures";
import type {
  LabCandidate,
  LabCurveSeries,
  LabField,
  LabNumericField,
  LabOutputDefinition,
  LabPreview,
  LabRuntimeCapability,
  LabSurfaceState,
  LabTaskDefinition,
} from "./types";

const CANDIDATE_COLORS = ["#2563eb", "#ea580c", "#7c3aed", "#059669", "#be123c"];

function cloneCandidates(candidates: LabCandidate[]): LabCandidate[] {
  return candidates.map((candidate) => ({ ...candidate, values: { ...candidate.values } }));
}

function number(value: number, digits = 1): string {
  return value.toLocaleString("ja-JP", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function candidateColor(index: number): string {
  return CANDIDATE_COLORS[index % CANDIDATE_COLORS.length];
}

function stateLabel(state: LabSurfaceState): string {
  if (state === "loading") return "更新中";
  if (state === "stale") return "旧結果を表示中";
  if (state === "error") return "取得失敗";
  return "最新";
}

function supportLabel(status: LabPreview["supportStatus"]): string {
  if (status === "caution") return "注意";
  if (status === "extrapolated") return "外挿";
  return "近傍あり";
}

function outputValue(preview: LabPreview, outputKey: string) {
  return preview.predictions.find((item) => item.outputKey === outputKey);
}

function LabHeader({
  taskKey,
  onTaskKey,
  state,
  onState,
  runtimeMode,
  onRuntimeMode,
}: {
  taskKey: "annealed" | "hotRolling";
  onTaskKey: (key: "annealed" | "hotRolling") => void;
  state: LabSurfaceState;
  onState: (state: LabSurfaceState) => void;
  runtimeMode: "full" | "pointOnly";
  onRuntimeMode: (mode: "full" | "pointOnly") => void;
}) {
  return (
    <header className="lab-header">
      <div>
        <span className="lab-kicker">Material Decision Workbench</span>
        <h1>UI Lab</h1>
        <p>本番契約に接続せず、共通Workbenchの操作と情報密度を試す実験環境</p>
      </div>
      <div className="lab-header-controls">
        <label>
          <span>Fixture</span>
          <select value={taskKey} onChange={(event) => onTaskKey(event.target.value as "annealed" | "hotRolling")}>
            <option value="annealed">焼鈍後特性</option>
            <option value="hotRolling">熱延後特性</option>
          </select>
        </label>
        <label>
          <span>Runtime</span>
          <select value={runtimeMode} onChange={(event) => onRuntimeMode(event.target.value as "full" | "pointOnly")}>
            <option value="full">不確実性・根拠あり</option>
            <option value="pointOnly">点予測のみ</option>
          </select>
        </label>
        <label>
          <span>Surface state</span>
          <select value={state} onChange={(event) => onState(event.target.value as LabSurfaceState)}>
            <option value="ready">Ready</option>
            <option value="loading">Loading</option>
            <option value="stale">Stale</option>
            <option value="error">Error</option>
          </select>
        </label>
      </div>
    </header>
  );
}

function CandidateRail({
  candidates,
  selectedId,
  onSelect,
  onDuplicate,
  onRemove,
}: {
  candidates: LabCandidate[];
  selectedId: string;
  onSelect: (id: string) => void;
  onDuplicate: () => void;
  onRemove: () => void;
}) {
  return (
    <aside className="candidate-rail lab-panel">
      <div className="panel-heading compact">
        <div>
          <span className="eyebrow">Candidates</span>
          <h2>候補</h2>
        </div>
        <span className="count-badge">{candidates.length}</span>
      </div>
      <div className="candidate-list">
        {candidates.map((candidate, index) => (
          <button
            className={candidate.id === selectedId ? "candidate-card selected" : "candidate-card"}
            key={candidate.id}
            onClick={() => onSelect(candidate.id)}
          >
            <i style={{ background: candidateColor(index) }} />
            <span>
              <b>{candidate.name}</b>
              <small>{candidate.id}</small>
            </span>
          </button>
        ))}
      </div>
      <div className="rail-actions">
        <button onClick={onDuplicate}>選択候補を複製</button>
        <button className="danger-text" disabled={candidates.length <= 1} onClick={onRemove}>削除</button>
      </div>
      <div className="lab-note">
        <b>Lab principle</b>
        <p>候補操作はfixture内だけ。保存・revision・APIはここでは決めません。</p>
      </div>
    </aside>
  );
}

function FieldEditor({
  field,
  value,
  onChange,
}: {
  field: LabField;
  value: number | string | undefined;
  onChange: (value: number | string) => void;
}) {
  if (field.group === "categorical") {
    return (
      <label className="field-card">
        <span><b>{field.label}</b><small>categorical</small></span>
        <select value={String(value ?? field.choices[0])} onChange={(event) => onChange(event.target.value)}>
          {field.choices.map((choice) => <option key={choice}>{choice}</option>)}
        </select>
      </label>
    );
  }
  return (
    <label className="field-card">
      <span><b>{field.label}</b><small>{field.unit}</small></span>
      <div className="field-input-pair">
        <input
          type="range"
          min={field.min}
          max={field.max}
          step={field.step}
          value={Number(value ?? field.min)}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        <input
          type="number"
          min={field.min}
          max={field.max}
          step={field.step}
          value={Number(value ?? field.min)}
          onChange={(event) => onChange(Number(event.target.value))}
        />
      </div>
    </label>
  );
}

function CandidateEditor({
  task,
  candidate,
  onValue,
  onName,
}: {
  task: LabTaskDefinition;
  candidate: LabCandidate;
  onValue: (path: string, value: number | string) => void;
  onName: (name: string) => void;
}) {
  const groups = [
    ["composition", "組成"],
    ["process", "工程条件"],
    ["categorical", "区分"],
  ] as const;
  return (
    <section className="candidate-editor lab-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Input</span>
          <h2>条件を編集</h2>
        </div>
        <input className="candidate-name-input" value={candidate.name} onChange={(event) => onName(event.target.value)} />
      </div>
      <p className="panel-description">{task.description}</p>
      {groups.map(([group, label]) => {
        const fields = task.fields.filter((field) => field.group === group);
        if (!fields.length) return null;
        return (
          <div className="field-group" key={group}>
            <div className="field-group-heading"><h3>{label}</h3><span>{fields.length} fields</span></div>
            <div className="field-grid">
              {fields.map((field) => (
                <FieldEditor
                  field={field}
                  key={field.path}
                  value={candidate.values[field.path]}
                  onChange={(value) => onValue(field.path, value)}
                />
              ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function PredictionCard({
  output,
  preview,
  capability,
}: {
  output: LabOutputDefinition;
  preview: LabPreview;
  capability: LabRuntimeCapability;
}) {
  const prediction = outputValue(preview, output.key);
  if (!prediction) return null;
  return (
    <article className="prediction-card">
      <div className="prediction-card-heading">
        <span>{output.label}</span>
        <small>{output.unit}</small>
      </div>
      <strong>{number(prediction.value, output.unit === "%" ? 1 : 0)}</strong>
      {capability.uncertainty && prediction.lower !== undefined && prediction.upper !== undefined ? (
        <div className="interval-row">
          <span>{number(prediction.lower, 0)}</span>
          <div><i style={{ left: "50%" }} /></div>
          <span>{number(prediction.upper, 0)}</span>
        </div>
      ) : (
        <p className="unavailable-inline">予測区間は利用できません</p>
      )}
      <div className="goal-row">
        <span>目標 {prediction.goalValue}{output.unit}</span>
        <b>{prediction.goalProbability === undefined ? "—" : `${Math.round(prediction.goalProbability * 100)}%`}</b>
      </div>
    </article>
  );
}

function EvidencePanel({
  task,
  candidate,
  preview,
  capability,
  surfaceState,
}: {
  task: LabTaskDefinition;
  candidate: LabCandidate;
  preview: LabPreview;
  capability: LabRuntimeCapability;
  surfaceState: LabSurfaceState;
}) {
  return (
    <aside className={`evidence-panel lab-panel state-${surfaceState}`}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Prediction & evidence</span>
          <h2>{candidate.name}</h2>
        </div>
        <span className={`surface-state ${surfaceState}`}>{stateLabel(surfaceState)}</span>
      </div>
      {surfaceState === "error" ? (
        <div className="surface-error">
          <b>予測を取得できませんでした</b>
          <p>旧結果を保持するか、空にするかをここで検証できます。</p>
          <button>再試行</button>
        </div>
      ) : (
        <>
          {surfaceState === "loading" && <div className="loading-strip"><i /></div>}
          {surfaceState === "stale" && <p className="stale-message">入力を保存中です。現在は直前の有効な予測を表示しています。</p>}
          <div className="prediction-grid">
            {task.outputs.map((output) => (
              <PredictionCard capability={capability} key={output.key} output={output} preview={preview} />
            ))}
          </div>
          {capability.support ? (
            <section className={`support-card ${preview.supportStatus}`}>
              <div>
                <span className="support-dot" />
                <b>{supportLabel(preview.supportStatus)}</b>
                <small>{preview.nearestLabel}</small>
              </div>
              <p>{preview.supportMessage}</p>
              {preview.warning && <em>{preview.warning}</em>}
            </section>
          ) : (
            <section className="unavailable-card">
              <b>データ支持度は利用できません</b>
              <p>runtime capabilityにない根拠を擬似生成しない状態を確認します。</p>
            </section>
          )}
          <section className="evidence-actions">
            <button disabled={!capability.similarity}>類似実験を見る</button>
            <button className="primary" disabled={!capability.detailedPrediction}>詳細予測を固定保存</button>
          </section>
        </>
      )}
    </aside>
  );
}

function ComparisonTable({
  task,
  candidates,
  previews,
  selectedId,
  onSelect,
}: {
  task: LabTaskDefinition;
  candidates: LabCandidate[];
  previews: Record<string, LabPreview>;
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const summaryFields = task.fields.filter((field) => field.group !== "categorical").slice(0, 4) as LabNumericField[];
  return (
    <section className="comparison-panel lab-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Compare</span>
          <h2>候補比較</h2>
        </div>
        <p>表の密度・固定列・予測の置き場所を試す領域</p>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>候補</th>
              {summaryFields.map((field) => <th key={field.path}>{field.label}<small>{field.unit}</small></th>)}
              {task.outputs.map((output) => <th key={output.key}>{output.label}<small>{output.unit}</small></th>)}
              <th>支持度</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((candidate, index) => {
              const preview = previews[candidate.id];
              return (
                <tr className={candidate.id === selectedId ? "selected" : ""} key={candidate.id} onClick={() => onSelect(candidate.id)}>
                  <th><i style={{ background: candidateColor(index) }} />{candidate.name}</th>
                  {summaryFields.map((field) => <td key={field.path}>{number(Number(candidate.values[field.path] ?? 0), 2)}</td>)}
                  {task.outputs.map((output) => <td className="prediction-cell" key={output.key}>{number(outputValue(preview, output.key)?.value ?? 0, output.unit === "%" ? 1 : 0)}</td>)}
                  <td><span className={`support-pill ${preview.supportStatus}`}>{supportLabel(preview.supportStatus)}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ResponseCurveChart({
  task,
  candidates,
  series,
  variable,
  output,
}: {
  task: LabTaskDefinition;
  candidates: LabCandidate[];
  series: LabCurveSeries[];
  variable: LabNumericField;
  output: LabOutputDefinition;
}) {
  const selectedSeries = series.filter((item) => item.outputKey === output.key);
  const values = selectedSeries.flatMap((item) => item.points.map((point) => point.value));
  const yMin = Math.min(...values);
  const yMax = Math.max(...values);
  const width = 620;
  const height = 220;
  const pad = { left: 48, right: 18, top: 18, bottom: 38 };
  const xScale = (value: number) => pad.left + ((value - variable.min) / Math.max(variable.max - variable.min, Number.EPSILON)) * (width - pad.left - pad.right);
  const yScale = (value: number) => height - pad.bottom - ((value - yMin) / Math.max(yMax - yMin, Number.EPSILON)) * (height - pad.top - pad.bottom);
  return (
    <article className="curve-chart-card">
      <div className="curve-chart-heading">
        <div><b>{output.label}</b><small>{output.unit}</small></div>
        <span>{selectedSeries.length}候補</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${variable.label}に対する${output.label}の応答曲線`}>
        <line className="axis" x1={pad.left} x2={pad.left} y1={pad.top} y2={height - pad.bottom} />
        <line className="axis" x1={pad.left} x2={width - pad.right} y1={height - pad.bottom} y2={height - pad.bottom} />
        {[0, 0.5, 1].map((ratio) => {
          const value = yMin + (yMax - yMin) * ratio;
          const y = yScale(value);
          return <g key={ratio}><line className="grid" x1={pad.left} x2={width - pad.right} y1={y} y2={y} /><text x={4} y={y + 4}>{number(value, 0)}</text></g>;
        })}
        {selectedSeries.map((item) => {
          const candidateIndex = candidates.findIndex((candidate) => candidate.id === item.candidateId);
          const path = item.points.map((point, index) => `${index ? "L" : "M"}${xScale(point.x)},${yScale(point.value)}`).join(" ");
          return <path className="curve-line" d={path} key={item.candidateId} style={{ stroke: candidateColor(candidateIndex) }} />;
        })}
        <text className="axis-label" x={(pad.left + width - pad.right) / 2} y={height - 7} textAnchor="middle">{variable.label} ({variable.unit})</text>
      </svg>
    </article>
  );
}

function ResponseCurves({
  task,
  candidates,
  selectedId,
}: {
  task: LabTaskDefinition;
  candidates: LabCandidate[];
  selectedId: string;
}) {
  const numericFields = task.fields.filter((field): field is LabNumericField => field.group !== "categorical");
  const [open, setOpen] = useState(true);
  const [variablePath, setVariablePath] = useState(numericFields[0]?.path ?? "");
  const [comparedIds, setComparedIds] = useState<string[]>([]);
  useEffect(() => {
    if (!numericFields.some((field) => field.path === variablePath)) setVariablePath(numericFields[0]?.path ?? "");
    setComparedIds([]);
  }, [task.id]);
  const visibleCandidates = candidates.filter((candidate) => candidate.id === selectedId || comparedIds.includes(candidate.id));
  const series = useMemo(() => calculateLabCurves(task, visibleCandidates, variablePath), [task, visibleCandidates, variablePath]);
  const variable = numericFields.find((field) => field.path === variablePath) ?? numericFields[0];
  return (
    <section className="curve-panel lab-panel">
      <div className="panel-heading curve-panel-heading">
        <div>
          <span className="eyebrow">Response curves</span>
          <h2>応答曲線</h2>
        </div>
        <div className="curve-controls">
          <label>変数<select value={variablePath} onChange={(event) => setVariablePath(event.target.value)}>{numericFields.map((field) => <option key={field.path} value={field.path}>{field.label}</option>)}</select></label>
          <button onClick={() => setOpen((value) => !value)}>{open ? "閉じる" : "開く"}</button>
        </div>
      </div>
      {!task.capabilities.responseCurves ? (
        <div className="unavailable-card"><b>応答曲線はこのruntimeでは利用できません</b></div>
      ) : open && variable ? (
        <>
          <div className="curve-candidate-picker">
            <b>比較候補</b>
            {candidates.filter((candidate) => candidate.id !== selectedId).map((candidate) => (
              <label key={candidate.id}>
                <input
                  type="checkbox"
                  checked={comparedIds.includes(candidate.id)}
                  onChange={(event) => setComparedIds((current) => event.target.checked ? [...current, candidate.id] : current.filter((id) => id !== candidate.id))}
                />
                {candidate.name}
              </label>
            ))}
            <small>選択候補だけを初期表示し、必要な比較候補を追加する案</small>
          </div>
          <div className="curve-grid">
            {task.outputs.map((output) => (
              <ResponseCurveChart candidates={candidates} key={output.key} output={output} series={series} task={task} variable={variable} />
            ))}
          </div>
        </>
      ) : (
        <p className="closed-panel-message">パネルが閉じている間は曲線計算を行わない想定です。</p>
      )}
    </section>
  );
}

export default function UiLabApp() {
  const [taskKey, setTaskKey] = useState<"annealed" | "hotRolling">("annealed");
  const fixture = LAB_FIXTURES[taskKey];
  const [candidates, setCandidates] = useState<LabCandidate[]>(() => cloneCandidates(fixture.candidates));
  const [selectedId, setSelectedId] = useState(fixture.candidates[0].id);
  const [surfaceState, setSurfaceState] = useState<LabSurfaceState>("ready");
  const [runtimeMode, setRuntimeMode] = useState<"full" | "pointOnly">("full");

  useEffect(() => {
    const next = LAB_FIXTURES[taskKey];
    setCandidates(cloneCandidates(next.candidates));
    setSelectedId(next.candidates[0].id);
    setSurfaceState("ready");
  }, [taskKey]);

  const selected = candidates.find((candidate) => candidate.id === selectedId) ?? candidates[0];
  const previews = useMemo(
    () => Object.fromEntries(candidates.map((candidate) => [candidate.id, calculateLabPreview(fixture.task, candidate)])),
    [candidates, fixture.task],
  );
  const capability: LabRuntimeCapability = runtimeMode === "full"
    ? fixture.task.capabilities
    : { ...fixture.task.capabilities, uncertainty: false, support: false, similarity: false };

  function updateSelected(path: string, value: number | string) {
    setCandidates((items) => items.map((candidate) => candidate.id === selected.id ? { ...candidate, values: { ...candidate.values, [path]: value } } : candidate));
    setSurfaceState("stale");
  }

  function updateName(name: string) {
    setCandidates((items) => items.map((candidate) => candidate.id === selected.id ? { ...candidate, name } : candidate));
  }

  function duplicateSelected() {
    const id = `${selected.id}-copy-${candidates.length + 1}`;
    const next = { ...selected, id, name: `${selected.name} コピー`, values: { ...selected.values } };
    setCandidates((items) => [...items, next]);
    setSelectedId(id);
  }

  function removeSelected() {
    if (candidates.length <= 1) return;
    const remaining = candidates.filter((candidate) => candidate.id !== selected.id);
    setCandidates(remaining);
    setSelectedId(remaining[0].id);
  }

  return (
    <div className="ui-lab-shell">
      <LabHeader
        onRuntimeMode={setRuntimeMode}
        onState={setSurfaceState}
        onTaskKey={setTaskKey}
        runtimeMode={runtimeMode}
        state={surfaceState}
        taskKey={taskKey}
      />
      <div className="lab-banner">
        <b>Prototype branch</b>
        <span>本番API未接続・直接merge禁止・採用componentだけ#7/#8/#15へ移植</span>
        <a href="/">production UIへ戻る</a>
      </div>
      <main className="lab-main">
        <section className="task-context">
          <div><span className="eyebrow">Current task</span><h2>{fixture.task.label}</h2><p>{fixture.task.description}</p></div>
          <div className="context-actions">
            <button onClick={() => setSurfaceState("loading")}>更新を開始</button>
            <button className="primary" onClick={() => setSurfaceState("ready")}>最新結果にする</button>
          </div>
        </section>
        <div className="workbench-layout">
          <CandidateRail candidates={candidates} onDuplicate={duplicateSelected} onRemove={removeSelected} onSelect={setSelectedId} selectedId={selected.id} />
          <div className="workbench-center">
            <CandidateEditor candidate={selected} onName={updateName} onValue={updateSelected} task={fixture.task} />
            <ComparisonTable candidates={candidates} onSelect={setSelectedId} previews={previews} selectedId={selected.id} task={fixture.task} />
          </div>
          <EvidencePanel capability={capability} candidate={selected} preview={previews[selected.id]} surfaceState={surfaceState} task={fixture.task} />
        </div>
        <ResponseCurves candidates={candidates} selectedId={selected.id} task={{ ...fixture.task, capabilities: capability }} />
      </main>
    </div>
  );
}
