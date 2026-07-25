import { useEffect, useMemo, useState } from "react";
import type { CandidateViewModel } from "../candidates";
import {
  workbenchApi,
  type ApiBlendMaterial,
  type ApiCandidate,
} from "../../shared/api/workbench-api";
import { blendComparisonRows, blendCost } from "./blendComparison";


type ComparisonCandidate = {
  key: string;
  candidate: ApiCandidate;
  historical: boolean;
};

type DerivationHistoryState = {
  ownerKey: string;
  items: ApiCandidate[];
  error: string;
};

export function BlendComparisonPanel({
  projectId,
  candidates,
  selected,
}: {
  projectId: string;
  candidates: CandidateViewModel[];
  selected: CandidateViewModel;
}) {
  const selectedKey = `${projectId}:${selected.id}:${selected.raw.revision}`;
  const [historyState, setHistoryState] = useState<DerivationHistoryState>({
    ownerKey: "",
    items: [],
    error: "",
  });
  const [materialsByCandidate, setMaterialsByCandidate] = useState<
    Record<string, ApiBlendMaterial[]>
  >({});
  const [showAll, setShowAll] = useState(false);
  const [materialError, setMaterialError] = useState("");
  const hasBlend = candidates.some((item) => item.raw.blend);
  const history = historyState.ownerKey === selectedKey ? historyState.items : [];
  const historyError = historyState.ownerKey === selectedKey ? historyState.error : "";

  useEffect(() => {
    let live = true;
    setHistoryState({ ownerKey: selectedKey, items: [], error: "" });
    if (!hasBlend || selected.raw.provenance?.source_kind !== "copy") return;
    void workbenchApi.candidateDerivationChain(projectId, selected.id)
      .then((items) => {
        if (live) setHistoryState({ ownerKey: selectedKey, items, error: "" });
      })
      .catch(() => {
        if (live) {
          setHistoryState({
            ownerKey: selectedKey,
            items: [],
            error: "派生履歴を確認できませんでした。",
          });
        }
      });
    return () => {
      live = false;
    };
  }, [hasBlend, projectId, selected.id, selected.raw.provenance, selectedKey]);

  const columns = useMemo<ComparisonCandidate[]>(() => [
    ...history
      .filter((item) => item.blend)
      .reverse()
      .map((candidate) => ({
        key: `${candidate.id}:${candidate.revision}:history`,
        candidate,
        historical: true,
      })),
    ...candidates
      .filter((item) => item.raw.blend)
      .map((item) => ({
        key: `${item.id}:${item.raw.revision}:current`,
        candidate: item.raw,
        historical: false,
      })),
  ], [candidates, history]);

  useEffect(() => {
    let live = true;
    setMaterialsByCandidate({});
    if (!columns.length) return;
    void Promise.all(columns.map(async (column) => {
      const items = await workbenchApi.candidateBlendMaterials(
        column.candidate.project_id,
        column.candidate.id,
        column.candidate.revision,
      );
      return [column.key, items] as const;
    })).then((entries) => {
      if (live) {
        setMaterialsByCandidate(Object.fromEntries(entries));
        setMaterialError("");
      }
    }).catch(() => {
      if (live) setMaterialError("原料名とコストを確認できませんでした。");
    });
    return () => {
      live = false;
    };
  }, [columns]);

  const rows = useMemo(
    () => blendComparisonRows(columns.map((item) => item.candidate), showAll),
    [columns, showAll],
  );
  const descriptors = (materialId: string) =>
    columns.flatMap((column) =>
      (materialsByCandidate[column.key] ?? [])
        .filter((item) => item.material_id === materialId)
    );
  const origin = [...columns].reverse().find((item) => item.historical);
  const originMaterialList = origin ? materialsByCandidate[origin.key] : undefined;
  const originMaterials = originMaterialList
    ? new Map(originMaterialList.map((item) => [item.material_id, item]))
    : null;
  const originCost = origin && originMaterials
    ? blendCost(origin.candidate, originMaterials)
    : null;

  if (!hasBlend) return null;
  return (
    <section className="blend-comparison-panel" aria-label="疎な配合の転置比較">
      <header>
        <div>
          <small>BLEND REVISION</small>
          <h3>配合差分</h3>
          <span>原料を行、候補と派生元revisionを列にして比較します。</span>
        </div>
        <button type="button" className="outline-button" onClick={() => setShowAll((value) => !value)}>
          {showAll ? "差のある原料だけ" : "全原料を表示"}
        </button>
      </header>
      {historyError && <p className="comparison-preview-error" role="alert">{historyError}</p>}
      {materialError && <p className="comparison-preview-error" role="alert">{materialError}</p>}
      <div className="blend-comparison-scroll">
        <table>
          <thead>
            <tr>
              <th>原料</th>
              {columns.map((column) => {
                const materialList = materialsByCandidate[column.key];
                const materials = new Map(
                  (materialList ?? []).map((item) => [item.material_id, item]),
                );
                const cost = materialList == null
                  ? null
                  : blendCost(column.candidate, materials);
                const delta = originCost == null || cost == null || column.historical
                  || column.candidate.id !== selected.id
                  || column.candidate.revision !== selected.raw.revision
                  ? null
                  : cost - originCost;
                return (
                  <th key={column.key}>
                    <span>{column.historical ? "派生元" : "候補"}</span>
                    {column.candidate.name}
                    <small>revision {column.candidate.revision}</small>
                    <small>
                      {cost == null
                        ? (materialError ? "コスト未取得" : "コスト読込中")
                        : `${cost.toLocaleString("ja-JP", { maximumFractionDigits: 0 })} 円/kg-core`}
                      {delta == null ? "" : ` (${delta >= 0 ? "+" : ""}${delta.toLocaleString("ja-JP", { maximumFractionDigits: 0 })})`}
                    </small>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((materialId) => {
              const materialDescriptors = descriptors(materialId);
              const names = Array.from(new Set(materialDescriptors.map((item) => item.name)));
              const groups = Array.from(new Set(materialDescriptors.map((item) => item.group)));
              const hasRevisionDifference = names.length > 1 || groups.length > 1;
              return (
                <tr key={materialId}>
                  <th>
                    <b>{names.join(" / ") || materialId}</b>
                    <small>{materialId}{groups.length ? ` · ${groups.join(" / ")}` : ""}</small>
                    {hasRevisionDifference && <em>revision差</em>}
                  </th>
                  {columns.map((column) => {
                    const ratio = column.candidate.blend?.items
                      .find((item) => item.material_id === materialId)?.ratio;
                    const material = materialsByCandidate[column.key]
                      ?.find((item) => item.material_id === materialId);
                    const originRatio = origin?.candidate.blend?.items
                      .find((item) => item.material_id === materialId)?.ratio ?? 0;
                    const delta = (
                      origin
                      && !column.historical
                      && column.candidate.id === selected.id
                    )
                      ? (ratio ?? 0) - originRatio
                      : null;
                    return (
                      <td key={column.key}>
                        {ratio == null || ratio === 0
                          ? <span>— 未使用</span>
                          : <b>{ratio.toLocaleString("ja-JP", { maximumFractionDigits: 4 })}%</b>}
                        {delta != null && Math.abs(delta) > 1e-9 && (
                          <small>
                            派生元比 {delta >= 0 ? "+" : ""}
                            {delta.toLocaleString("ja-JP", { maximumFractionDigits: 4 })}
                          </small>
                        )}
                        {material?.procurement === "試作限定" && <em>試作限定</em>}
                        {material?.procurement === "廃止予定" && <em>廃止予定</em>}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
            {!rows.length && (
              <tr>
                <td colSpan={columns.length + 1}>表示中の候補に配合差はありません。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
