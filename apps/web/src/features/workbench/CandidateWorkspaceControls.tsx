import { useEffect, useState } from "react";
import { provenanceLabel, type CandidateProvenance } from "../../shared/candidateProvenance";
import {
  fromApiCandidate,
  type ApplicationCapability,
  type CandidateViewModel as Candidate,
  type TaskDefinitionContract,
  type TaskOutputDefinition,
} from "../candidates";
import {
  workbenchApi,
  type ApiCandidateOriginEvidence,
  type ApiHistoricalObservationEvidence,
} from "../../shared/api/workbench-api";
import { HistoricalEvidenceDrawer } from "./HistoricalEvidenceDrawer";
import { originMeasurements, type OriginMeasurement } from "./originEvidence";


export function CandidateOrigin({
  projectId,
  candidate,
  outputs,
  taskDefinition,
  displayDecimalOverrides,
  broken,
  onOpen,
}: {
  projectId: string;
  candidate: Candidate;
  outputs: TaskOutputDefinition[];
  taskDefinition: TaskDefinitionContract | null;
  displayDecimalOverrides?: Record<string, number>;
  broken: boolean;
  onOpen: () => void;
}) {
  const provenance = candidate.raw.provenance as CandidateProvenance;
  const hasOriginNavigation = provenance.source_kind !== "direct" && provenance.source_kind !== "manual";
  const lineageReference = provenance.source_kind === "lineage" ? provenance.source_ref : null;
  const historicalReference = provenance.source_kind === "historical_observation"
    ? provenance.source_ref
    : null;
  const referenceOrigin = lineageReference !== null || historicalReference !== null;
  const [measurements, setMeasurements] = useState<OriginMeasurement[] | null>(null);
  const [originEvidence, setOriginEvidence] = useState<ApiCandidateOriginEvidence | null>(null);
  const [, setHistoricalEvidence] = useState<ApiHistoricalObservationEvidence | null>(null);
  const [originEvidenceState, setOriginEvidenceState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  useEffect(() => {
    setEvidenceOpen(false);
    setMeasurements(null);
    setOriginEvidence(null);
    setHistoricalEvidence(null);
    if (provenance.source_kind === "historical_observation") {
      const controller = new AbortController();
      setOriginEvidenceState("loading");
      void workbenchApi.historicalObservationEvidence(projectId, candidate.id, controller.signal)
        .then((evidence) => {
          if (controller.signal.aborted) return;
          setHistoricalEvidence(evidence);
          setMeasurements(outputs.flatMap((output) => {
            const value = evidence.actual_outputs[output.key];
            return typeof value === "number" ? [{
              key: output.key,
              label: output.label,
              mean: value,
              std: 0,
              count: 1,
              unit: output.unit,
            }] : [];
          }));
          setOriginEvidenceState("ready");
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setMeasurements([]);
            setOriginEvidenceState("error");
          }
        });
      return () => controller.abort();
    }
    if (provenance.source_kind !== "lineage") {
      setOriginEvidenceState("idle");
      return;
    }
    const controller = new AbortController();
    setOriginEvidenceState("loading");
    void workbenchApi.candidateOriginEvidence(projectId, candidate.id, controller.signal)
      .then((evidence) => {
        if (!controller.signal.aborted) {
          setMeasurements(originMeasurements(evidence, outputs));
          setOriginEvidence(evidence);
          setOriginEvidenceState("ready");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setMeasurements([]);
          setOriginEvidence(null);
          setOriginEvidenceState("error");
        }
      });
    return () => controller.abort();
  }, [candidate.id, outputs, projectId, provenance]);
  return (
    <>
      <div className={`candidate-origin${broken ? " missing" : ""}${referenceOrigin ? " reference-data" : ""}`}>
      <span><b>作成元</b>{referenceOrigin && <i>参照データ由来</i>}{provenanceLabel(provenance)}</span>
      {referenceOrigin && (
        <span
          className="candidate-origin-measurements"
          title="候補化した時点の過去実測です。現在の予測値、予測区間、支持範囲とは別の証拠です。"
        >
          <small>{historicalReference ? "過去の実測値（actual）" : "作成元実測"}</small>
          {measurements?.length
            ? measurements.map((measurement) => (
                <b
                  key={measurement.key}
                  title={`${measurement.mean.toLocaleString("ja-JP", { maximumFractionDigits: 1 })} ± ${measurement.std.toLocaleString("ja-JP", { maximumFractionDigits: 1 })} ${measurement.unit} / n=${measurement.count}`}
                >
                  {measurement.label} {measurement.mean.toLocaleString("ja-JP", { maximumFractionDigits: 1 })}
                </b>
              ))
            : <b>—</b>}
        </span>
      )}
      {broken ? (
        <em>コピー元は削除済みか参照できません</em>
      ) : candidate.raw.archived_at ? (
        <em>archive済み候補を参照中</em>
      ) : provenance.source_kind === "copy" && candidate.raw.blend ? (
        <button
          type="button"
          className="outline-button"
          onClick={() => document.querySelector(".blend-comparison-panel")?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          })}
        >
          派生元 revision {provenance.source_ref.candidate_revision} を見る
        </button>
      ) : lineageReference ? (
        <button type="button" className="outline-button" onClick={() => setEvidenceOpen(true)}>作成元の実績を見る</button>
      ) : historicalReference ? (
        <span className="candidate-origin-historical-identity">
          <small>実測record {historicalReference.observation_id} · 親条件 {historicalReference.parent_key} · {historicalReference.source_label}</small>
          <small>Dataset Revision {historicalReference.dataset_view_revision_id}</small>
          <small>現在の予測値・区間・支持範囲は候補結果として別に表示しています</small>
          <details>
            <summary>固定参照の詳細</summary>
            <code>{historicalReference.source_sha256}</code>
          </details>
        </span>
      ) : hasOriginNavigation ? (
        <button type="button" className="outline-button" onClick={onOpen}>作成元へ戻る</button>
      ) : (
        <small>この候補は比較画面で直接作成されました</small>
      )}
      </div>
      <HistoricalEvidenceDrawer
        open={evidenceOpen}
        projectId={projectId}
        reference={lineageReference ? {
          processKey: originEvidence?.process_key ?? lineageReference.entity_key,
          compositionKey: originEvidence?.composition_key ?? lineageReference.composition_entity_key,
          relationContextIds: originEvidence?.relation_context_ids ?? lineageReference.relation_context_ids,
          observationIds: originEvidence?.observation_ids,
          repeatSummary: originEvidence?.repeat_summary,
          measurementState: originEvidenceState === "idle" ? "loading" : originEvidenceState,
        } : null}
        outputs={outputs}
        taskDefinition={taskDefinition}
        displayDecimalOverrides={displayDecimalOverrides}
        onClose={() => setEvidenceOpen(false)}
        onOpenLineage={() => {
          setEvidenceOpen(false);
          onOpen();
        }}
      />
    </>
  );
}


export function CandidateFileControls({
  projectId,
  capability,
  onImported,
}: {
  projectId: string;
  capability?: ApplicationCapability;
  onImported: (items: Candidate[]) => void;
}) {
  const [message, setMessage] = useState("");
  const upload = async (file?: File) => {
    if (!file) return;
    try {
      const body = await workbenchApi.importCandidates(projectId, file);
      const imported = body.candidates.map(fromApiCandidate);
      onImported(imported);
      setMessage(
        `${body.created}件を取り込みました${body.errors.length ? `（${body.errors.length}件は確認が必要）` : ""}`,
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "XLSXを取り込めませんでした。",
      );
    }
  };
  const download = () => {
    window.location.assign(workbenchApi.candidateExportUrl(projectId));
  };
  const downloadTemplate = () => {
    window.location.assign(workbenchApi.candidateTemplateUrl(projectId));
  };
  return (
    <div className="file-controls">
      {capability?.candidate_excel_import && <button className="outline-button" onClick={downloadTemplate}>
        入力テンプレート
      </button>}
      {capability?.candidate_excel_import && <label className="outline-button">
        候補XLSXを読込
        <input
          type="file"
          accept=".xlsx"
          onChange={(event) => {
            void upload(event.target.files?.[0]);
          }}
          hidden
        />
      </label>}
      {capability?.candidate_excel_export && <button className="outline-button" onClick={download}>
        候補・予測をXLSX出力
      </button>}
      {message && <small>{message}</small>}
    </div>
  );
}
