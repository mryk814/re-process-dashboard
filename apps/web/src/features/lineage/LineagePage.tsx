import { useEffect, useRef, useState } from "react";
import { fromApiCandidate, type CandidateViewModel as Candidate, type TaskOutputDefinition } from "../candidates";
import { ApiClientError } from "../../shared/api/client";
import { assessOutputValues, resolveOutputDefinition } from "../../shared/outputPresentation";
import {
  workbenchApi,
  type ApiLineage,
  type ApiLineageIndex,
  type ApiLineageNodeReview,
} from "../../shared/api/workbench-api";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { SvgChartTooltip } from "../../shared/ui/SvgChartTooltip";
import { LineageGraph } from "./LineageGraph";
import {
  beginLineageResourceLoad,
  initialLineageResourceState,
  rejectLineageResourceLoad,
  resolveLineageResourceLoad,
} from "./lineageResourceState";

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

const HEAT_CHART_WIDTH = 420;
const HEAT_PLOT_LEFT = 20;
const HEAT_PLOT_RIGHT = 20;
const HEAT_PLOT_WIDTH = HEAT_CHART_WIDTH - HEAT_PLOT_LEFT - HEAT_PLOT_RIGHT;

function normalizedTimePosition(time: number, maxTime: number) {
  return Math.min(1, Math.max(0, time / Math.max(maxTime, 1)));
}

function primaryConditionPresentation(sourceColumn: string) {
  const unitMatch = sourceColumn.match(/^(.*?)\s*[\[(]([^)\]]+)[)\]]\s*$/);
  const withoutUnit = unitMatch?.[1] ?? sourceColumn;
  const label = withoutUnit
    .replace(/[_./]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return {
    label: label || "条件",
    unit: unitMatch?.[2] ?? "",
    sourceColumn,
  };
}

function lineageResourceUnavailable(cause: unknown) {
  if (!(cause instanceof ApiClientError)) return false;
  return cause.availability?.status === "unavailable"
    || cause.code === "subsystem_unavailable"
    || cause.code === "runtime_unavailable"
    || cause.code === "task-store-unconfigured"
    || cause.code === "task-store-unavailable"
    || cause.code === "model-store-unconfigured"
    || cause.code === "model-store-unavailable";
}

function clientLoadedAt(value: string | null) {
  return value ? new Date(value).toLocaleString("ja-JP") : "";
}

type HeatStageSegment = {
  category: string;
  name: string;
  status: string;
  startTime: number;
  endTime: number;
};

type LineageGroupSelection = {
  parentKey: string;
  entityType: string;
  nodeKeys: string[];
};

type ReviewStatus = ApiLineageNodeReview["status"];

const reviewLabels: Record<ReviewStatus, string> = {
  noted: "メモ",
  later: "後で確認",
  accepted: "問題なし",
  needs_fix: "要修正",
  hidden: "非表示",
};

function heatStageSegments(heat: ApiLineage["node"]["heat_pattern"]): HeatStageSegment[] {
  const segments: HeatStageSegment[] = [];
  heat.forEach((point, index) => {
    const category = point.stage_category ?? "工程";
    const name = point.stage_name ?? "工程点";
    const status = point.mapping_status ?? "";
    const startTime = index === 0 ? 0 : point.time_s;
    const endTime = heat[index + 1]?.time_s ?? point.time_s;
    const last = segments[segments.length - 1];
    if (last && last.category === category && last.name === name) {
      last.endTime = Math.max(last.endTime, endTime);
      if (last.status !== status) last.status = "複数の対応状態";
    } else {
      segments.push({
        category,
        name,
        status,
        startTime,
        endTime,
      });
    }
  });
  return segments.filter((stage) => stage.endTime > stage.startTime);
}
export function LineagePage({
  projectId,
  supportsCandidateCreation,
  outputs,
  initialEntityKey,
  qualityIssueId,
  onEntityChange,
  onReturnToQuality,
  onCandidate,
}: {
  projectId: string;
  supportsCandidateCreation: boolean;
  outputs: TaskOutputDefinition[];
  initialEntityKey?: string;
  qualityIssueId?: string;
  onEntityChange: (entityKey: string) => void;
  onReturnToQuality: () => void;
  onCandidate: (candidate: Candidate) => void;
}) {
  const [entityKey, setEntityKey] = useState(initialEntityKey ?? "");
  const [query, setQuery] = useState("");
  const [entityType, setEntityType] = useState("");
  const [issueFilter, setIssueFilter] = useState<"all" | "with_issues" | "without_issues">("all");
  const indexScope = JSON.stringify([projectId, query.trim(), entityType, issueFilter]);
  const [reviews, setReviews] = useState<ApiLineageNodeReview[]>([]);
  const [reviewResourceState, setReviewResourceState] = useState(
    () => initialLineageResourceState(projectId),
  );
  const [reviewRevision, setReviewRevision] = useState(0);
  const [reviewLedgerOpen, setReviewLedgerOpen] = useState(false);
  const [reviewStatus, setReviewStatus] = useState<ReviewStatus>("noted");
  const [reviewNote, setReviewNote] = useState("");
  const [reviewSaveState, setReviewSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [indexRevision, setIndexRevision] = useState(0);
  const [graphLimit, setGraphLimit] = useState(40);
  const [showAllReachable, setShowAllReachable] = useState(false);
  const [index, setIndex] = useState<ApiLineageIndex | null>(null);
  const [indexResourceState, setIndexResourceState] = useState(
    () => initialLineageResourceState(indexScope),
  );
  const [data, setData] = useState<ApiLineage | null>(null);
  const [error, setError] = useState("");
  const [candidateError, setCandidateError] = useState("");
  const [candidateAddedCount, setCandidateAddedCount] = useState(0);
  const [selectedCandidateOptions, setSelectedCandidateOptions] = useState<string[]>([]);
  const [candidateCreating, setCandidateCreating] = useState(false);
  const [selectedGroup, setSelectedGroup] = useState<LineageGroupSelection | null>(null);
  const [hoveredHeatPoint, setHoveredHeatPoint] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const activeProjectRef = useRef(projectId);
  const activeEntityRef = useRef(entityKey);
  const loadedIndexScopeRef = useRef<string | null>(null);
  const loadedReviewProjectRef = useRef<string | null>(null);
  const outputLabel = (raw: string) => {
    const definition = outputs.find((output) => raw === output.key || raw.startsWith(`${output.key}[`) || (output.key === "lambda" && raw.startsWith("λ")));
    return definition ? `${definition.label}${definition.unit ? ` (${definition.unit})` : ""}` : raw;
  };
  activeProjectRef.current = projectId;
  activeEntityRef.current = entityKey;
  const candidateOptions = data?.candidate_options ?? [];
  const candidateOptionKey = (option: (typeof candidateOptions)[number]) => `${option.process_role}\u001f${option.process_key}\u001f${option.melt_key}`;
  const candidateOptionsIdentity = candidateOptions.map(candidateOptionKey).join("\u001e");
  useEffect(() => {
    setEntityKey(initialEntityKey ?? "");
    setGraphLimit(40);
  }, [projectId, initialEntityKey]);
  useEffect(() => {
    setShowAllReachable(false);
  }, [projectId]);
  useEffect(() => {
    setSelectedGroup(null);
    setSelectedCandidateOptions([]);
    setCandidateAddedCount(0);
    setCandidateCreating(false);
    setCandidateError("");
  }, [entityKey]);
  useEffect(() => {
    setSelectedCandidateOptions(candidateOptions.map(candidateOptionKey));
  }, [entityKey, candidateOptionsIdentity]);
  useEffect(() => {
    setQuery("");
    setEntityType("");
    setIssueFilter("all");
    setIndex(null);
    setData(null);
    setError("");
    setCandidateError("");
    setCandidateAddedCount(0);
    setCandidateCreating(false);
    setSelectedCandidateOptions([]);
    setReviews([]);
    setReviewLedgerOpen(false);
    setReviewStatus("noted");
    setReviewNote("");
    setReviewSaveState("idle");
  }, [projectId]);
  useEffect(() => {
    let cancelled = false;
    const scope = projectId;
    const retainsCurrentEvidence = loadedReviewProjectRef.current === scope;
    if (!retainsCurrentEvidence) setReviews([]);
    setReviewResourceState((current) => beginLineageResourceLoad(current, scope));
    workbenchApi.lineageReviews(projectId)
      .then((payload) => {
        if (cancelled) return;
        loadedReviewProjectRef.current = scope;
        setReviews(payload.items);
        setReviewResourceState(resolveLineageResourceLoad(scope, payload.items.length === 0));
      })
      .catch((cause) => {
        if (cancelled) return;
        setReviewResourceState((current) => rejectLineageResourceLoad(
          current,
          scope,
          "確認メモを取得できませんでした。",
          lineageResourceUnavailable(cause),
        ));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, reviewRevision]);
  useEffect(() => {
    const controller = new AbortController();
    const scope = indexScope;
    const retainsCurrentEvidence = loadedIndexScopeRef.current === scope;
    if (!retainsCurrentEvidence) setIndex(null);
    setIndexResourceState((current) => beginLineageResourceLoad(current, scope));
    const timer = window.setTimeout(() => {
      workbenchApi.lineageIndex(projectId, query.trim(), entityType, issueFilter, controller.signal)
        .then((payload) => {
          if (controller.signal.aborted) return;
          loadedIndexScopeRef.current = scope;
          setIndex(payload);
          setIndexResourceState(resolveLineageResourceLoad(scope, payload.items.length === 0));
        })
        .catch((cause) => {
          if (controller.signal.aborted) return;
          setIndexResourceState((current) => rejectLineageResourceLoad(
            current,
            scope,
            "実績・工程の検索結果を取得できませんでした。",
            lineageResourceUnavailable(cause),
          ));
        });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [projectId, query, entityType, issueFilter, indexRevision, indexScope]);
  useEffect(() => {
    if (!entityKey) {
      setData(null);
      setError("");
      return;
    }
    const controller = new AbortController();
    setData(null);
    setError("");
    setCandidateError("");
    workbenchApi.lineage(projectId, entityKey, graphLimit, showAllReachable, controller.signal)
      .then((lineage) => {
        if (!controller.signal.aborted) {
          setData(lineage);
          setReviewStatus(lineage.review?.status ?? "noted");
          setReviewNote(lineage.review?.note ?? "");
          setReviewSaveState("idle");
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "系譜を取得できませんでした。",
          );
      });
    return () => {
      controller.abort();
    };
  }, [projectId, entityKey, graphLimit, showAllReachable]);
  const saveReview = async () => {
    if (!data) return;
    setReviewSaveState("saving");
    try {
      const review = await workbenchApi.saveLineageReview(projectId, data.key, {
        entity_type: data.node.entity_type,
        status: reviewStatus,
        note: reviewNote.trim(),
      });
      setData({ ...data, review });
      setReviewRevision((value) => value + 1);
      setIndexRevision((value) => value + 1);
      setReviewSaveState("saved");
    } catch {
      setReviewSaveState("error");
    }
  };
  const clearReview = async () => {
    if (!data?.review) return;
    setReviewSaveState("saving");
    try {
      await workbenchApi.deleteLineageReview(projectId, data.key);
      setData({ ...data, review: null });
      setReviewStatus("noted");
      setReviewNote("");
      setReviewRevision((value) => value + 1);
      setIndexRevision((value) => value + 1);
      setReviewSaveState("idle");
    } catch {
      setReviewSaveState("error");
    }
  };
  const exportReviews = async () => {
    try {
      const csv = await workbenchApi.lineageReviewsCsv(projectId);
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "lineage-node-reviews.csv";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch {
      setReviewSaveState("error");
    }
  };
  const createCandidates = async () => {
    const requestProjectId = projectId;
    const requestEntityKey = entityKey;
    const selected = candidateOptions.filter((option) => selectedCandidateOptions.includes(candidateOptionKey(option)));
    setCandidateCreating(true);
    setCandidateError("");
    setCandidateAddedCount(0);
    const createdKeys: string[] = [];
    const errors: string[] = [];
    for (const option of selected) {
      try {
        const created = fromApiCandidate(await workbenchApi.createCandidateFromLineage(
          requestEntityKey,
          requestProjectId,
          option.process_key,
          option.melt_key,
        ));
        if (activeProjectRef.current !== requestProjectId || activeEntityRef.current !== requestEntityKey) break;
        onCandidate(created);
        createdKeys.push(candidateOptionKey(option));
      } catch (cause) {
        if (activeProjectRef.current !== requestProjectId || activeEntityRef.current !== requestEntityKey) break;
        errors.push(cause instanceof Error ? cause.message : "候補を作成できませんでした。");
      }
    }
    if (activeProjectRef.current === requestProjectId && activeEntityRef.current === requestEntityKey) {
      setSelectedCandidateOptions((current) => current.filter((key) => !createdKeys.includes(key)));
      setCandidateAddedCount(createdKeys.length);
      if (errors.length) setCandidateError(`${createdKeys.length}件を追加、${errors.length}件は追加できませんでした。${errors[0]}`);
      setCandidateCreating(false);
    }
  };
  const issueLabels: Record<string, string> = {
    missing_key: "キー欠損",
    orphan_entity: "孤立",
    duplicate_key: "重複",
    invalid_reference: "参照切れ",
    out_of_range: "範囲外",
    suspicious_distribution: "分布の偏り",
    curation_quarantine: "学習利用から隔離",
    missing_target: "目的変数の欠損",
  };
  const openNode = (key: string) => {
    setGraphLimit(40);
    setSelectedGroup(null);
    setEntityKey(key);
    onEntityChange(key);
  };
  const heat = data?.node.heat_pattern ?? [];
  const maxTime = Math.max(1, ...heat.map((point) => point.time_s));
  const maxTemp = Math.max(
    1,
    ...heat.flatMap((point) => [point.temperature_c, point.set_temperature_c ?? point.temperature_c]),
  );
  const heatX = (time: number) => HEAT_PLOT_LEFT + normalizedTimePosition(time, maxTime) * HEAT_PLOT_WIDTH;
  const heatY = (temperature: number) => 120 - (temperature / maxTemp) * 100;
  const heatTimeTicks = [0, maxTime / 2, maxTime];
  const heatTemperatureTicks = [0, maxTemp / 2, maxTemp];
  const heatPoints = heat
    .map((point) => `${heatX(point.time_s)},${heatY(point.temperature_c)}`)
    .join(" ");
  const setHeatPoints = heat
    .filter((point) => typeof point.set_temperature_c === "number")
    .map(
      (point) =>
        `${heatX(point.time_s)},${heatY(point.set_temperature_c ?? 0)}`,
    )
    .join(" ");
  const heatStageTrack = heatStageSegments(heat);
  const selectedGroupNodes = new Map(
    (data?.graph.nodes ?? [])
      .filter((node) => selectedGroup?.nodeKeys.includes(node.key))
      .map((node) => [node.key, node]),
  );
  const selectedGroupObservationsById = new Map(
    (data?.node.observation_groups ?? [])
      .flatMap((group) => group.observations)
      .filter((observation) => {
        const graphNode = selectedGroupNodes.get(observation.id);
        return observation.parent_key === selectedGroup?.parentKey
          && graphNode?.source_sheet === observation.source;
      })
      .map((observation) => [observation.id, observation] as const),
  );
  const selectedGroupRows = (selectedGroup?.nodeKeys ?? []).map((nodeKey) => ({
    nodeKey,
    observation: selectedGroupObservationsById.get(nodeKey),
  }));
  const selectedGroupProperties = Array.from(new Set(
    selectedGroupRows.flatMap(({ observation }) => Object.keys(observation?.outputs ?? {})),
  ));
  const currentIndexState = indexResourceState.scope === indexScope
    ? indexResourceState
    : initialLineageResourceState(indexScope);
  const currentIndex = loadedIndexScopeRef.current === indexScope ? index : null;
  const currentReviewState = reviewResourceState.scope === projectId
    ? reviewResourceState
    : initialLineageResourceState(projectId);
  const currentReviews = loadedReviewProjectRef.current === projectId ? reviews : [];
  return (
    <div className="page-panel lineage-page">
      {qualityIssueId && (
        <div className="investigation-context" role="status">
          <span>データ品質の検出結果から調査中</span>
          <button type="button" className="text-button" onClick={onReturnToQuality}>品質一覧へ戻る</button>
        </div>
      )}
      <div className="page-intro">
        <div>
          <span className="overline">データ探索</span>
          <p>
            この材料・条件は、どの工程と試験結果につながっているか。
          </p>
        </div>
      </div>
      <div className={`lineage-workspace${data ? "" : " no-detail"}`}>
        <aside className="lineage-browser" aria-label="系譜ノード検索">
          {currentIndex && (
            <div className="lineage-source-facts">
              <span><b>{number(currentIndex.total_entities)}</b> エンティティ</span>
              <span><b>{number(currentIndex.relation_rows)}</b> 関係レコード</span>
              <span className={currentIndex.detected_issues ? "has-issue" : ""}><b>{currentIndex.detected_issues}</b> 検出問題</span>
            </div>
          )}
          <label htmlFor="lineage-query">ノードを検索</label>
          <input
            id="lineage-query"
            className="lineage-filter-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="キー・鋼種・PJ・route"
          />
          <label>
            種別
            <select value={entityType} onChange={(event) => setEntityType(event.target.value)}>
              <option value="">すべて</option>
              {Object.keys(currentIndex?.counts_by_type ?? {}).map((type) => (
                <option key={type} value={type}>{type} ({currentIndex?.counts_by_type[type]})</option>
              ))}
            </select>
          </label>
          <label>
            問題
            <select
              value={issueFilter}
              onChange={(event) => setIssueFilter(event.target.value as typeof issueFilter)}
            >
              <option value="all">すべて</option>
              <option value="with_issues">問題あり</option>
              <option value="without_issues">問題なし</option>
            </select>
          </label>
          <button
            type="button"
            className="outline-button"
            disabled={currentIndexState.phase === "loading"}
            onClick={() => setIndexRevision((value) => value + 1)}
          >
            {currentIndexState.phase === "loading"
              ? "検索結果を読込中…"
              : currentIndexState.phase === "stale"
                || currentIndexState.phase === "error"
                || currentIndexState.phase === "unavailable"
                ? "検索結果を再試行"
                : "検索結果を更新"}
          </button>
          {currentIndexState.phase === "loading" && (
            <p className="empty-evidence" role="status">
              {currentIndex && currentIndexState.loadedAt
                ? `検索結果を更新しています。表示中の結果は、この画面で ${clientLoadedAt(currentIndexState.loadedAt)} に取得した内容です。`
                : "検索結果を読み込んでいます。"}
            </p>
          )}
          {currentIndexState.phase === "stale" && (
            <div className="data-library-resource-error" role="alert">
              <div>
                <strong>検索結果を更新できませんでした</strong>
                <p>表示中の結果は保持しています。最新の検索結果として扱わないでください。</p>
                <small>この画面での取得時刻: {clientLoadedAt(currentIndexState.loadedAt)}</small>
              </div>
            </div>
          )}
          {(currentIndexState.phase === "error" || currentIndexState.phase === "unavailable") && (
            <div className="data-library-resource-error" role="alert">
              <div>
                <strong>{currentIndexState.phase === "unavailable"
                  ? "実績・工程の検索は現在利用できません"
                  : "実績・工程の検索結果を取得できませんでした"}</strong>
                <p>検索結果は未確認です。一致する実績が0件という意味ではありません。</p>
                <small>「検索結果を再試行」は、この検索条件だけを読み直します。</small>
              </div>
            </div>
          )}
          <div className="lineage-review-ledger-tools">
            <button
              type="button"
              className={reviewLedgerOpen ? "active" : ""}
              onClick={() => setReviewLedgerOpen((open) => !open)}
            >
              確認メモ {currentReviewState.loadedAt ? `${currentReviews.length}件` : "未取得"}
            </button>
            <button type="button" disabled={!currentReviews.length} onClick={() => void exportReviews()}>CSV</button>
            <button
              type="button"
              disabled={currentReviewState.phase === "loading"}
              onClick={() => setReviewRevision((value) => value + 1)}
            >
              {currentReviewState.phase === "loading"
                ? "メモを読込中…"
                : currentReviewState.phase === "stale"
                  || currentReviewState.phase === "error"
                  || currentReviewState.phase === "unavailable"
                  ? "メモを再試行"
                  : "メモを更新"}
            </button>
          </div>
          {currentReviewState.phase === "loading" && (
            <p className="empty-evidence" role="status">
              {currentReviews.length || currentReviewState.loadedAt
                ? `確認メモを更新しています。表示中の内容は、この画面で ${clientLoadedAt(currentReviewState.loadedAt)} に取得したものです。`
                : "確認メモを読み込んでいます。"}
            </p>
          )}
          {currentReviewState.phase === "stale" && (
            <div className="data-library-resource-error" role="alert">
              <div>
                <strong>確認メモを更新できませんでした</strong>
                <p>表示中のメモは保持しています。最新の台帳として扱わないでください。</p>
                <small>この画面での取得時刻: {clientLoadedAt(currentReviewState.loadedAt)}</small>
              </div>
            </div>
          )}
          {(currentReviewState.phase === "error" || currentReviewState.phase === "unavailable") && (
            <div className="data-library-resource-error" role="alert">
              <div>
                <strong>{currentReviewState.phase === "unavailable"
                  ? "確認メモは現在利用できません"
                  : "確認メモを取得できませんでした"}</strong>
                <p>メモ件数は未確認です。0件という意味ではありません。</p>
                <small>「メモを再試行」は、確認メモだけを読み直します。</small>
              </div>
            </div>
          )}
          {reviewLedgerOpen ? (
            <>
              <div className="lineage-result-list lineage-review-list">
                {currentReviews.map((review) => (
                  <button
                    key={review.entity_key}
                    type="button"
                    className={review.entity_key === entityKey ? "active" : ""}
                    onClick={() => openNode(review.entity_key)}
                  >
                    <span className="lineage-result-title">
                      <b>{review.entity_key}</b>
                      <small className={`review-${review.status}`}>{reviewLabels[review.status]}</small>
                    </span>
                    <span className="lineage-result-meta">{review.entity_type}</span>
                    {review.note && <span className="lineage-review-note">{review.note}</span>}
                  </button>
                ))}
                {!currentReviews.length && currentReviewState.phase === "empty" && (
                  <p className="empty-evidence">確認メモはまだありません。</p>
                )}
                {!currentReviews.length && currentReviewState.phase === "stale" && (
                  <p className="empty-evidence">前回取得時点では確認メモはありませんでした。</p>
                )}
              </div>
              <small className="lineage-result-limit">非表示にしたキーもここから開けます。</small>
            </>
          ) : (
            <>
              <div className="lineage-result-list">
                {(currentIndex?.items ?? []).map((item) => (
                  <button
                    key={`${item.entity_type}-${item.key}`}
                    type="button"
                    className={item.key === entityKey ? "active" : ""}
                    onClick={() => openNode(item.key)}
                  >
                    <span className="lineage-result-title"><b>{item.key}</b><small>{item.entity_type}{item.has_issue ? " · 要確認" : ""}</small></span>
                    {item.review_status && (
                      <span className={`lineage-review-badge review-${item.review_status}`}>
                        {reviewLabels[item.review_status]}
                      </span>
                    )}
                    {item.entity_type === "焼鈍" && (
                      <>
                        <span className="lineage-result-meta">
                          {(item.melt_keys?.length ?? 0) > 1
                            ? `${item.melt_keys!.length}成分共有 (${item.melt_keys!.join(" / ")})`
                            : item.family || "成分未特定"}
                          {" · "}{item.project || "PJ不明"} · {item.route || "route不明"}
                        </span>
                        <span className="lineage-result-meta">peak {item.peak_temperature_c == null ? "—" : `${number(item.peak_temperature_c)}°C`} · {item.learning_status || "区分なし"}</span>
                        <span className="lineage-result-observations">
                          {Object.entries(item.observation_summary ?? {}).slice(0, 4).map(([property, summary]) => `${property.replace("[MPa]", "").replace("[%]", "")} ${number(summary.mean, 1)}±${number(summary.std, 1)} (n=${summary.n})`).join(" / ") || "焼鈍後観測なし"}
                        </span>
                      </>
                    )}
                  </button>
                ))}
                {currentIndex && !currentIndex.items.length && currentIndexState.phase === "empty" && (
                  <p className="empty-evidence">一致するキーはありません。</p>
                )}
                {currentIndex && !currentIndex.items.length && currentIndexState.phase === "stale" && (
                  <p className="empty-evidence">前回取得時点では一致するキーはありませんでした。</p>
                )}
              </div>
              <small className="lineage-result-limit">
                {currentIndex
                  ? `${number(currentIndex.matched_entities ?? currentIndex.items.length)}件中${number(currentIndex.items.length)}件を表示`
                  : currentIndexState.phase === "loading"
                    ? "検索中"
                    : "検索結果は未取得"}
                {" · "}一度に最大200件まで表示。選択するとグラフを開きます。
              </small>
            </>
          )}
        </aside>
      {error ? (
        <main className="lineage-main">
          <div className="lineage-load-error">
          <b>{entityKey}</b>
          <p>{error}</p>
          <span>左の検索結果から存在するキーを選んでください。</span>
          </div>
        </main>
      ) : data ? (
        <>
        <main className="lineage-main">
          <LineageGraph
            graph={data.graph}
            selectedKey={data.key}
            onSelect={openNode}
            onGroupSelect={setSelectedGroup}
            onLoadMore={() => setGraphLimit((current) => Math.min(200, current + 40))}
            showAllReachable={showAllReachable}
            onShowAllReachableChange={setShowAllReachable}
          />
            {data.graph.edges.length > 0 && (
              <details className="route-evidence">
                <summary>経路の接続根拠 {data.graph.edges.length}本</summary>
                <div>
                  {data.graph.edges.map((edge) => (
                    <p key={`${edge.source}-${edge.target}`}>
                      <button type="button" onClick={() => openNode(edge.source)}>{edge.source}</button>
                      <span>→</span>
                      <button type="button" onClick={() => openNode(edge.target)}>{edge.target}</button>
                      <small>relation {edge.route_rows.slice(0, 5).join(", ")}{edge.route_rows.length > 5 ? ` +${edge.route_rows.length - 5}` : ""}</small>
                    </p>
                  ))}
                </div>
              </details>
            )}
        </main>
        <aside className="lineage-review-panel" aria-label="このノードの確認メモ">
          <div className="lineage-review-panel-label">確認メモ</div>
          <div className="lineage-review-target">
            <span>確認対象</span>
            <strong>{data.key}</strong>
            <small>{data.node.entity_type}</small>
          </div>
          <section className="lineage-review-editor">
            <label>
              対応
              <select
                value={reviewStatus}
                onChange={(event) => {
                  setReviewStatus(event.target.value as ReviewStatus);
                  setReviewSaveState("idle");
                }}
              >
                {Object.entries(reviewLabels).map(([status, label]) => (
                  <option key={status} value={status}>{label}</option>
                ))}
              </select>
            </label>
            <label>
              メモ
              <textarea
                maxLength={1000}
                rows={5}
                value={reviewNote}
                placeholder="このままでよい理由、修正内容、後で確認する観点"
                onChange={(event) => {
                  setReviewNote(event.target.value);
                  setReviewSaveState("idle");
                }}
              />
            </label>
            <div>
              {data.review && <button type="button" className="text-button" disabled={reviewSaveState === "saving"} onClick={() => void clearReview()}>記録を削除</button>}
              <button type="button" className="outline-button" disabled={reviewSaveState === "saving"} onClick={() => void saveReview()}>
                {reviewSaveState === "saving" ? "保存中" : "保存"}
              </button>
              <small className={reviewSaveState === "error" ? "error" : ""}>
                {reviewSaveState === "saved" ? "保存しました" : reviewSaveState === "error" ? "保存できませんでした" : data.review ? `更新 ${new Date(data.review.updated_at).toLocaleString("ja-JP")}` : ""}
              </small>
            </div>
          </section>
        </aside>
        <aside className="lineage-detail-panel" aria-label="選択ノード詳細">
          <section className="lineage-node-summary" aria-label="ノード情報">
            <div className="lineage-node-summary-label">ノード情報</div>
            <div className="lineage-detail-header">
              <div>
                <span className="overline">
                  {data.node.source_sheet} / {data.node.entity_type}
                </span>
                <h3>{data.key}</h3>
                <p>
                  {Object.values(data.relations).reduce(
                    (sum, values) => sum + values.length,
                    0,
                  )}
                  件の関係、{data.node.connected_observation_count}件の接続観測
                </p>
              </div>
              <div className="lineage-detail-action">
                {supportsCandidateCreation && candidateOptions.length > 1 && (
                  <div className="lineage-candidate-options">
                    <div>
                      <b>候補にする組合せ</b>
                      <button type="button" className="text-button" onClick={() => setSelectedCandidateOptions(
                        selectedCandidateOptions.length === candidateOptions.length ? [] : candidateOptions.map(candidateOptionKey),
                      )}>
                        {selectedCandidateOptions.length === candidateOptions.length ? "すべて外す" : "すべて選ぶ"}
                      </button>
                    </div>
                    <div className="lineage-candidate-option-list">
                      {candidateOptions.map((option) => {
                        const key = candidateOptionKey(option);
                        return (
                          <label key={key}>
                            <input
                              type="checkbox"
                              checked={selectedCandidateOptions.includes(key)}
                              onChange={(event) => setSelectedCandidateOptions((current) => (
                                event.target.checked ? [...current, key] : current.filter((item) => item !== key)
                              ))}
                            />
                            <span><b>{option.process_key}</b><small>{option.process_label} / 成分 {option.melt_key}</small></span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                )}
                <CandidateAddButton
                  disabled={!supportsCandidateCreation || !data.candidate_eligible || !selectedCandidateOptions.length || candidateCreating}
                  title={supportsCandidateCreation ? data.candidate_reason : "この予測タスクは系譜からの候補化に対応していません"}
                  onClick={() => {
                    void createCandidates();
                  }}
                >
                  {candidateCreating ? "追加中…" : candidateOptions.length > 1 ? `${selectedCandidateOptions.length}件を候補へ追加` : "候補ストックへ追加"}
                </CandidateAddButton>
                <span className={`lineage-detail-action-reason ${supportsCandidateCreation && data.candidate_eligible ? "" : "muted"}`}>
                  {supportsCandidateCreation ? data.candidate_reason : "この予測タスクは系譜からの候補化に対応していません。"}
                </span>
                {candidateAddedCount > 0 && <span className="lineage-candidate-added" role="status">
                  候補ストックに{candidateAddedCount}件追加しました。画面上部の「候補を比較」から確認できます。
                </span>}
                {candidateError && <span className="warning">{candidateError}</span>}
              </div>
            </div>
            <section className="lineage-node-facts">
              <h3>主要条件</h3>
              <div className="lineage-node-facts-scroll">
                <table>
                  <tbody>
                    <tr>
                      {Object.keys(data.node.primary_conditions).map((key) => {
                        const presentation = primaryConditionPresentation(key);
                        return <th scope="col" key={key}>
                          <span>{presentation.label}</span>
                          {presentation.unit && <small>{presentation.unit}</small>}
                          <code title="元データの列名">{presentation.sourceColumn}</code>
                        </th>;
                      })}
                    </tr>
                    <tr>
                      {Object.entries(data.node.primary_conditions).map(([key, value]) => <td key={key}>{value === null ? "—" : String(value)}</td>)}
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
            {data.node.evidence_image && (
              <section className="lineage-node-evidence-image" aria-label="観察画像">
                <h3>観察画像</h3>
                {data.node.evidence_image.available ? (
                  <img
                    src={workbenchApi.lineageEvidenceImageUrl(projectId, data.key)}
                    alt={`${data.key} の観察画像`}
                    loading="lazy"
                  />
                ) : (
                  // 参照はあるが実体が無い。観測が存在しなかったことにはしない。
                  <p className="empty-evidence">
                    画像ファイルを読み込めません。{data.node.evidence_image.reason ?? ""}
                  </p>
                )}
                <small>
                  <code title="元データの参照先">{data.node.evidence_image.declared_path}</code>
                </small>
              </section>
            )}
          </section>
          {selectedGroup && (
            <section className="lineage-group-facts">
              <div className="lineage-group-facts-header">
                <div>
                  <h3>{selectedGroup.entityType} {selectedGroup.nodeKeys.length}件</h3>
                  <p>{selectedGroup.parentKey} に接続された試験をまとめて表示</p>
                </div>
                <button type="button" className="text-button" onClick={() => setSelectedGroup(null)}>グループ選択を解除</button>
              </div>
              {selectedGroupObservationsById.size ? (
                <div className="lineage-group-facts-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">試験キー</th>
                        {selectedGroupProperties.map((property) => <th scope="col" key={property}>{outputLabel(property)}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {selectedGroupRows.map(({ nodeKey, observation }) => (
                        <tr key={nodeKey}>
                          <th scope="row">{nodeKey}</th>
                          {selectedGroupProperties.map((property) => {
                            const value = observation?.outputs[property];
                            return <td key={property}>{value == null ? "—" : typeof value === "number" ? number(value, Math.abs(value) < 1 ? 3 : 1) : String(value)}</td>;
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty-evidence">このグループの実績値はありません。</p>
              )}
            </section>
          )}
          <section className="lineage-neighbor-facts">
            <h3>
              上流組成 <small>mass%</small>
            </h3>
            <div className="lineage-node-facts-scroll">
              <table>
                <tbody>
                  <tr>
                    {Object.keys(data.node.composition).map((key) => <th scope="col" key={key}>{key}</th>)}
                  </tr>
                  <tr>
                    {Object.entries(data.node.composition).map(([key, value]) => <td key={key}>{number(value, value < 0.01 ? 5 : 3)}</td>)}
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
          <div className="lineage-detail-grid">
            <section>
              <h3>
                実績ヒートパターン <small>{heat.length}点</small>
              </h3>
              {heat.length ? (
                <>
                <svg
                  viewBox={`0 0 ${HEAT_CHART_WIDTH} 135`}
                  className="lineage-heat"
                  role="group"
                  aria-label="実績ヒートパターン"
                >
                  {heatTemperatureTicks.map((tick) => <g key={`temp-${tick}`} className="lineage-heat-grid"><line x1={HEAT_PLOT_LEFT} x2={HEAT_CHART_WIDTH - HEAT_PLOT_RIGHT} y1={heatY(tick)} y2={heatY(tick)} /><text x={HEAT_PLOT_LEFT - 3} y={heatY(tick) + 3} textAnchor="end">{number(tick)}</text></g>)}
                  {heatTimeTicks.map((tick) => <g key={`time-${tick}`} className="lineage-heat-grid"><line x1={heatX(tick)} x2={heatX(tick)} y1="20" y2="120" /><text x={heatX(tick)} y="132" textAnchor="middle">{number(tick)}</text></g>)}
                  <line x1={HEAT_PLOT_LEFT} x2={HEAT_CHART_WIDTH - HEAT_PLOT_RIGHT} y1="120" y2="120" />
                  <polyline
                    points={heatPoints}
                    fill="none"
                    stroke="#1f5fc4"
                    strokeWidth="3"
                  />
                  {setHeatPoints && (
                    <polyline
                      points={setHeatPoints}
                      fill="none"
                      stroke="#c17816"
                      strokeWidth="1.5"
                      strokeDasharray="5 4"
                    />
                  )}
                  {heat.map((point) => (
                    <circle
                      key={point.time_s}
                      className="svg-chart-hit-target"
                      role="img"
                      tabIndex={0}
                      aria-label={[point.stage_category, point.stage_name, `${point.time_s}s / ${point.temperature_c}°C`].filter(Boolean).join(" · ")}
                      cx={heatX(point.time_s)}
                      cy={heatY(point.temperature_c)}
                      r="3"
                      fill="#1f5fc4"
                      onMouseEnter={() => setHoveredHeatPoint({ x: heatX(point.time_s), y: heatY(point.temperature_c), lines: [point.stage_name || point.stage_category || "実績温度", `時間 ${number(point.time_s, 1)} s`, `温度 ${number(point.temperature_c, 1)} °C`, ...(point.set_temperature_c == null ? [] : [`設定 ${number(point.set_temperature_c, 1)} °C`])] })}
                      onMouseLeave={() => setHoveredHeatPoint(null)}
                      onFocus={() => setHoveredHeatPoint({ x: heatX(point.time_s), y: heatY(point.temperature_c), lines: [point.stage_name || point.stage_category || "実績温度", `時間 ${number(point.time_s, 1)} s`, `温度 ${number(point.temperature_c, 1)} °C`, ...(point.set_temperature_c == null ? [] : [`設定 ${number(point.set_temperature_c, 1)} °C`])] })}
                      onBlur={() => setHoveredHeatPoint(null)}
                    >
                    </circle>
                  ))}
                  {hoveredHeatPoint && <SvgChartTooltip {...hoveredHeatPoint} chartWidth={HEAT_CHART_WIDTH} chartHeight={135} />}
                </svg>
                <div
                  className="lineage-process-track"
                  aria-label="ヒートパターンと同じ時間軸の工程区間"
                  style={{ marginInline: `${(HEAT_PLOT_LEFT / HEAT_CHART_WIDTH) * 100}%` }}
                >
                  {heatStageTrack.map((stage, index) => (
                    <div
                      className={`lineage-process-segment stage-${stage.category.toLowerCase().replaceAll("_", "-")}`}
                      key={`${stage.category}-${stage.name}-${index}`}
                      style={{
                        left: `${normalizedTimePosition(stage.startTime, maxTime) * 100}%`,
                        width: `${(normalizedTimePosition(stage.endTime, maxTime) - normalizedTimePosition(stage.startTime, maxTime)) * 100}%`,
                      }}
                      title={`${stage.category} / ${stage.name} · ${number(stage.startTime, 1)}–${number(stage.endTime, 1)} s${stage.status ? ` · ${stage.status}` : ""}`}
                      aria-label={`${stage.name}、${number(stage.startTime, 1)}秒から${number(stage.endTime, 1)}秒${stage.status ? `、${stage.status}` : ""}`}
                      tabIndex={0}
                    >
                      {(stage.endTime - stage.startTime) / maxTime >= 0.1 && <b>{stage.name}</b>}
                    </div>
                  ))}
                </div>
                <div className="lineage-heat-legend">
                  <span><i className="actual" />実績温度</span>
                  <span><i className="setting" />設定温度</span>
                </div>
                <details className="lineage-stage-details">
                  <summary>工程区間 {heatStageTrack.length}段階</summary>
                  <ol>
                    {heatStageTrack.map((stage, index) => (
                      <li key={`detail-${stage.category}-${stage.name}-${index}`}>
                        <b>{stage.name}</b>
                        <span>{number(stage.startTime, 1)}–{number(stage.endTime, 1)} s</span>
                        {stage.status && <small>{stage.status}</small>}
                      </li>
                    ))}
                  </ol>
                </details>
                </>
              ) : (
                <p className="empty-evidence">
                  このノードに焼鈍履歴は接続されていません。
                </p>
              )}
            </section>
            <section>
              <h3>工程段階別の特性分布</h3>
              {(data.node.observation_groups ?? []).length ? (
                <>
                  <div className="lineage-observation-scroll">
                    <table className="quality-table compact-table">
                    <thead>
                      <tr>
                        <th>段階 / 試験</th>
                        <th>特性</th>
                        <th>n</th>
                        <th>min</th>
                        <th>mean ± SD</th>
                        <th>median</th>
                        <th>max</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(data.node.observation_groups ?? []).map(
                        (group) => {
                          const assessment = assessOutputValues(resolveOutputDefinition(outputs, group.property), [group.min, group.mean, group.median, group.max], "実測値");
                          return <tr key={`${group.test_type}-${group.property}`} className={assessment.implausible ? "plausibility-warning-row" : undefined}>
                            <td><b>{group.stage}</b><br /><small>{group.test_type}</small></td>
                            <td>{outputLabel(group.property)}{assessment.implausible ? <span className="plausibility-warning" title={assessment.warning ?? undefined}>⚠ 物理範囲外</span> : null}</td>
                            <td>{group.count}</td>
                            {group.count === 1 ? (
                              <td colSpan={4} className="lineage-single-observation">
                                <strong>{number(group.mean, 1)}</strong>
                                <small>単一の実測値</small>
                              </td>
                            ) : (
                              <>
                                <td>{number(group.min, 1)}</td>
                                <td>
                                  {number(group.mean, 1)} ±{" "}
                                  {number(group.std, 1)}
                                </td>
                                <td>{number(group.median, 1)}</td>
                                <td>{number(group.max, 1)}</td>
                              </>
                            )}
                          </tr>;
                        },
                      )}
                    </tbody>
                    </table>
                  </div>
                  <details className="similar-more">
                    <summary>観測値を表示</summary>
                    {(data.node.connected_observations ?? []).map((observation) => (
                      <p key={observation.id}>
                        {observation.id} · {observation.source} ·{" "}
                        {Object.entries(observation.outputs)
                          .map(([key, value]) => { const assessment = assessOutputValues(resolveOutputDefinition(outputs, key), [value], "実測値"); return <span key={key} title={assessment.warning ?? undefined} className={assessment.implausible ? "plausibility-value" : undefined}>{outputLabel(key)} {number(value, 1)}{assessment.implausible ? <><small>⚠ 物理範囲外</small><em className="plausibility-reason">{assessment.warning}</em></> : null}</span>; }) }
                      </p>
                    ))}
                  </details>
                </>
              ) : (
                <p className="empty-evidence">接続観測はありません。</p>
              )}
            </section>
          </div>
          {data.quality_issues.map((issue) => (
            <p
              className="warning"
              key={issue.issue_id}
            >
              <b>{issueLabels[issue.issue_type] ?? issue.issue_type}</b> · {issue.source_sheet} · {issue.entity_key || "キーなし"}: {issue.detail}
            </p>
          ))}
        </aside>
        </>
      ) : (
        <main className="lineage-main">
          <section className="lineage-empty-overview">
            <span className="overline">ノード未選択</span>
            <h3>調べるノードを選択してください</h3>
            <p>左の検索欄からノードを選ぶと、実在する関係線と前後工程を表示します。</p>
            {currentIndex && <p>{number(currentIndex.total_entities)}ノード / {number(currentIndex.relation_rows)}関係 / {currentIndex.detected_issues}件の品質問題</p>}
          </section>
        </main>
      )}
      </div>
    </div>
  );
}
