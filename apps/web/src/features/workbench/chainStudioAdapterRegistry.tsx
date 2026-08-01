import type { ReactNode } from "react";

import type {
  ApiChainExecution,
  ApiChainSnapshot,
} from "../../shared/api/workbench-api";
import { formatNumberAtDecimals } from "../../shared/taskPresentation";

type Stage = ApiChainExecution["stages"][number];
type OutputDefinition = Stage["output_definitions"][number];
type Prediction = {
  value?: number;
  std?: number;
  quantiles?: Record<string, number>;
  samples?: number[];
};

export type ChainCandidateAdapterId = "scalar/v1" | "sparse_blend/v1";

export type ChainEvidenceProps = {
  execution: ApiChainExecution | null;
  snapshot: ApiChainSnapshot | undefined;
  specializedEvidence?: ReactNode;
};

export type ChainEvidenceRendererAdapter = {
  rendererId: string;
  supportsActualComparison: boolean;
  renderCurrent: (props: ChainEvidenceProps) => ReactNode;
};

export type ChainCandidateEditorAdapter = {
  adapterId: ChainCandidateAdapterId;
  label: string;
  requiresSparseBlendContract: boolean;
  supportsUncertaintyPropagation: boolean;
  emptyStateLabel: string;
  stageLabel: (stageId: string) => string;
  renderSnapshotStageAsJson: (stageId: string) => boolean;
  evidence: ChainEvidenceRendererAdapter;
};

function stagePredictions(stage: Stage | ApiChainSnapshot["stages"][number] | undefined) {
  const raw = stage?.result?.predictions;
  return raw && typeof raw === "object" ? raw as Record<string, Prediction> : {};
}

function displayPrediction(prediction: Prediction | undefined, definition: OutputDefinition) {
  if (typeof prediction?.value !== "number" || !Number.isFinite(prediction.value)) return "—";
  const unit = definition.unit.trim();
  const point = `${formatNumberAtDecimals(prediction.value, definition.display_decimals)}${unit ? ` ${unit}` : ""}`;
  if (typeof prediction.std === "number" && Number.isFinite(prediction.std)) {
    return `${point}（標準偏差 ${formatNumberAtDecimals(prediction.std, definition.display_decimals)}${unit ? ` ${unit}` : ""}）`;
  }
  if (prediction.quantiles && Object.keys(prediction.quantiles).length) {
    return `${point}（quantileあり）`;
  }
  if (prediction.samples?.length) return `${point}（sample ${prediction.samples.length}件）`;
  return `${point}（点推定）`;
}

function GenericEvidence({ execution, snapshot }: ChainEvidenceProps) {
  const stages = execution?.stages ?? snapshot?.stages ?? [];
  if (!stages.length) {
    return <p className="chain-output-unavailable">実行後にStageごとの結果と証跡を表示します。</p>;
  }
  return <div className="chain-result-grid" data-chain-evidence-renderer="generic/v1">
    {stages.map((stage) => {
      const definitions = stage.output_definitions;
      const predictions = stagePredictions(stage);
      return <section className="chain-result-card" key={stage.stage_id}>
        <header><div><span>STAGE {stage.stage_id}</span><h3>{definitions.length ? "予測結果" : "Stage evidence"}</h3></div><b className="source-badge predicted">{stage.status}</b></header>
        {definitions.length
          ? <div className="chain-table-scroll" tabIndex={0} aria-label={`Stage ${stage.stage_id}の結果`}>
            <table><thead><tr><th>出力</th><th>値／不確かさ</th><th>metadata</th></tr></thead>
              <tbody>{definitions.map((definition) => <tr key={definition.key}>
                <th>{definition.label}</th>
                <td>{displayPrediction(predictions[definition.key], definition)}</td>
                <td>{definition.unit || "unitなし"}</td>
              </tr>)}</tbody>
            </table>
          </div>
          : <details><summary>technical detail</summary><pre tabIndex={0}>{JSON.stringify(stage.result, null, 2)}</pre></details>}
      </section>;
    })}
  </div>;
}

const scalarAdapter: ChainCandidateEditorAdapter = {
  adapterId: "scalar/v1",
  label: "scalar inputs",
  requiresSparseBlendContract: false,
  supportsUncertaintyPropagation: false,
  emptyStateLabel: "固定契約から基準候補を作成",
  stageLabel: () => "予測Stage",
  renderSnapshotStageAsJson: () => false,
  evidence: {
    rendererId: "generic/v1",
    supportsActualComparison: false,
    renderCurrent: (props) => <GenericEvidence {...props} />,
  },
};

const sparseBlendAdapter: ChainCandidateEditorAdapter = {
  adapterId: "sparse_blend/v1",
  label: "sparse blend",
  requiresSparseBlendContract: true,
  supportsUncertaintyPropagation: true,
  emptyStateLabel: "固定契約から基準配合を作成",
  stageLabel: (stageId) => (
    stageId === "A" ? "材料成分" : stageId === "B" ? "溶着成分" : "特性"
  ),
  renderSnapshotStageAsJson: (stageId) => stageId === "A",
  evidence: {
    rendererId: "sparse-blend/v1",
    supportsActualComparison: true,
    renderCurrent: ({ specializedEvidence }) => specializedEvidence ?? null,
  },
};

const registry = new Map<ChainCandidateAdapterId, ChainCandidateEditorAdapter>([
  [scalarAdapter.adapterId, scalarAdapter],
  [sparseBlendAdapter.adapterId, sparseBlendAdapter],
]);

export function resolveChainCandidateEditorAdapter(adapterId: string) {
  return registry.get(adapterId as ChainCandidateAdapterId);
}

export function registeredChainCandidateAdapterIds() {
  return [...registry.keys()];
}
