import { useEffect, useState } from "react";
import type { ResolvedTaskDefinition, TaskDefinitionContract } from "./taskDefinition";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8765";

type HotCandidate = {
  raw: ApiCandidate;
  id: string;
  name: string;
  composition: Record<string, number>;
  reheat_temperature_c: number;
  hold_time_min: number;
  finish_temperature_c: number;
  coiling_temperature_c: number;
  cooling_rate_c_s: number;
  entry_thickness_mm: number;
  exit_thickness_mm: number;
  route: "A" | "B" | "C";
};

type ApiCandidate = {
  id: string;
  project_id: string;
  name: string;
  inputs: {
    composition: Record<string, number>;
    process: Record<string, number>;
    categorical: Record<string, string>;
    heat_pattern?: Array<{ time_s: number; temperature_c: number }>;
  };
  provenance: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type ApiCandidateInput = Pick<ApiCandidate, "name" | "inputs" | "provenance">;

function fromApiCandidate(candidate: ApiCandidate): HotCandidate {
  const process = candidate.inputs.process;
  return {
    raw: candidate,
    id: candidate.id,
    name: candidate.name,
    composition: candidate.inputs.composition,
    reheat_temperature_c: process.reheat_temperature_c,
    hold_time_min: process.hold_time_min,
    finish_temperature_c: process.finish_temperature_c,
    coiling_temperature_c: process.coiling_temperature_c,
    cooling_rate_c_s: process.cooling_rate_c_s,
    entry_thickness_mm: process.entry_thickness_mm,
    exit_thickness_mm: process.exit_thickness_mm,
    route: candidate.inputs.categorical.route as HotCandidate["route"],
  };
}

function toApiCandidate(candidate: HotCandidate): ApiCandidateInput {
  return {
    name: candidate.name,
    inputs: {
      composition: candidate.composition,
      process: {
        ...candidate.raw.inputs.process,
        reheat_temperature_c: candidate.reheat_temperature_c,
        hold_time_min: candidate.hold_time_min,
        finish_temperature_c: candidate.finish_temperature_c,
        coiling_temperature_c: candidate.coiling_temperature_c,
        cooling_rate_c_s: candidate.cooling_rate_c_s,
        entry_thickness_mm: candidate.entry_thickness_mm,
        exit_thickness_mm: candidate.exit_thickness_mm,
      },
      categorical: { ...candidate.raw.inputs.categorical, route: candidate.route },
    },
    provenance: candidate.raw.provenance,
  };
}

type HotPreview = {
  predictions: Record<string, { value: number; lower: number; upper: number; unit: string; uncertainty_components?: Record<string, number> }>;
  support: { status: "supported" | "caution" | "extrapolated"; message: string; percentile: number; components: Record<string, number> };
  similar: Array<{ parent_key: string; distance: number; repeat_summary: Record<string, { mean: number; std: number; n: number }> }>;
  model_meta: { model: { id: string; version: string; method: string } };
};

const n = (value: number, digits = 1) => value.toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function HotRollingWorkbench({ projectId }: { projectId: string }) {
  const [candidates, setCandidates] = useState<HotCandidate[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [previews, setPreviews] = useState<Record<string, HotPreview>>({});
  const [task, setTask] = useState<TaskDefinitionContract | null>(null);
  const [notice, setNotice] = useState("熱延タスクを読み込んでいます");
  const selected = candidates.find((item) => item.id === selectedId) ?? candidates[0];
  const preview = selected ? previews[selected.id] : undefined;

  async function loadPreview(candidateId: string) {
    const response = await fetch(`${API_URL}/api/projects/${encodeURIComponent(projectId)}/candidates/${candidateId}/preview`, { method: "POST" });
    if (!response.ok) throw new Error("preview unavailable");
    const result = (await response.json()) as HotPreview;
    setPreviews((items) => ({ ...items, [candidateId]: result }));
  }

  useEffect(() => {
    const load = async () => {
      try {
        setTask(null);
        setCandidates([]);
        const taskResponse = await fetch(`${API_URL}/api/projects/${encodeURIComponent(projectId)}/task-definition`);
        if (!taskResponse.ok) throw new Error();
        const resolved = (await taskResponse.json()) as ResolvedTaskDefinition;
        if (resolved.task_definition.id !== "hot-rolled-properties-v1") {
          setNotice("熱延後特性タスクのプロジェクトを選択してください");
          return;
        }
        const candidateResponse = await fetch(`${API_URL}/api/projects/${encodeURIComponent(projectId)}/candidates`);
        if (!candidateResponse.ok) throw new Error();
        const loadedCandidates = ((await candidateResponse.json()) as ApiCandidate[]).map(fromApiCandidate);
        setCandidates(loadedCandidates);
        setSelectedId(loadedCandidates[0]?.id ?? "");
        setTask(resolved.task_definition);
        await Promise.all(loadedCandidates.map((item) => loadPreview(item.id)));
        setNotice("GPR予測と熱延実績を同期しました");
      } catch {
        setNotice("熱延タスクを読み込めません。API接続を確認してください");
      }
    };
    void load();
  }, [projectId]);

  const reduction = selected ? (1 - selected.exit_thickness_mm / selected.entry_thickness_mm) * 100 : 0;

  function localUpdate(field: keyof HotCandidate, value: number | string) {
    if (!selected) return;
    setCandidates((items) => items.map((item) => item.id === selected.id ? { ...item, [field]: value } : item));
  }

  function compositionUpdate(name: string, value: number) {
    if (!selected) return;
    setCandidates((items) => items.map((item) => item.id === selected.id ? { ...item, composition: { ...item.composition, [name]: value } } : item));
  }

  async function persist(candidate: HotCandidate) {
    try {
      const response = await fetch(`${API_URL}/api/projects/${encodeURIComponent(projectId)}/candidates/${candidate.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(toApiCandidate(candidate)) });
      if (!response.ok) throw new Error();
      const saved = fromApiCandidate((await response.json()) as ApiCandidate);
      setCandidates((items) => items.map((item) => item.id === saved.id ? saved : item));
      await loadPreview(saved.id);
      setNotice(`${saved.name}を保存し、GPR予測を更新しました`);
    } catch {
      setNotice("入力を保存できません。温度・板厚の関係を確認してください");
    }
  }

  async function addCandidate() {
    if (!selected || candidates.length >= 10) return;
    const payload = toApiCandidate({ ...selected, name: `${selected.name} コピー` });
    const response = await fetch(`${API_URL}/api/projects/${encodeURIComponent(projectId)}/candidates`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) return;
    const created = fromApiCandidate((await response.json()) as ApiCandidate);
    setCandidates((items) => [...items, created]);
    setSelectedId(created.id);
    await loadPreview(created.id);
  }

  async function deleteCandidate() {
    if (!selected || candidates.length <= 1) return;
    const response = await fetch(`${API_URL}/api/projects/${encodeURIComponent(projectId)}/candidates/${selected.id}`, { method: "DELETE" });
    if (!response.ok) return;
    const remaining = candidates.filter((item) => item.id !== selected.id);
    setCandidates(remaining);
    setSelectedId(remaining[0].id);
  }

  if (!selected || !task) return <section className="hot-loading"><h2>熱延条件の候補検討</h2><p>{notice}</p></section>;

  const compositionNames = task.input_groups.find((group) => group.key === "composition")?.fields.map((field) => field.path.split(".", 2)[1]) ?? [];
  const processInputs = task.input_groups.find((group) => group.key === "process")?.fields.filter((field) => field.kind === "number") ?? [];
  const routeInput = task.input_groups.find((group) => group.key === "categorical")?.fields.find((field) => field.path === "categorical.route");
  const context = Object.fromEntries(task.fixed_context.map((item) => [item.path, item.value]));
  const outputs = task.outputs;

  return (
    <div className="hot-workbench">
      <aside className="hot-inspector">
        <div className="hot-section-heading"><div><span className="overline">HOT ROLLING</span><h2>{selected.name}</h2></div><span className="route-badge">Route {selected.route}</span></div>
        <label className="hot-name">候補名<input value={selected.name} onChange={(event) => localUpdate("name", event.target.value)} onBlur={() => void persist(selected)} /></label>
        <section className="hot-composition">
          <header><h3>組成</h3><span>mass%</span></header>
          <div>{compositionNames.map((name) => <label key={name}>{name}<input type="number" step="any" value={selected.composition[name] ?? 0} onChange={(event) => compositionUpdate(name, Number(event.target.value))} onBlur={() => void persist(selected)} /></label>)}</div>
        </section>
        <h3 className="hot-subheading">熱延条件</h3>
        <div className="hot-field-grid">
          {processInputs.map((input) => {
            const field = input.path.split(".", 2)[1] as keyof HotCandidate;
            return (
            <label key={input.path}>{input.label}<span><input type="number" step="any" value={Number(selected[field])} onChange={(event) => localUpdate(field, Number(event.target.value))} onBlur={() => void persist(selected)} /><em>{input.unit}</em></span><small>学習 {input.training_range ? `${n(input.training_range.min)}–${n(input.training_range.max)}` : "—"}</small></label>
            );
          })}
        </div>
        <div className="hot-derived"><span>圧下率</span><b>{n(reduction)}%</b><small>入出側板厚から算出</small></div>
        {routeInput && <label className="hot-route">ルート<select value={selected.route} onChange={(event) => { const next = { ...selected, route: event.target.value as HotCandidate["route"] }; setCandidates((items) => items.map((item) => item.id === next.id ? next : item)); void persist(next); }}>{routeInput.choices.map((route) => <option key={route}>{route}</option>)}</select></label>}
        <p className="hot-context">設備 {String(context["context.equipment"] ?? "—")} · 引張方向 {String(context["context.test_direction"] ?? "—")}</p>
      </aside>

      <section className="hot-main">
        <header className="hot-main-header"><div><span className="overline">CANDIDATE COMPARISON</span><h2>熱延条件と予測特性</h2></div><div><button onClick={() => void addCandidate()} disabled={candidates.length >= 10}>複製</button><button className="danger-quiet" onClick={() => void deleteCandidate()} disabled={candidates.length <= 1}>削除</button></div></header>
        <div className="hot-table-wrap"><table className="hot-table"><thead><tr><th>候補</th><th>仕上</th><th>巻取</th><th>冷却</th><th>圧下</th>{outputs.map((output) => <th key={output.key}>{output.label}</th>)}<th>支持度</th></tr></thead><tbody>{candidates.map((item) => { const itemPreview = previews[item.id]; return <tr key={item.id} className={item.id === selected.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><th>{item.name}<small>Route {item.route}</small></th><td>{n(item.finish_temperature_c, 0)}℃</td><td>{n(item.coiling_temperature_c, 0)}℃</td><td>{n(item.cooling_rate_c_s)}℃/s</td><td>{n((1 - item.exit_thickness_mm / item.entry_thickness_mm) * 100)}%</td>{outputs.map((output) => { const value = itemPreview?.predictions[output.key]; return <td key={output.key}>{value ? n(value.value, output.unit === "%" ? 1 : 0) : "—"}</td>; })}<td><span className={`hot-support-dot ${itemPreview?.support.status ?? ""}`} />{itemPreview?.support.status === "supported" ? "範囲内" : itemPreview?.support.status === "extrapolated" ? "外挿" : "要確認"}</td></tr>; })}</tbody></table></div>
        <section className="hot-composition-comparison">
          <header><span className="overline">COMPOSITION</span><h3>組成の候補比較</h3></header>
          <div className="hot-table-wrap"><table className="hot-table hot-composition-table"><thead><tr><th>候補</th>{compositionNames.map((name) => <th key={name}>{name}</th>)}</tr></thead><tbody>{candidates.map((item) => <tr key={item.id} className={item.id === selected.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><th>{item.name}</th>{compositionNames.map((name) => <td key={name}>{n(item.composition[name] ?? 0, name === "C" || name === "Mn" || name === "Si" ? 3 : 4)}</td>)}</tr>)}</tbody></table></div>
        </section>
        <p className="hot-notice">{notice}</p>
      </section>

      <aside className="hot-evidence">
        <div><span className="overline">PREDICTION EVIDENCE</span><h2>予測と不確かさ</h2><small>{preview?.model_meta.model.id} · {preview?.model_meta.model.version}</small></div>
        {preview && outputs.map((output) => { const prediction = preview.predictions[output.key]; if (!prediction) return null; const parts = prediction.uncertainty_components ?? {}; return <section className="hot-metric" key={output.key}><header><b>{output.label}</b><strong>{n(prediction.value, output.unit === "%" ? 1 : 0)} <small>{prediction.unit}</small></strong></header><div className="hot-interval"><span style={{ left: `${Math.max(0, Math.min(100, ((prediction.value - prediction.lower) / Math.max(prediction.upper - prediction.lower, 1e-6)) * 100))}%` }} /></div><p>{n(prediction.lower)}–{n(prediction.upper)} {prediction.unit}</p><dl><div><dt>モデル</dt><dd>±{n(parts.latent_model_std ?? Math.sqrt(parts.latent_model_variance ?? 0))}</dd></div><div><dt>測定ばらつき</dt><dd>±{n(parts.observation_noise_std ?? Math.sqrt(parts.observation_noise_variance ?? 0))}</dd></div></dl></section>; })}
        {preview && <section className={`hot-support ${preview.support.status}`}><b>{preview.support.status === "supported" ? "学習範囲内" : preview.support.status === "extrapolated" ? "外挿" : "要確認"}</b><p>{preview.support.message}</p><small>距離百分位 {n(preview.support.percentile, 0)}%</small></section>}
        <section className="hot-neighbors"><h3>近い熱延実績</h3>{preview?.similar.map((item) => <article key={item.parent_key}><b>{item.parent_key}</b><span>距離 {n(item.distance, 2)}</span><p>{Object.entries(item.repeat_summary).map(([key, value]) => `${key} ${n(value.mean)} ± ${n(value.std)} (n=${value.n})`).join(" / ")}</p></article>)}</section>
      </aside>
    </div>
  );
}
