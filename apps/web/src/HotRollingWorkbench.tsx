import { useEffect, useState } from "react";
import { fromApiCandidate, toApiCandidate, type CandidateViewModel } from "./candidateModel";
import { CandidateInspector, ComparisonTable } from "./TaskDrivenCandidateUi";
import { setCandidateInputValue, validateResolvedTaskDefinition, type TaskDefinitionContract } from "./taskDefinition";
import { workbenchApi, type ApiPreview } from "./shared/api/workbench-api";
import { useCandidateEditor } from "./useCandidateEditor";
import { candidateInputIdentity } from "./inferenceRequestCache";

const n = (value: number, digits = 1) => value.toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function HotRollingWorkbench({ projectId }: { projectId: string }) {
  const [candidates, setCandidates] = useState<CandidateViewModel[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [previews, setPreviews] = useState<Record<string, ApiPreview>>({});
  const [task, setTask] = useState<TaskDefinitionContract | null>(null);
  const [notice, setNotice] = useState("熱延タスクを読み込んでいます");
  const editor = useCandidateEditor({
    projectId,
    setCandidates,
    onPreview: (candidateId, result) => setPreviews((items) => {
      if (result) return { ...items, [candidateId]: result };
      const { [candidateId]: _, ...remaining } = items;
      return remaining;
    }),
    onNotice: setNotice,
  });
  const selected = candidates.find((item) => item.id === selectedId) ?? candidates[0];
  const preview = selected ? previews[selected.id] : undefined;

  async function loadPreview(candidateId: string, inputs: CandidateViewModel["raw"]["inputs"], shouldApply: () => boolean = () => true) {
    const result = await workbenchApi.previewCandidate(projectId, candidateId, candidateInputIdentity(inputs));
    if (!shouldApply()) return;
    setPreviews((items) => ({ ...items, [candidateId]: result }));
  }

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setTask(null);
        setCandidates([]);
        const resolved = await workbenchApi.taskDefinition(projectId);
        validateResolvedTaskDefinition(resolved);
        if (resolved.task_definition.id !== "hot-rolled-properties-v1") {
          setNotice("熱延後特性タスクのプロジェクトを選択してください");
          return;
        }
        const loadedCandidates = (await workbenchApi.listCandidates(projectId)).map(fromApiCandidate);
        editor.acceptServerCandidates(loadedCandidates.map((candidate) => candidate.raw));
        setCandidates(loadedCandidates);
        setSelectedId(loadedCandidates[0]?.id ?? "");
        setTask(resolved.task_definition);
        await Promise.all(loadedCandidates.map((item) => loadPreview(item.id, item.raw.inputs, () => !cancelled)));
        if (cancelled) return;
        setNotice("GPR予測と熱延実績を同期しました");
      } catch {
        if (!cancelled) setNotice("熱延タスクを読み込めません。API接続を確認してください");
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [projectId]);

  function nameUpdate(candidateId: string, value: string) {
    const current = candidates.find((candidate) => candidate.id === candidateId);
    if (!current) return;
    const next = { ...current, label: value };
    setCandidates((items) => items.map((item) => item.id === candidateId ? next : item));
    editor.schedule(next, current);
  }

  function pathInputUpdate(path: string, value: number | string) {
    if (!selected) return;
    const next = {
      ...selected,
      raw: {
        ...selected.raw,
        inputs: setCandidateInputValue(selected.raw.inputs, path, value),
      },
    };
    setCandidates((items) => items.map((item) => item.id === selected.id ? next : item));
    editor.schedule(next, selected);
  }

  async function addCandidate() {
    if (!selected || candidates.length >= 10) return;
    const payload = toApiCandidate({ ...selected, label: `${selected.label} コピー` });
    const created = fromApiCandidate(await workbenchApi.createCandidate(projectId, payload));
    setCandidates((items) => [...items, created]);
    setSelectedId(created.id);
    await loadPreview(created.id, created.raw.inputs);
  }

  async function deleteCandidate() {
    if (!selected || candidates.length <= 1) return;
    await workbenchApi.deleteCandidate(projectId, selected.id, selected.raw.revision);
    const remaining = candidates.filter((item) => item.id !== selected.id);
    setCandidates(remaining);
    setSelectedId(remaining[0].id);
  }

  if (!selected || !task) return <section className="hot-loading"><h2>熱延条件の候補検討</h2><p>{notice}</p></section>;

  const outputs = task.outputs;

  return (
    <div className="hot-workbench">
      <CandidateInspector
        candidate={selected}
        taskDefinition={task}
        saveState={editor.saveStates[selected.id] ?? "idle"}
        fieldErrors={editor.fieldErrors[selected.id] ?? []}
        onInput={pathInputUpdate}
        onReload={() => editor.reload(selected.id)}
        onCopyDraft={() => void editor.copyDraft(selected)}
        className="hot-inspector"
      />
      <section className="hot-main">
        <header className="hot-main-header"><div><span className="overline">CANDIDATE COMPARISON</span><h2>熱延条件と予測特性</h2></div><div><button onClick={() => void addCandidate()} disabled={candidates.length >= 10}>複製</button><button className="danger-quiet" onClick={() => void deleteCandidate()} disabled={candidates.length <= 1}>削除</button></div></header>
        <ComparisonTable
          candidates={candidates}
          selectedId={selected.id}
          taskDefinition={task}
          previewsByCandidate={previews}
          targetValues={{}}
          onSelect={setSelectedId}
          onName={nameUpdate}
          onInput={(candidateId, path, value) => {
            const current = candidates.find((candidate) => candidate.id === candidateId);
            if (!current) return;
            const next = { ...current, raw: { ...current.raw, inputs: setCandidateInputValue(current.raw.inputs, path, value) } };
            setCandidates((items) => items.map((item) => item.id === candidateId ? next : item));
            editor.schedule(next, current);
          }}
        />
        <p className="hot-notice">{notice}</p>
      </section>

      <aside className="hot-evidence">
        <div><span className="overline">PREDICTION EVIDENCE</span><h2>予測と不確かさ</h2><small>{preview?.model_meta.model?.id ?? "—"} · {preview?.model_meta.model?.version ?? "—"}</small></div>
        {preview && outputs.map((output) => { const prediction = preview.predictions[output.key]; if (!prediction) return null; const parts = prediction.uncertainty_components ?? {}; return <section className="hot-metric" key={output.key}><header><b>{output.label}</b><strong>{n(prediction.value, output.unit === "%" ? 1 : 0)} <small>{prediction.unit}</small></strong></header><div className="hot-interval"><span style={{ left: `${Math.max(0, Math.min(100, ((prediction.value - prediction.lower) / Math.max(prediction.upper - prediction.lower, 1e-6)) * 100))}%` }} /></div><p>{n(prediction.lower)}–{n(prediction.upper)} {prediction.unit}</p><dl><div><dt>モデル</dt><dd>±{n(parts.latent_model_std ?? Math.sqrt(parts.latent_model_variance ?? 0))}</dd></div><div><dt>測定ばらつき</dt><dd>±{n(parts.observation_noise_std ?? Math.sqrt(parts.observation_noise_variance ?? 0))}</dd></div></dl></section>; })}
        {preview && <section className={`hot-support ${preview.support.status}`}><b>{preview.support.status === "supported" ? "学習範囲内" : preview.support.status === "extrapolated" ? "外挿" : "要確認"}</b><p>{preview.support.message}</p><small>距離百分位 {n(preview.support.percentile, 0)}%</small></section>}
        <section className="hot-neighbors"><h3>近い熱延実績</h3>{preview?.similar.map((item) => <article key={item.parent_key}><b>{item.parent_key}</b><span>距離 {n(item.distance, 2)}</span><p>{Object.entries(item.repeat_summary ?? {}).map(([key, value]) => `${key} ${n(value.mean)} ± ${n(value.std)} (n=${value.n})`).join(" / ")}</p></article>)}</section>
      </aside>
    </div>
  );
}
