import { useEffect, useMemo, useState } from "react";
import type { ApiCandidate } from "../../shared/api/workbench-api";
import {
  type ApiBlendOptimizationContext,
  type ApiBlendOptimizationResult,
  workbenchApi,
} from "../../shared/api/workbench-api";
import {
  availableBlendTargetComponents,
  blendTargetValidationError,
  nextBlendTarget,
  serializeBlendTargets,
  type BlendTargetDraft,
} from "./blendOptimization";

export function BlendOptimizationPanel({
  projectId,
  candidate,
  onCandidateCreated,
}: {
  projectId: string;
  candidate: ApiCandidate;
  onCandidateCreated: (candidate: ApiCandidate) => void;
}) {
  const [context, setContext] = useState<ApiBlendOptimizationContext | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ApiBlendOptimizationResult | null>(null);
  const [objective, setObjective] = useState<"cost" | "baseline_l1">("cost");
  const [inclusionDecisions, setInclusionDecisions] = useState(false);
  const [materialIds, setMaterialIds] = useState<string[]>([]);
  const [targets, setTargets] = useState<BlendTargetDraft[]>([]);
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    setContext(null);
    setResult(null);
    setError("");
    setConfirmed(false);
    if (!candidate.blend) return;
    let cancelled = false;
    setLoading(true);
    workbenchApi.blendOptimizationContext(projectId, candidate.id, candidate.revision)
      .then((loaded) => {
        if (cancelled) return;
        setContext(loaded);
        setMaterialIds(candidate.blend?.items.map((item) => item.material_id) ?? []);
        const firstComponent = loaded.components.find((item) => item !== "Fe") ?? loaded.components[0] ?? "";
        setTargets([{ id: 0, component: firstComponent, lower: "0", upper: "100" }]);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "配合逆算条件を取得できませんでした");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, candidate.id, candidate.revision, candidate.blend]);

  const targetError = blendTargetValidationError(targets);
  const selectedMaterials = useMemo(
    () => context?.materials.filter((item) => materialIds.includes(item.material_id)) ?? [],
    [context, materialIds],
  );
  const canRun = Boolean(
    context
    && confirmed
    && !targetError
    && materialIds.length
    && materialIds.includes(context.balance_material_id)
    && !running,
  );

  if (!candidate.blend) return null;

  async function run() {
    if (!context || !canRun) return;
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const response = await workbenchApi.runBlendOptimization(projectId, candidate.id, {
        expected_revision: candidate.revision,
        name: `${candidate.name} / 配合逆算`,
        objective,
        inclusion_decisions: inclusionDecisions,
        material_ids: materialIds,
        composition_targets: serializeBlendTargets(targets),
      });
      setResult(response);
      if (response.candidate) onCandidateCreated(response.candidate);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "配合逆算を実行できませんでした");
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="blend-optimization-panel" aria-label="材料成分から配合を逆算">
      <header>
        <div>
          <span className="overline">STAGE A · BLEND SOLVE</span>
          <h3>材料成分 → 配合</h3>
        </div>
        <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? "閉じる" : "条件を確認"}
        </button>
      </header>
      {open && (
        <div className="blend-optimization-body">
          {loading && <p role="status">固定条件を読み込んでいます…</p>}
          {error && <p className="blend-optimization-error" role="alert">{error}</p>}
          {context && (
            <>
              <div className="blend-fixed-contract">
                <span>Hoop <b>{context.fixed_hoop_id}</b></span>
                <span>Fill <b>{context.fixed_fill_ratio}%</b></span>
                <span>Balance <b>{context.balance_material_id}</b></span>
                <span>Design Space <b>r{context.design_space.revision}</b></span>
              </div>

              <fieldset>
                <legend>1. 使用可能な原料</legend>
                <p className="blend-availability-note">
                  Design Space r{context.design_space.revision} の allowed_material_ids
                  （{context.materials.length}件）。調達区分は参考表示で、採否制約ではありません。
                </p>
                <div className="blend-material-checks">
                  {context.materials.map((material) => {
                    const checked = materialIds.includes(material.material_id);
                    const balance = material.material_id === context.balance_material_id;
                    return (
                      <label key={material.material_id}>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={balance}
                          onChange={(event) => {
                            setConfirmed(false);
                            setMaterialIds((current) => event.target.checked
                              ? [...current, material.material_id]
                              : current.filter((id) => id !== material.material_id));
                          }}
                        />
                        <span>{material.name}<small>{material.material_id} · {material.group} · {material.procurement}</small></span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              <div className="blend-optimization-grid">
                <fieldset>
                  <legend>2. 目的</legend>
                  <label>
                    <input type="radio" checked={objective === "cost"} onChange={() => { setObjective("cost"); setConfirmed(false); }} />
                    粉体配合コストを最小化
                  </label>
                  <label>
                    <input type="radio" checked={objective === "baseline_l1"} onChange={() => { setObjective("baseline_l1"); setConfirmed(false); }} />
                    基準配合からの変更量（L1）を最小化
                  </label>
                  <label className="blend-inclusion-toggle">
                    <input type="checkbox" checked={inclusionDecisions} onChange={(event) => { setInclusionDecisions(event.target.checked); setConfirmed(false); }} />
                    採否もsolverに決めさせる（MILP）
                  </label>
                  <small>{inclusionDecisions ? "選択数・群内選択数を整数制約として扱います" : "選んだ原料を固定集合としてLPで解きます"}</small>
                </fieldset>

                <fieldset>
                  <legend>3. 材料成分の許容範囲</legend>
                  <div className="blend-target-list">
                    {targets.map((target, index) => (
                      <div className="blend-target-row" key={target.id}>
                        <select
                          aria-label={`成分 ${index + 1}`}
                          value={target.component}
                          onChange={(event) => {
                            setTargets((current) => current.map((item) => (
                              item.id === target.id
                                ? { ...item, component: event.target.value }
                                : item
                            )));
                            setConfirmed(false);
                          }}
                        >
                          {availableBlendTargetComponents(
                            context.components,
                            targets,
                            target.id,
                          ).map((component) => (
                            <option key={component}>{component}</option>
                          ))}
                        </select>
                        <input
                          aria-label={`${target.component} 下限`}
                          type="number"
                          min="0"
                          max="100"
                          step="0.01"
                          value={target.lower}
                          onChange={(event) => {
                            setTargets((current) => current.map((item) => (
                              item.id === target.id
                                ? { ...item, lower: event.target.value }
                                : item
                            )));
                            setConfirmed(false);
                          }}
                        />
                        <span>〜</span>
                        <input
                          aria-label={`${target.component} 上限`}
                          type="number"
                          min="0"
                          max="100"
                          step="0.01"
                          value={target.upper}
                          onChange={(event) => {
                            setTargets((current) => current.map((item) => (
                              item.id === target.id
                                ? { ...item, upper: event.target.value }
                                : item
                            )));
                            setConfirmed(false);
                          }}
                        />
                        <span>mass %</span>
                        <button
                          type="button"
                          className="blend-target-remove"
                          aria-label={`${target.component} の許容範囲を削除`}
                          disabled={targets.length === 1}
                          onClick={() => {
                            setTargets((current) => current.filter((item) => item.id !== target.id));
                            setConfirmed(false);
                          }}
                        >
                          削除
                        </button>
                      </div>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="blend-target-add"
                    disabled={targets.length >= context.components.length}
                    onClick={() => {
                      const nextId = Math.max(-1, ...targets.map((target) => target.id)) + 1;
                      const next = nextBlendTarget(context.components, targets, nextId);
                      if (!next) return;
                      setTargets((current) => [...current, next]);
                      setConfirmed(false);
                    }}
                  >
                    成分を追加
                  </button>
                  {targetError && (
                    <small className="blend-target-error" role="alert">{targetError}</small>
                  )}
                </fieldset>
              </div>

              <details className="blend-constraint-summary">
                <summary>固定される制約を確認</summary>
                <p>配合合計 100% · 選択数 {context.selection_count.minimum}〜{context.selection_count.maximum}</p>
                <p>原料上下限: {context.material_bounds.map((item) => `${item.material_id} ${item.lower}〜${item.upper}%`).join(" / ") || "個別指定なし"}</p>
                <p>群合計: {context.group_totals.map((item) => `${item.group} ${item.lower}〜${item.upper}%`).join(" / ") || "指定なし"}</p>
                <p>群内選択数: {context.group_cardinalities.map((item) => `${item.group} ${item.minimum}〜${item.maximum}`).join(" / ") || "指定なし"}</p>
                <p>対象原料: {selectedMaterials.map((item) => item.material_id).join(", ")}</p>
              </details>

              <div className="blend-run-confirmation">
                <label>
                  <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                  原料・目的・目標範囲・固定制約を確認しました
                </label>
                <button type="button" className="primary-button" disabled={!canRun} onClick={() => void run()}>
                  {running ? "計算中…" : "この条件で逆算"}
                </button>
              </div>

              {result && (
                <div className={`blend-optimization-result ${result.status}`} role="status">
                  <b>{result.status === "feasible" ? "候補を作成しました" : "この条件では解がありません"}</b>
                  <span>{result.message}</span>
                  {result.objective_value != null && (
                    <span>目的関数値 {result.objective_value.toLocaleString("ja-JP", { maximumFractionDigits: 4 })} {result.objective_unit}</span>
                  )}
                  <small>{result.method} · SciPy {result.solver_version}</small>
                  {result.relaxation_candidates.length > 0 && (
                    <ul>
                      {result.relaxation_candidates.map((item, index) => (
                        <li key={`${item.constraint}-${item.direction}-${index}`}>{item.message}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
