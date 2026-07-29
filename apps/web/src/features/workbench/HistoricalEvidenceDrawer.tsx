import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  workbenchApi,
  type ApiCandidateOriginEvidence,
  type ApiLineage,
  type ApiLineageCandidateOption,
} from "../../shared/api/workbench-api";
import { formatTaskNumber } from "../../shared/taskPresentation";
import type { TaskDefinitionContract, TaskOutputDefinition } from "../candidates";

export type HistoricalEvidenceReference = {
  processKey: string;
  compositionKey?: string | null;
  relationContextIds?: string[];
  observationIds?: string[];
  repeatSummary?: ApiCandidateOriginEvidence["repeat_summary"];
  observedOutputs?: Record<string, number>;
  measurementState?: "loading" | "ready" | "error";
  distance?: number;
  source?: string;
};

type MeasurementSummary = {
  mean: number;
  std: number;
  count: number;
  min?: number;
  max?: number;
};

function measurementSummary(
  reference: HistoricalEvidenceReference,
  output: TaskOutputDefinition,
): MeasurementSummary | null {
  const keys = [...(output.measurement_keys ?? []), output.key, output.label];
  for (const key of keys) {
    const repeat = reference.repeatSummary?.[key];
    if (repeat) return { mean: repeat.mean, std: repeat.std, count: repeat.n };
    const observed = reference.observedOutputs?.[key];
    if (typeof observed === "number") return { mean: observed, std: 0, count: 1 };
  }
  return null;
}

function optionKey(option: ApiLineageCandidateOption) {
  return `${option.process_key}\u001f${option.melt_key}`;
}

function valueText(value: string | number | boolean | null) {
  if (value === null || value === "") return "—";
  if (typeof value === "number") {
    return value.toLocaleString("ja-JP", { maximumFractionDigits: Math.abs(value) < 0.01 ? 5 : 3 });
  }
  return String(value);
}

function HeatPatternMini({ points }: { points: ApiLineage["node"]["heat_pattern"] }) {
  if (!points.length) return <p className="empty-evidence">ヒートパターンはありません。</p>;
  const width = 420;
  const height = 112;
  const left = 30;
  const right = 12;
  const top = 12;
  const bottom = 22;
  const minTime = Math.min(...points.map((point) => point.time_s));
  const maxTime = Math.max(...points.map((point) => point.time_s));
  const maxTemperature = Math.max(1, ...points.map((point) => point.temperature_c));
  const x = (time: number) => left + ((time - minTime) / Math.max(maxTime - minTime, 1)) * (width - left - right);
  const y = (temperature: number) => top + (1 - temperature / maxTemperature) * (height - top - bottom);
  const path = points.map((point) => `${x(point.time_s)},${y(point.temperature_c)}`).join(" ");
  const stages = [...new Set(points.map((point) => point.stage_name?.trim()).filter((name): name is string => Boolean(name)))];
  return (
    <>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`実績ヒートパターン、${points.length}点、最高${maxTemperature.toLocaleString("ja-JP")}℃`}>
        <line x1={left} x2={width - right} y1={height - bottom} y2={height - bottom} />
        <line x1={left} x2={left} y1={top} y2={height - bottom} />
        <polyline points={path} fill="none" />
        {points.map((point, index) => <circle key={`${point.time_s}-${index}`} cx={x(point.time_s)} cy={y(point.temperature_c)} r="2.5" />)}
        <text x={left} y={height - 6}>{minTime.toLocaleString("ja-JP")} s</text>
        <text x={width - right} y={height - 6} textAnchor="end">{maxTime.toLocaleString("ja-JP")} s</text>
        <text x={left - 4} y={top + 4} textAnchor="end">{maxTemperature.toLocaleString("ja-JP")}℃</text>
      </svg>
      {stages.length > 0 && <div className="historical-evidence-stages">{stages.map((stage) => <span key={stage}>{stage}</span>)}</div>}
    </>
  );
}

export function HistoricalEvidenceDrawer({
  open,
  projectId,
  reference,
  outputs,
  taskDefinition,
  displayDecimalOverrides,
  onClose,
  onOpenLineage,
  onAddCandidate,
}: {
  open: boolean;
  projectId: string;
  reference: HistoricalEvidenceReference | null;
  outputs: TaskOutputDefinition[];
  taskDefinition?: TaskDefinitionContract | null;
  displayDecimalOverrides?: Record<string, number>;
  onClose: () => void;
  onOpenLineage?: () => void;
  onAddCandidate?: (entityKey: string, processKey?: string, meltKey?: string) => Promise<boolean>;
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const addRequestRef = useRef(0);
  const [lineage, setLineage] = useState<ApiLineage | null>(null);
  const [compositionLineage, setCompositionLineage] = useState<ApiLineage | null>(null);
  const [compositionLoadState, setCompositionLoadState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [loadState, setLoadState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [selectedOptionKey, setSelectedOptionKey] = useState("");
  const [addState, setAddState] = useState<"idle" | "adding" | "added" | "error">("idle");
  const processKey = reference?.processKey ?? "";
  const compositionKey = reference?.compositionKey ?? "";

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButtonRef.current?.focus({ preventScroll: true });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, [open]);

  useEffect(() => {
    setLineage(null);
    setSelectedOptionKey("");
    setAddState("idle");
    if (!open || !processKey) {
      setLoadState("idle");
      return;
    }
    const controller = new AbortController();
    setLoadState("loading");
    void workbenchApi.lineage(projectId, processKey, 40, false, controller.signal)
      .then((loaded) => {
        if (controller.signal.aborted) return;
        setLineage(loaded);
        setLoadState("ready");
        const candidates = (loaded.candidate_options ?? []).filter((option) => option.process_key === processKey);
        const exact = compositionKey
          ? candidates.find((option) => option.melt_key === compositionKey)
          : undefined;
        if (exact) setSelectedOptionKey(optionKey(exact));
        else if (candidates.length === 1) setSelectedOptionKey(optionKey(candidates[0]));
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoadState("error");
      });
    return () => controller.abort();
  }, [compositionKey, open, processKey, projectId]);

  useEffect(() => {
    setCompositionLineage(null);
    if (!open || !compositionKey) {
      setCompositionLoadState("idle");
      return;
    }
    const controller = new AbortController();
    setCompositionLoadState("loading");
    void workbenchApi.lineage(projectId, compositionKey, 20, false, controller.signal)
      .then((loaded) => {
        if (controller.signal.aborted) return;
        setCompositionLineage(loaded);
        setCompositionLoadState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setCompositionLoadState("error");
      });
    return () => controller.abort();
  }, [compositionKey, open, projectId]);

  useEffect(() => {
    addRequestRef.current += 1;
    setAddState("idle");
  }, [compositionKey, open, processKey]);

  const candidateOptions = useMemo(
    () => (lineage?.candidate_options ?? []).filter((option) => option.process_key === processKey),
    [lineage, processKey],
  );
  const selectedOption = candidateOptions.find((option) => optionKey(option) === selectedOptionKey);
  const addCandidate = async () => {
    if (!reference || !selectedOption || !onAddCandidate) return;
    const requestSequence = ++addRequestRef.current;
    setAddState("adding");
    try {
      const added = await onAddCandidate(reference.processKey, selectedOption.process_key, selectedOption.melt_key);
      if (requestSequence === addRequestRef.current) setAddState(added ? "added" : "error");
    } catch {
      if (requestSequence === addRequestRef.current) setAddState("error");
    }
  };
  const composition = compositionLineage?.node.composition
    && Object.keys(compositionLineage.node.composition).length > 0
    ? compositionLineage.node.composition
    : lineage?.node.composition ?? {};
  if (!open || !reference) return null;
  return createPortal(
    <aside className="historical-evidence-drawer" aria-label="過去実績の根拠">
      <header>
        <div>
          <span className="reference-data-kicker">参照データ</span>
          <h2>過去実績の根拠</h2>
          <p>{reference.processKey}{reference.compositionKey ? ` / 成分 ${reference.compositionKey}` : ""}</p>
        </div>
        <button ref={closeButtonRef} type="button" className="historical-evidence-close" aria-label="過去実績の根拠を閉じる" onClick={onClose}>×</button>
      </header>

      <div className="historical-evidence-body">
        <section className="historical-evidence-summary">
          {typeof reference.distance === "number" && <span><small>候補からの距離</small><b>{reference.distance.toFixed(2)}</b></span>}
          <span><small>接続観測</small><b>{reference.observationIds?.length ?? lineage?.node.connected_observation_count ?? "—"}件</b></span>
          <span><small>元データ</small><b>{lineage?.node.source_sheet ?? reference.source ?? "—"}</b></span>
        </section>

        <section>
          <h3>実測特性</h3>
          <div className="historical-measurements">
            {outputs.map((output) => {
              const summary = measurementSummary(reference, output);
              if (!summary) return <article key={output.key}><small>{output.label}</small><b>—</b></article>;
              const number = taskDefinition
                ? formatTaskNumber(summary.mean, taskDefinition, `output.${output.key}`, displayDecimalOverrides)
                : summary.mean.toLocaleString("ja-JP", { maximumFractionDigits: 3 });
              return <article key={output.key}>
                <small>{output.label}</small>
                <b>{number}<i>{output.unit === "1" ? "" : output.unit}</i></b>
                <span>標準偏差 {summary.std.toLocaleString("ja-JP", { maximumFractionDigits: 3 })} · n={summary.count}</span>
                {summary.min !== undefined && summary.max !== undefined && <span>範囲 {summary.min.toLocaleString("ja-JP")}–{summary.max.toLocaleString("ja-JP")}</span>}
              </article>;
            })}
          </div>
          {reference.measurementState === "loading" && <p className="historical-evidence-status">候補化した時点の作成元実測を読み込んでいます。</p>}
          {reference.measurementState === "error" && <p className="historical-evidence-status warning">候補化した時点の作成元実測を取得できません。現在の集約値では代用していません。</p>}
        </section>

        {lineage?.node.primary_conditions && Object.keys(lineage.node.primary_conditions).length > 0 && <section>
          <h3>工程条件</h3>
          <dl className="historical-evidence-facts">
            {Object.entries(lineage.node.primary_conditions).map(([key, value]) => <div key={key}><dt title={key}>{key}</dt><dd>{valueText(value)}</dd></div>)}
          </dl>
        </section>}

        {(compositionKey || Object.keys(composition).length > 0) && <section>
          <h3>上流組成 {compositionKey && <small>{compositionKey}</small>} <small>mass%</small></h3>
          {Object.keys(composition).length > 0 ? <dl className="historical-evidence-composition">
            {Object.entries(composition).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{valueText(value)}</dd></div>)}
          </dl> : compositionLoadState === "error"
            ? <p className="historical-evidence-status warning">選択した上流成分の組成詳細を取得できません。</p>
            : <p className="historical-evidence-status">選択した上流成分の組成を読み込んでいます。</p>}
        </section>}

        {lineage?.node.heat_pattern && lineage.node.heat_pattern.length > 0 && <section>
          <h3>実績ヒートパターン</h3>
          <div className="historical-heat-pattern"><HeatPatternMini points={lineage.node.heat_pattern} /></div>
        </section>}

        <details className="historical-evidence-technical">
          <summary>接続根拠</summary>
          <dl>
            <div><dt>工程キー</dt><dd>{reference.processKey}</dd></div>
            <div><dt>成分キー</dt><dd>{reference.compositionKey ?? "—"}</dd></div>
            <div><dt>relation</dt><dd>{reference.relationContextIds?.join(", ") || "—"}</dd></div>
            <div><dt>観測ID</dt><dd>{reference.observationIds?.join(", ") || "—"}</dd></div>
          </dl>
        </details>

        {loadState === "loading" && <p className="historical-evidence-status">工程条件と系譜を読み込んでいます。</p>}
        {loadState === "error" && <p className="historical-evidence-status warning">詳細な系譜は取得できませんでした。実測特性と保存済みの接続根拠は確認できます。</p>}
      </div>

      <footer>
        {onOpenLineage && <button type="button" className="text-button" onClick={onOpenLineage}>データ探索で系譜全体を見る</button>}
        {onAddCandidate && candidateOptions.length > 0 && <div className="historical-evidence-add">
          {candidateOptions.length > 1 && <label>引き継ぐ上流条件
            <select value={selectedOptionKey} onChange={(event) => { setSelectedOptionKey(event.target.value); setAddState("idle"); }}>
              <option value="">選択してください</option>
              {candidateOptions.map((option) => <option key={optionKey(option)} value={optionKey(option)}>{option.process_key} / 成分 {option.melt_key}</option>)}
            </select>
          </label>}
          <button type="button" className="primary-button" disabled={!selectedOption || addState === "adding" || addState === "added"} onClick={() => void addCandidate()}>
            {addState === "adding" ? "追加中…" : addState === "added" ? "候補に追加済み" : "この実績を候補にする"}
          </button>
          {addState === "error" && <small role="alert">候補に追加できませんでした。</small>}
        </div>}
      </footer>
    </aside>,
    document.body,
  );
}
