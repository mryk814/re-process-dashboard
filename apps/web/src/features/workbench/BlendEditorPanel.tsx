import { useEffect, useMemo, useState } from "react";
import type { CandidateViewModel as Candidate } from "../candidates";
import type { CandidateProvenance } from "../../shared/candidateProvenance";
import {
  workbenchApi,
  type ApiBlendEditorContext,
  type ApiDeterministicTransformResult,
} from "../../shared/api/workbench-api";
import {
  addBlendMaterial,
  blendScientificIdentity,
  compatibleBlendContext,
  createInitialBlend,
  editBlendRatio,
  filterBlendMaterials,
  parseBlendPaste,
  removeBlendMaterial,
  replaceBlendMaterial,
  validatePasteRows,
  type PasteRow,
} from "./blendEditor";

type Props = {
  projectId: string;
  candidate: Candidate;
  transformId?: string;
  onBlend: (
    candidateId: string,
    blend: NonNullable<Candidate["raw"]["blend"]>,
    lockedMaterialIds?: string[],
  ) => void;
  onLocks: (candidateId: string, lockedMaterialIds: string[]) => void;
};

const format = (value: number, digits = 2) => value.toLocaleString("ja-JP", {
  maximumFractionDigits: digits,
  minimumFractionDigits: digits,
});

function statusLabel(status: string) {
  if (status === "試作限定") return <strong className="blend-procurement trial">試作</strong>;
  if (status === "廃止予定") return <strong className="blend-procurement retired">廃止予定</strong>;
  if (status === "条件付") return <strong className="blend-procurement conditional">条件付</strong>;
  return null;
}

export function BlendEditorPanel({ projectId, candidate, transformId, onBlend, onLocks }: Props) {
  const blend = candidate.raw.blend;
  const [context, setContext] = useState<ApiBlendEditorContext | null>(null);
  const [contextError, setContextError] = useState("");
  const [result, setResult] = useState<ApiDeterministicTransformResult | null>(null);
  const [baseline, setBaseline] = useState<ApiDeterministicTransformResult | null>(null);
  const [computing, setComputing] = useState(false);
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const [materialType, setMaterialType] = useState("");
  const [includeRetired, setIncludeRetired] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [replacementFor, setReplacementFor] = useState("");
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteRows, setPasteRows] = useState<PasteRow[]>([]);
  const [showAllComponents, setShowAllComponents] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const contexts = transformId && blend
      ? workbenchApi.resolveBlendEditorContext(transformId, blend).then((item) => [item])
      : transformId
        ? workbenchApi.blendEditorContext(transformId).then((item) => [item])
      : workbenchApi.deterministicTransforms()
        .then((transforms) => Promise.all(transforms.map((item) => workbenchApi.blendEditorContext(item.transform_id))));
    void contexts
      .then((contexts) => {
        if (cancelled) return;
        const matched = blend
          ? contexts.find((item) => compatibleBlendContext(blend, item))
          : contexts[0];
        if (!matched) throw new Error("候補の版に対応する原料catalogがありません");
        setContext(matched);
        setContextError("");
      })
      .catch((cause) => {
        if (!cancelled) setContextError(cause instanceof Error ? cause.message : "原料catalogを取得できませんでした");
      });
    return () => {
      cancelled = true;
    };
  }, [transformId, blend?.scientific_master.digest, blend?.commercial_catalog.digest, blend?.design_space.digest]);

  const scientificIdentity = blend ? blendScientificIdentity(blend) : "";
  useEffect(() => {
    if (!blend || !context) return;
    const controller = new AbortController();
    setComputing(true);
    void workbenchApi.executeDeterministicTransform(context.transform_id, blend, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) {
          setResult(next);
          setMessage("");
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setMessage(cause instanceof Error ? cause.message : "Stage Aを計算できませんでした");
      })
      .finally(() => {
        if (!controller.signal.aborted) setComputing(false);
      });
    return () => controller.abort();
  }, [context?.transform_id, scientificIdentity]);

  useEffect(() => {
    const provenance = candidate.raw.provenance as CandidateProvenance;
    if (!context || provenance.source_kind !== "copy") {
      setBaseline(null);
      return;
    }
    let cancelled = false;
    void workbenchApi.candidateRevision(
      provenance.source_ref.project_id,
      provenance.source_ref.candidate_id,
      provenance.source_ref.candidate_revision,
    ).then((origin) => origin.blend
      ? workbenchApi.executeDeterministicTransform(context.transform_id, origin.blend)
      : null)
      .then((value) => {
        if (!cancelled) setBaseline(value);
      })
      .catch(() => {
        if (!cancelled) setBaseline(null);
      });
    return () => {
      cancelled = true;
    };
  }, [candidate.id, context?.transform_id]);

  const byId = useMemo(() => new Map(context?.materials.map((item) => [item.material_id, item]) ?? []), [context]);
  const groups = useMemo(() => [...new Set(context?.materials.map((item) => item.group) ?? [])].sort(), [context]);
  const types = useMemo(
    () => [...new Set(context?.materials.filter((item) => !group || item.group === group).map((item) => item.material_type) ?? [])].sort(),
    [context, group],
  );
  const filtered = useMemo(
    () => context ? filterBlendMaterials(context.materials, query, group, materialType, includeRetired) : [],
    [context, query, group, materialType, includeRetired],
  );
  const total = blend?.items.reduce((sum, item) => sum + item.ratio, 0) ?? 0;
  const validation = candidate.raw.blend_validation ?? { status: "not_applicable" as const, issues: [] };
  const valid = validation.status === "valid";
  const locked = candidate.raw.editor_state?.locked_material_ids ?? [];
  const contributions = new Map(result?.material_cost_contributions.map((item) => [item.material_id, item]) ?? []);
  const componentRows = useMemo(() => {
    if (!result) return [];
    return Object.entries(result.material_composition)
      .map(([name, value]) => ({
        name,
        value,
        delta: baseline ? value - (baseline.material_composition[name] ?? 0) : value,
      }))
      .filter((item) => item.value !== 0 || item.delta !== 0)
      .sort((left, right) => Math.abs(right.delta) - Math.abs(left.delta));
  }, [baseline, result]);

  if (!blend) {
    return (
      <section className="blend-editor-panel blend-editor-launcher" aria-label="配合明細エディタ">
        <header><div><h3>配合明細</h3><span>Stage Aから材料成分を派生</span></div></header>
        {contextError && <p className="blend-editor-message" role="alert">{contextError}</p>}
        <button
          type="button"
          disabled={!context}
          onClick={() => context && onBlend(candidate.id, createInitialBlend(context), [])}
        >
          初期配合を作成
        </button>
      </section>
    );
  }
  const updateBlend = (next: typeof blend, nextMessage = "", nextLocks?: string[]) => {
    onBlend(candidate.id, next, nextLocks);
    setMessage(nextMessage);
  };
  const applyPaste = () => {
    if (!context || pasteRows.some((row) => row.error)) return;
    let next = blend;
    for (const row of pasteRows) {
      next = addBlendMaterial(next, row.materialId).blend;
      next = editBlendRatio(next, locked, context, row.materialId, Number(row.ratioText)).blend;
    }
    updateBlend(next);
    setPasteRows([]);
    setPasteText("");
    setPasteOpen(false);
  };

  return (
    <section className="blend-editor-panel" aria-label="配合明細エディタ">
      <header>
        <div>
          <h3>配合明細</h3>
          <span>{blend.items.length}点 · core mass %</span>
        </div>
        <div className="blend-editor-summary">
          <strong className={valid ? "valid" : "invalid"}>{valid ? "成立" : "要確認"}</strong>
          <b>合計 {format(total, 3)}%</b>
          <b>粉体配合コスト {result ? `${format(result.powder_blend_cost_yen_per_kg_core, 0)} 円/kg-core` : "—"}</b>
          {computing && <small>Stage A 更新中</small>}
        </div>
      </header>
      <p className="blend-editor-boundary-note">
        配合変更はStage A派生成分だけを更新します。Stage B予測の入力成分は変わりません。
      </p>
      {(contextError || message) && <p className="blend-editor-message" role="status">{contextError || message}</p>}
      {!valid && validation.issues.length > 0 && (
        <details className="blend-editor-issues">
          <summary>{validation.issues.length}件の成立条件を確認</summary>
          <ul>{validation.issues.map((issue) => <li key={`${issue.code}:${issue.path}`}>{issue.message}</li>)}</ul>
        </details>
      )}
      <div className="blend-editor-scroll">
        <table>
          <thead><tr><th>原料</th><th>調達</th><th>比率 %</th><th>単価</th><th>金額寄与</th><th>操作</th></tr></thead>
          <tbody>
            {blend.items.map((item) => {
              const material = byId.get(item.material_id);
              const contribution = contributions.get(item.material_id);
              const isLocked = locked.includes(item.material_id);
              const isBalance = item.material_id === blend.balance_material_id;
              const replacementCandidates = context?.materials.filter((candidateMaterial) => (
                candidateMaterial.material_type === material?.material_type
                && candidateMaterial.material_id !== item.material_id
                && !blend.items.some((line) => line.material_id === candidateMaterial.material_id)
                && (includeRetired || candidateMaterial.procurement !== "廃止予定")
              )) ?? [];
              return (
                <tr key={item.material_id} className={isBalance ? "balance" : ""}>
                  <td><b>{material?.name ?? item.material_id}</b><small>{item.material_id} · {material?.group ?? "—"} / {material?.material_type ?? "—"}</small><small>{material?.main_components.join("・") || "主成分—"} · D50 {material ? format(material.d50_um, 1) : "—"} μm</small></td>
                  <td>{material && statusLabel(material.procurement)}<small>{material?.procurement ?? "—"}</small></td>
                  <td><input type="number" step="any" disabled={isLocked} defaultValue={item.ratio} key={`${candidate.id}:${item.material_id}:${item.ratio}`} aria-label={`${material?.name ?? item.material_id} 配合比`} onBlur={(event) => {
                    if (!context) return;
                    const edited = editBlendRatio(blend, locked, context, item.material_id, Number(event.target.value));
                    updateBlend(edited.blend, edited.message);
                  }} />{isBalance && <em>残部</em>}</td>
                  <td>{material ? `${format(material.unit_price_yen_per_kg_core, 0)} 円` : "—"}</td>
                  <td>{contribution ? <><b>{format(contribution.contribution_yen_per_kg_core, 0)} 円</b><small>{format(contribution.share_of_blend_cost * 100, 1)}%</small></> : "—"}</td>
                  <td>
                    <button type="button" className={isLocked ? "active" : ""} aria-pressed={isLocked} onClick={() => onLocks(candidate.id, isLocked ? locked.filter((id) => id !== item.material_id) : [...locked, item.material_id])}>{isLocked ? "lock中" : "lock"}</button>
                    {!isBalance && <button type="button" disabled={isLocked} onClick={() => setReplacementFor(replacementFor === item.material_id ? "" : item.material_id)}>置換</button>}
                    {!isBalance && <button type="button" disabled={isLocked} onClick={() => {
                      const removed = removeBlendMaterial(blend, item.material_id, locked);
                      updateBlend(removed.blend, removed.message, removed.lockedMaterialIds);
                    }}>削除</button>}
                    {replacementFor === item.material_id && (
                      <select aria-label={`${material?.name ?? item.material_id}の置換先`} defaultValue="" onChange={(event) => {
                        if (!event.target.value) return;
                        const replaced = replaceBlendMaterial(blend, locked, item.material_id, event.target.value);
                        updateBlend(replaced.blend, replaced.message);
                        setReplacementFor("");
                      }}>
                        <option value="">同じ原料種類から選択</option>
                        {replacementCandidates.map((replacement) => <option key={replacement.material_id} value={replacement.material_id}>{replacement.name} / {replacement.procurement} / {format(replacement.unit_price_yen_per_kg_core, 0)}円</option>)}
                      </select>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="blend-editor-actions">
        <button type="button" onClick={() => setShowPicker((value) => !value)} disabled={!context || blend.items.length >= (context?.design_space.selection_count.maximum ?? 20)}>原料を追加</button>
        <button type="button" onClick={() => setPasteOpen((value) => !value)} disabled={!context}>複数行を貼り付け</button>
      </div>
      {showPicker && context && (
        <div className="blend-material-picker">
          <div>
            <select aria-label="原料グループ" value={group} onChange={(event) => { setGroup(event.target.value); setMaterialType(""); }}><option value="">すべての群</option>{groups.map((value) => <option key={value}>{value}</option>)}</select>
            <select aria-label="原料種類" value={materialType} onChange={(event) => setMaterialType(event.target.value)}><option value="">すべての役割・種類</option>{types.map((value) => <option key={value}>{value}</option>)}</select>
            <input type="search" placeholder="コード・名称・主成分で検索" value={query} onChange={(event) => setQuery(event.target.value)} />
            <label><input type="checkbox" checked={includeRetired} onChange={(event) => setIncludeRetired(event.target.checked)} />廃止予定も表示</label>
          </div>
          <ul>{filtered.slice(0, 60).map((material) => <li key={material.material_id}><button type="button" disabled={blend.items.some((item) => item.material_id === material.material_id)} onClick={() => {
            const added = addBlendMaterial(blend, material.material_id);
            updateBlend(added.blend, added.message);
            if (!added.message) setShowPicker(false);
          }}><b>{material.name}</b><span>{material.material_id} · {material.group} / {material.material_type}</span><span>{material.main_components.join("・")} · {format(material.unit_price_yen_per_kg_core, 0)}円</span>{statusLabel(material.procurement)}</button></li>)}</ul>
        </div>
      )}
      {pasteOpen && context && (
        <div className="blend-paste-editor">
          <label>Excelから「原料コード　比率」の2列を貼り付け<textarea value={pasteText} placeholder={"RM-0001\t12.5\nRM-0042\t3.0"} onChange={(event) => {
            const value = event.target.value;
            setPasteText(value);
            setPasteRows(parseBlendPaste(value, context.materials, blend.items.map((item) => item.material_id)));
          }} /></label>
          {pasteRows.length > 0 && <table><thead><tr><th>行</th><th>原料コード</th><th>比率</th><th>確認</th></tr></thead><tbody>{pasteRows.map((row, index) => <tr key={row.row} className={row.error ? "invalid" : ""}><td>{row.row}</td><td><input list="blend-material-codes" value={row.materialId} onChange={(event) => setPasteRows((current) => validatePasteRows(current.map((item, currentIndex) => ({ ...item, ...(currentIndex === index ? { materialId: event.target.value } : {}) })), context.materials, blend.items.map((item) => item.material_id)))} /></td><td><input value={row.ratioText} onChange={(event) => setPasteRows((current) => validatePasteRows(current.map((item, currentIndex) => ({ ...item, ...(currentIndex === index ? { ratioText: event.target.value } : {}) })), context.materials, blend.items.map((item) => item.material_id)))} /></td><td>{row.columnCount > 2 ? <button type="button" onClick={() => setPasteRows((current) => validatePasteRows(current.map((item, currentIndex) => ({ ...item, ...(currentIndex === index ? { columnCount: 2 } : {}) })), context.materials, blend.items.map((item) => item.material_id)))}>余分な列を除外</button> : row.error || "追加できます"}</td></tr>)}</tbody></table>}
          <datalist id="blend-material-codes">{context.materials.map((item) => <option key={item.material_id} value={item.material_id}>{item.name}</option>)}</datalist>
          <button type="button" disabled={!pasteRows.length || pasteRows.some((row) => row.error)} onClick={applyPaste}>確認済みの行を追加</button>
        </div>
      )}
      {result && (
        <div className="blend-derived-components">
          <header><div><h4>Stage A 派生材料成分</h4><span>{baseline ? "派生元との差が大きい順" : "値が大きい順"}</span></div><button type="button" onClick={() => setShowAllComponents((value) => !value)}>{showAllComponents ? "上位だけ" : `全${componentRows.length}成分`}</button></header>
          <div>{componentRows.slice(0, showAllComponents ? componentRows.length : 8).map((item) => <span key={item.name}><b>{item.name}</b><em>{format(item.value, 4)}%</em>{baseline && <small className={item.delta > 0 ? "positive" : item.delta < 0 ? "negative" : ""}>{item.delta > 0 ? "+" : ""}{format(item.delta, 4)}</small>}</span>)}</div>
        </div>
      )}
    </section>
  );
}
