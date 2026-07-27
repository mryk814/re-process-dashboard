import { useEffect, useRef, useState, type RefObject } from "react";
import {
  workbenchApi,
  type ApiChainDistributionCapability,
  type ApiChainDistributionRun,
} from "../../shared/api/workbench-api";
import { ApiClientError } from "../../shared/api/client";
import {
  DistributionRequestGeneration,
  distributionMatchesIdentity,
  type DistributionRequestIdentity,
} from "./distributionRequestGeneration";
import type { ChainUncertaintyAvailability } from "./chainUncertaintyState";
import "./chain-uncertainty.css";

type DistributionMetric = {
  quantiles: Record<string, number>;
  standard_deviation: number;
};

function metric(summary: DistributionMetric | undefined) {
  if (!summary) return "—";
  const q = summary.quantiles;
  return `${q["0.05"].toLocaleString("ja-JP", { maximumFractionDigits: 3 })}–${q["0.95"].toLocaleString("ja-JP", { maximumFractionDigits: 3 })} · σ ${summary.standard_deviation.toLocaleString("ja-JP", { maximumFractionDigits: 3 })}`;
}

export function ChainUncertaintyPanel({
  projectId,
  candidateId,
  candidateRevision,
  pointExecutionReady,
  chainRevisionDigest,
  pointExecutionRequestId,
  readOnly = false,
  open,
  onOpenChange,
  onAvailabilityChange,
  panelRef,
}: {
  projectId: string;
  candidateId: string;
  candidateRevision: number;
  pointExecutionReady: boolean;
  chainRevisionDigest: string;
  pointExecutionRequestId: string;
  readOnly?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onAvailabilityChange?: (availability: ChainUncertaintyAvailability) => void;
  panelRef?: RefObject<HTMLDetailsElement | null>;
}) {
  const [capability, setCapability] = useState<ApiChainDistributionCapability | null>(null);
  const [run, setRun] = useState<ApiChainDistributionRun | null>(null);
  const [sampleCount, setSampleCount] = useState(512);
  const [seed, setSeed] = useState(20260725);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const requests = useRef(new DistributionRequestGeneration());
  const executionController = useRef<AbortController | null>(null);
  const identity: DistributionRequestIdentity = {
    projectId,
    candidateId,
    candidateRevision,
    chainRevisionDigest,
    pointExecutionRequestId,
  };

  useEffect(() => {
    const controller = new AbortController();
    executionController.current?.abort();
    executionController.current = null;
    const token = requests.current.activate(identity);
    setBusy(false);
    setCapability(null);
    setRun(null);
    setMessage("");
    void Promise.all([
      readOnly
        ? Promise.resolve(null)
        : workbenchApi.chainDistributionCapability(projectId),
      workbenchApi.latestChainDistribution(projectId, candidateId, controller.signal)
        .catch((cause) => {
          if (cause instanceof ApiClientError && cause.status === 404) return null;
          throw cause;
        }),
    ]).then(([nextCapability, latest]) => {
      if (controller.signal.aborted || !requests.current.isCurrent(token)) return;
      if (nextCapability && nextCapability.chain_revision_digest !== chainRevisionDigest) {
        throw new Error("点推定と分布実行条件のChain Revisionが一致しません");
      }
      setCapability(nextCapability);
      setRun(
        latest && distributionMatchesIdentity(latest, identity) ? latest : null,
      );
    }).catch((cause) => {
      if (!controller.signal.aborted && requests.current.isCurrent(token)) {
        setMessage(cause instanceof Error ? cause.message : "分布実行条件を取得できませんでした");
      }
    });
    return () => {
      controller.abort();
      executionController.current?.abort();
      executionController.current = null;
      requests.current.invalidate();
    };
  }, [
    projectId,
    candidateId,
    candidateRevision,
    chainRevisionDigest,
    pointExecutionRequestId,
    readOnly,
  ]);

  useEffect(() => {
    if (!onAvailabilityChange) return;
    const stages = capability?.stages ?? [];
    onAvailabilityChange({
      supportedStages: stages.length
        ? Object.fromEntries(stages.map((stage) => [stage.stage_id, stage.capability.supported]))
        : undefined,
      runComputed: Boolean(run),
    });
    // 親へ渡すのは状態の要約だけなので、capability/runの変化にだけ反応させる。
  }, [capability, run]); // eslint-disable-line react-hooks/exhaustive-deps

  const stageB = run?.stages.find((stage) => stage.stage_id === "B");
  const stageC = run?.stages.find((stage) => stage.stage_id === "C");
  const stageBUncertainty = stageB?.stage_uncertainty ?? {};
  const stageCUncertainty = stageC?.stage_uncertainty ?? {};
  const stageCPropagated = stageC?.propagated_uncertainty ?? {};
  const stageCKeys = Object.keys(stageCUncertainty).sort((a, b) => a.localeCompare(b, "en"));

  async function executeDistribution() {
    executionController.current?.abort();
    const controller = new AbortController();
    executionController.current = controller;
    const token = requests.current.activate(identity);
    setBusy(true);
    setMessage("分布を計算しています");
    try {
      const result = await workbenchApi.runChainDistribution(
        projectId,
        candidateId,
        candidateRevision,
        seed,
        sampleCount,
        controller.signal,
      );
      if (
        controller.signal.aborted
        || !requests.current.isCurrent(token)
        || !distributionMatchesIdentity(result, identity)
      ) return;
      setRun(result);
      setMessage(
        result.status === "completed"
          ? "固定seedの分布を保存しました"
          : "対応Stageだけを計算しました",
      );
    } catch (cause) {
      if (!controller.signal.aborted && requests.current.isCurrent(token)) {
        setMessage(cause instanceof Error ? cause.message : "分布を実行できませんでした");
      }
    } finally {
      if (requests.current.isCurrent(token)) {
        executionController.current = null;
        setBusy(false);
      }
    }
  }

  return <details
    className="chain-uncertainty-panel"
    ref={panelRef}
    open={open}
    onToggle={(event) => onOpenChange?.((event.currentTarget as HTMLDetailsElement).open)}
  >
    <summary>
      <span><b>不確かさを伝播</b><small>点推定とは別に明示実行</small></span>
      <em>{run ? `seed ${run.provenance.seed} · n=${run.provenance.sample_count}` : "未実行"}</em>
    </summary>
    <div className="chain-uncertainty-body">
      <p>
        独立残差正規近似（q05–q95由来）に、各出力の物理境界を適用しています。
        posteriorや出力間相関を持つ分布ではありません。
      </p>
      {!readOnly && <div className="chain-uncertainty-controls">
        <label>samples
          <select value={sampleCount} onChange={(event) => setSampleCount(Number(event.target.value))}>
            {[128, 512, 1024].map((count) => <option key={count} value={count}>{count}</option>)}
          </select>
        </label>
        <label>seed
          <input type="number" min="0" max="2147483647" step="1" value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
        </label>
        <button
          type="button"
          className="primary-button"
          disabled={
            busy
            || !pointExecutionReady
            || !capability?.explicit_run_available
            || !Number.isInteger(seed)
            || seed < 0
          }
          onClick={() => void executeDistribution()}
        >
          {busy ? "計算中…" : "分布を計算して保存"}
        </button>
        {!pointExecutionReady && <small>先に現revisionの点推定を最新にしてください</small>}
      </div>}
      {capability && !capability.full_propagation_supported && <div className="chain-uncertainty-warning">
        {capability.stages.filter((stage) => !stage.capability.supported).map((stage) => (
          <span key={stage.stage_id}>Stage {stage.stage_id}: {stage.capability.reason}</span>
        ))}
      </div>}
      <div className="chain-uncertainty-status" role="status">{message}</div>
      {run && <>
        <div className="chain-uncertainty-provenance">
          <span>algorithm {run.provenance.algorithm}</span>
          <span>candidate r{run.provenance.candidate_revision}</span>
          <span>point {run.provenance.point_execution_request_id.slice(0, 8)}</span>
        </div>
        {stageCKeys.length > 0 && <div className="chain-table-scroll">
          <table>
            <thead><tr><th>Stage C 特性</th><th>Stage固有</th><th>Bから伝播後</th></tr></thead>
            <tbody>{stageCKeys.map((key) => <tr key={key}>
              <th>{key}</th>
              <td>{metric(stageCUncertainty[key])}</td>
              <td>{metric(stageCPropagated[key])}</td>
            </tr>)}</tbody>
          </table>
        </div>}
        {stageB && <details className="chain-stage-b-uncertainty">
          <summary>Stage Bの不確かさ <b>{Object.keys(stageBUncertainty).length}</b></summary>
          <div className="chain-table-scroll"><table>
            <thead><tr><th>成分</th><th>Stage固有</th></tr></thead>
            <tbody>{Object.keys(stageBUncertainty).sort((a, b) => a.localeCompare(b, "en")).map((key) => (
              <tr key={key}><th>{key}</th><td>{metric(stageBUncertainty[key])}</td></tr>
            ))}</tbody>
          </table></div>
        </details>}
      </>}
    </div>
  </details>;
}
