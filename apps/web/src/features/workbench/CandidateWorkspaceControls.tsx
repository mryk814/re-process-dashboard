import { useEffect, useState } from "react";
import { provenanceLabel, type CandidateProvenance } from "../../shared/candidateProvenance";
import {
  fromApiCandidate,
  type ApplicationCapability,
  type CandidateViewModel as Candidate,
  type TaskOutputDefinition,
} from "../candidates";
import { workbenchApi } from "../../shared/api/workbench-api";
import { originMeasurements, type OriginMeasurement } from "./originEvidence";


export function CandidateOrigin({
  projectId,
  candidate,
  outputs,
  broken,
  onOpen,
}: {
  projectId: string;
  candidate: Candidate;
  outputs: TaskOutputDefinition[];
  broken: boolean;
  onOpen: () => void;
}) {
  const provenance = candidate.raw.provenance as CandidateProvenance;
  const hasOriginNavigation = provenance.source_kind !== "direct" && provenance.source_kind !== "manual";
  const referenceOrigin = provenance.source_kind === "lineage";
  const [measurements, setMeasurements] = useState<OriginMeasurement[] | null>(null);
  useEffect(() => {
    setMeasurements(null);
    if (provenance.source_kind !== "lineage") return;
    const controller = new AbortController();
    void workbenchApi.lineage(projectId, provenance.source_ref.entity_key, 1, controller.signal)
      .then((lineage) => {
        if (!controller.signal.aborted) setMeasurements(originMeasurements(lineage, outputs));
      })
      .catch(() => {
        if (!controller.signal.aborted) setMeasurements([]);
      });
    return () => controller.abort();
  }, [outputs, projectId, provenance]);
  return (
    <div className={`candidate-origin${broken ? " missing" : ""}${referenceOrigin ? " reference-data" : ""}`}>
      <span><b>作成元</b>{referenceOrigin && <i>参照データ由来</i>}{provenanceLabel(provenance)}</span>
      {referenceOrigin && (
        <span
          className="candidate-origin-measurements"
          title="候補化した時点の作成元実測です。候補の条件を編集しても、この参考値は変わりません。"
        >
          <small>作成元実測</small>
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
      ) : hasOriginNavigation ? (
        <button type="button" className="outline-button" onClick={onOpen}>作成元へ戻る</button>
      ) : (
        <small>この候補は比較画面で直接作成されました</small>
      )}
    </div>
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
