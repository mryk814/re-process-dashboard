import { useState } from "react";
import { provenanceLabel, type CandidateProvenance } from "../../shared/candidateProvenance";
import {
  fromApiCandidate,
  type ApplicationCapability,
  type CandidateViewModel as Candidate,
} from "../candidates";
import { workbenchApi } from "../../shared/api/workbench-api";


export function CandidateOrigin({
  candidate,
  broken,
  onOpen,
}: {
  candidate: Candidate;
  broken: boolean;
  onOpen: () => void;
}) {
  const provenance = candidate.raw.provenance as CandidateProvenance;
  const hasOriginNavigation = provenance.source_kind !== "direct" && provenance.source_kind !== "manual";
  const referenceOrigin = provenance.source_kind === "lineage";
  return (
    <div className={`candidate-origin${broken ? " missing" : ""}${referenceOrigin ? " reference-data" : ""}`}>
      <span><b>作成元</b>{referenceOrigin && <i>参照データ由来</i>}{provenanceLabel(provenance)}</span>
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
      {capability?.candidate_excel_import && <small className="candidate-xlsx-hint">1行＝1候補。列名・単位は変更しません。詳細はテンプレート内「入力ルール」。</small>}
      {message && <small>{message}</small>}
    </div>
  );
}
