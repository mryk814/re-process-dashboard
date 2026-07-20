import { useEffect, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8765";

type HotCandidate = {
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

type HotPreview = {
  predictions: Record<string, { value: number; lower: number; upper: number; unit: string; uncertainty_components?: Record<string, number> }>;
  support: { status: "supported" | "caution" | "extrapolated"; message: string; percentile: number; components: Record<string, number> };
  similar: Array<{ parent_key: string; distance: number; repeat_summary: Record<string, { mean: number; std: number; n: number }> }>;
  model_meta: { model: { id: string; version: string; method: string } };
};

type TaskDefinition = {
  inputs: Array<{ field: keyof HotCandidate; label: string; unit: string; min: number; max: number }>;
  categorical_inputs: { route: Array<"A" | "B" | "C"> };
  context: { equipment: string; test_direction: string };
  model: { id: string; version: string };
};

const compositionNames = ["C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Ti", "B", "N", "O", "Ca"];
const n = (value: number, digits = 1) => value.toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function HotRollingWorkbench() {
  const [candidates, setCandidates] = useState<HotCandidate[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [previews, setPreviews] = useState<Record<string, HotPreview>>({});
  const [task, setTask] = useState<TaskDefinition | null>(null);
  const [notice, setNotice] = useState("熱延タスクを読み込んでいます");
  const selected = candidates.find((item) => item.id === selectedId) ?? candidates[0];
  const preview = selected ? previews[selected.id] : undefined;

  async function loadPreview(candidateId: string) {
    const response = await fetch(`${API_URL}/api/hot-rolling/candidates/${candidateId}/preview`, { method: "POST" });
    if (!response.ok) throw new Error("preview unavailable");
    const result = (await response.json()) as HotPreview;
    setPreviews((items) => ({ ...items, [candidateId]: result }));
  }

  useEffect(() => {
    const load = async () => {
      try {
        const [candidateResponse, taskResponse] = await Promise.all([
          fetch(`${API_URL}/api/hot-rolling/candidates`),
          fetch(`${API_URL}/api/hot-rolling/task-definition`),
        ]);
        if (!candidateResponse.ok || !taskResponse.ok) throw new Error();
        const loadedCandidates = (await candidateResponse.json()) as HotCandidate[];
        setCandidates(loadedCandidates);
        setSelectedId(loadedCandidates[0]?.id ?? "");
        setTask((await taskResponse.json()) as TaskDefinition);
        await Promise.all(loadedCandidates.map((item) => loadPreview(item.id)));
        setNotice("GPR予測と熱延実績を同期しました");
      } catch {
        setNotice("熱延タスクを読み込めません。API接続を確認してください");
      }
    };
    void load();
  }, []);

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
      const response = await fetch(`${API_URL}/api/hot-rolling/candidates/${candidate.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(candidate) });
      if (!response.ok) throw new Error();
      const saved = (await response.json()) as HotCandidate;
      setCandidates((items) => items.map((item) => item.id === saved.id ? saved : item));
      await loadPreview(saved.id);
      setNotice(`${saved.name}を保存し、GPR予測を更新しました`);
    } catch {
      setNotice("入力を保存できません。温度・板厚の関係を確認してください");
    }
  }

  async function addCandidate() {
    if (!selected || candidates.length >= 10) return;
    const payload = { ...selected, name: `${selected.name} コピー` };
    delete (payload as Partial<HotCandidate>).id;
    const response = await fetch(`${API_URL}/api/hot-rolling/candidates`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) return;
    const created = (await response.json()) as HotCandidate;
    setCandidates((items) => [...items, created]);
    setSelectedId(created.id);
    await loadPreview(created.id);
  }

  async function deleteCandidate() {
    if (!selected || candidates.length <= 1) return;
    const response = await fetch(`${API_URL}/api/hot-rolling/candidates/${selected.id}`, { method: "DELETE" });
    if (!response.ok) return;
    const remaining = candidates.filter((item) => item.id !== selected.id);
    setCandidates(remaining);
    setSelectedId(remaining[0].id);
  }

  if (!selected || !task) return <section className="hot-loading"><h2>熱延条件の候補検討</h2><p>{notice}</p></section>;

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
          {task.inputs.map((input) => (
            <label key={input.field}>{input.label}<span><input type="number" step="any" value={Number(selected[input.field])} onChange={(event) => localUpdate(input.field, Number(event.target.value))} onBlur={() => void persist(selected)} /><em>{input.unit}</em></span><small>学習 {n(input.min)}–{n(input.max)}</small></label>
          ))}
        </div>
        <div className="hot-derived"><span>圧下率</span><b>{n(reduction)}%</b><small>入出側板厚から算出</small></div>
        <label className="hot-route">ルート<select value={selected.route} onChange={(event) => { const next = { ...selected, route: event.target.value as HotCandidate["route"] }; setCandidates((items) => items.map((item) => item.id === next.id ? next : item)); void persist(next); }}>{task.categorical_inputs.route.map((route) => <option key={route}>{route}</option>)}</select></label>
        <p className="hot-context">設備 {task.context.equipment} · 引張方向 {task.context.test_direction}</p>
      </aside>

      <section className="hot-main">
        <header className="hot-main-header"><div><span className="overline">CANDIDATE COMPARISON</span><h2>熱延条件と予測特性</h2></div><div><button onClick={() => void addCandidate()} disabled={candidates.length >= 10}>複製</button><button className="danger-quiet" onClick={() => void deleteCandidate()} disabled={candidates.length <= 1}>削除</button></div></header>
        <div className="hot-table-wrap"><table className="hot-table"><thead><tr><th>候補</th><th>仕上</th><th>巻取</th><th>冷却</th><th>圧下</th><th>TS</th><th>YS</th><th>EL</th><th>支持度</th></tr></thead><tbody>{candidates.map((item) => { const itemPreview = previews[item.id]; return <tr key={item.id} className={item.id === selected.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><th>{item.name}<small>Route {item.route}</small></th><td>{n(item.finish_temperature_c, 0)}℃</td><td>{n(item.coiling_temperature_c, 0)}℃</td><td>{n(item.cooling_rate_c_s)}℃/s</td><td>{n((1 - item.exit_thickness_mm / item.entry_thickness_mm) * 100)}%</td>{["TS", "YS", "EL"].map((target) => <td key={target}>{itemPreview ? n(itemPreview.predictions[target].value, target === "EL" ? 1 : 0) : "—"}</td>)}<td><span className={`hot-support-dot ${itemPreview?.support.status ?? ""}`} />{itemPreview?.support.status === "supported" ? "範囲内" : itemPreview?.support.status === "extrapolated" ? "外挿" : "要確認"}</td></tr>; })}</tbody></table></div>
        <section className="hot-composition-comparison">
          <header><span className="overline">COMPOSITION</span><h3>組成の候補比較</h3></header>
          <div className="hot-table-wrap"><table className="hot-table hot-composition-table"><thead><tr><th>候補</th>{compositionNames.map((name) => <th key={name}>{name}</th>)}</tr></thead><tbody>{candidates.map((item) => <tr key={item.id} className={item.id === selected.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><th>{item.name}</th>{compositionNames.map((name) => <td key={name}>{n(item.composition[name] ?? 0, name === "C" || name === "Mn" || name === "Si" ? 3 : 4)}</td>)}</tr>)}</tbody></table></div>
        </section>
        <p className="hot-notice">{notice}</p>
      </section>

      <aside className="hot-evidence">
        <div><span className="overline">PREDICTION EVIDENCE</span><h2>予測と不確かさ</h2><small>{preview?.model_meta.model.id} · {preview?.model_meta.model.version}</small></div>
        {preview && ["TS", "YS", "EL"].map((target) => { const prediction = preview.predictions[target]; const parts = prediction.uncertainty_components ?? {}; return <section className="hot-metric" key={target}><header><b>{target}</b><strong>{n(prediction.value, target === "EL" ? 1 : 0)} <small>{prediction.unit}</small></strong></header><div className="hot-interval"><span style={{ left: `${Math.max(0, Math.min(100, ((prediction.value - prediction.lower) / Math.max(prediction.upper - prediction.lower, 1e-6)) * 100))}%` }} /></div><p>{n(prediction.lower)}–{n(prediction.upper)} {prediction.unit}</p><dl><div><dt>モデル</dt><dd>±{n(parts.latent_model_std ?? Math.sqrt(parts.latent_model_variance ?? 0))}</dd></div><div><dt>測定ばらつき</dt><dd>±{n(parts.observation_noise_std ?? Math.sqrt(parts.observation_noise_variance ?? 0))}</dd></div></dl></section>; })}
        {preview && <section className={`hot-support ${preview.support.status}`}><b>{preview.support.status === "supported" ? "学習範囲内" : preview.support.status === "extrapolated" ? "外挿" : "要確認"}</b><p>{preview.support.message}</p><small>距離百分位 {n(preview.support.percentile, 0)}%</small></section>}
        <section className="hot-neighbors"><h3>近い熱延実績</h3>{preview?.similar.map((item) => <article key={item.parent_key}><b>{item.parent_key}</b><span>距離 {n(item.distance, 2)}</span><p>{Object.entries(item.repeat_summary).map(([key, value]) => `${key} ${n(value.mean)} ± ${n(value.std)} (n=${value.n})`).join(" / ")}</p></article>)}</section>
      </aside>
    </div>
  );
}
