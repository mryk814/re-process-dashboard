import { useEffect, useRef, useState } from "react";
import { fromApiCandidate, type CandidateViewModel as Candidate, type TaskOutputDefinition } from "../candidates";
import { workbenchApi, type ApiLineage, type ApiLineageIndex } from "../../shared/api/workbench-api";
import { LineageGraph } from "./LineageGraph";

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

type HeatStageSegment = {
  category: string;
  name: string;
  status: string;
  duration: number;
};

function heatStageSegments(heat: ApiLineage["node"]["heat_pattern"]): HeatStageSegment[] {
  const segments: HeatStageSegment[] = [];
  heat.forEach((point, index) => {
    const category = point.stage_category ?? "工程";
    const name = point.stage_name ?? "工程点";
    const status = point.mapping_status ?? "";
    const previousTime = index === 0 ? 0 : heat[index - 1].time_s;
    const last = segments[segments.length - 1];
    if (last && last.category === category && last.name === name && last.status === status) {
      last.duration += Math.max(0.001, point.time_s - previousTime);
    } else {
      segments.push({
        category,
        name,
        status,
        duration: Math.max(0.001, point.time_s - previousTime),
      });
    }
  });
  return segments;
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
  const [directKey, setDirectKey] = useState("");
  const [entityType, setEntityType] = useState("焼鈍");
  const [issueOnly, setIssueOnly] = useState(false);
  const [graphLimit, setGraphLimit] = useState(40);
  const [index, setIndex] = useState<ApiLineageIndex | null>(null);
  const [data, setData] = useState<ApiLineage | null>(null);
  const [error, setError] = useState("");
  const [candidateError, setCandidateError] = useState("");
  const activeProjectRef = useRef(projectId);
  const outputLabel = (raw: string) => {
    const definition = outputs.find((output) => raw === output.key || raw.startsWith(`${output.key}[`) || (output.key === "lambda" && raw.startsWith("λ")));
    return definition ? `${definition.label}${definition.unit ? ` (${definition.unit})` : ""}` : raw;
  };
  activeProjectRef.current = projectId;
  useEffect(() => {
    setEntityKey(initialEntityKey ?? "");
    setGraphLimit(40);
  }, [projectId, initialEntityKey]);
  useEffect(() => {
    setQuery("");
    setDirectKey("");
    setError("");
    setCandidateError("");
  }, [projectId]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      workbenchApi.lineageIndex(query.trim(), entityType, issueOnly, controller.signal)
        .then(setIndex)
        .catch(() => undefined);
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, entityType, issueOnly]);
  useEffect(() => {
    if (!entityKey) {
      setData(null);
      setError("");
      return;
    }
    let cancelled = false;
    setError("");
    setCandidateError("");
    workbenchApi.lineage(entityKey, graphLimit)
      .then((lineage) => {
        if (!cancelled) {
          setData(lineage);
        }
      })
      .catch((cause) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "系譜を取得できませんでした。",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [entityKey, graphLimit]);
  const createCandidate = async () => {
    const requestProjectId = projectId;
    const requestEntityKey = entityKey;
    try {
      const created = fromApiCandidate(await workbenchApi.createCandidateFromLineage(requestEntityKey, requestProjectId));
      if (activeProjectRef.current !== requestProjectId) return;
      onCandidate(created);
    } catch (cause) {
      if (activeProjectRef.current !== requestProjectId) return;
      setCandidateError(
        cause instanceof Error ? cause.message : "候補を作成できませんでした。",
      );
    }
  };
  const issueLabels: Record<string, string> = {
    missing_key: "キー欠損",
    orphan_entity: "孤立",
    duplicate_key: "重複",
    invalid_reference: "参照切れ",
  };
  const openNode = (key: string) => {
    setGraphLimit(40);
    setEntityKey(key);
    onEntityChange(key);
  };
  const heat = data?.node.heat_pattern ?? [];
  const maxTime = Math.max(1, ...heat.map((point) => point.time_s));
  const maxTemp = Math.max(
    1,
    ...heat.flatMap((point) => [point.temperature_c, point.set_temperature_c ?? point.temperature_c]),
  );
  const heatPoints = heat
    .map(
      (point) =>
        `${20 + (point.time_s / maxTime) * 380},${120 - (point.temperature_c / maxTemp) * 100}`,
    )
    .join(" ");
  const setHeatPoints = heat
    .filter((point) => typeof point.set_temperature_c === "number")
    .map(
      (point) =>
        `${20 + (point.time_s / maxTime) * 380},${120 - ((point.set_temperature_c ?? 0) / maxTemp) * 100}`,
    )
    .join(" ");
  const heatStages = Array.from(
    new Map(
      heat
        .filter((point) => point.stage_category || point.stage_name)
        .map((point) => [
          `${point.stage_category ?? "工程"}-${point.stage_name ?? ""}`,
          { category: point.stage_category ?? "工程", name: point.stage_name ?? "", status: point.mapping_status ?? "" },
        ]),
    ).values(),
  );
  const heatStageTrack = heatStageSegments(heat);
  return (
    <div className="page-panel lineage-page">
      {qualityIssueId && (
        <div className="investigation-context" role="status">
          <span>データ品質の検出結果から調査中</span>
          <button type="button" className="text-button" onClick={onReturnToQuality}>品質一覧へ戻る</button>
        </div>
      )}
      <div className="page-intro lineage-intro">
        <div>
          <span className="overline">データ探索</span>
          <h2>工程系譜</h2>
          <p>
            この材料・条件は、どの工程と試験結果につながっているか。
          </p>
        </div>
        <form
          className="lineage-direct-open"
          onSubmit={(event) => {
            event.preventDefault();
            if (directKey.trim()) {
              openNode(directKey.trim());
            }
          }}
        >
          <label htmlFor="lineage-direct-key">キーを直接指定</label>
          <input
            id="lineage-direct-key"
            value={directKey}
            onChange={(event) => setDirectKey(event.target.value)}
            placeholder="例: AN-00001"
          />
          <button type="submit" className="secondary-button">開く</button>
        </form>
      </div>
      <div className="lineage-workspace">
        <aside className="lineage-browser" aria-label="系譜ノード検索">
          {index && (
            <div className="lineage-source-facts">
              <span><b>{number(index.total_entities)}</b> エンティティ</span>
              <span><b>{number(index.relation_rows)}</b> relation行</span>
              <span className={index.detected_issues ? "has-issue" : ""}><b>{index.detected_issues}</b> 検出問題</span>
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
              {Object.keys(index?.counts_by_type ?? {}).map((type) => (
                <option key={type} value={type}>{type} ({index?.counts_by_type[type]})</option>
              ))}
            </select>
          </label>
          <label className="lineage-issue-filter">
            <input type="checkbox" checked={issueOnly} onChange={(event) => setIssueOnly(event.target.checked)} />
            問題があるノードだけ
          </label>
          <div className="lineage-result-list">
            {(index?.items ?? []).map((item) => (
              <button
                key={`${item.entity_type}-${item.key}`}
                type="button"
                className={item.key === entityKey ? "active" : ""}
                onClick={() => openNode(item.key)}
              >
                <span className="lineage-result-title"><b>{item.key}</b><small>{item.entity_type}{item.has_issue ? " · 要確認" : ""}</small></span>
                {item.entity_type === "焼鈍" && (
                  <>
                    <span className="lineage-result-meta">{item.family || "family不明"} · {item.project || "PJ不明"} · {item.route || "route不明"}</span>
                    <span className="lineage-result-meta">peak {item.peak_temperature_c == null ? "—" : `${number(item.peak_temperature_c)}°C`} · {item.learning_status || "区分なし"}</span>
                    <span className="lineage-result-observations">
                      {Object.entries(item.observation_summary ?? {}).slice(0, 4).map(([property, summary]) => `${property.replace("[MPa]", "").replace("[%]", "")} ${number(summary.mean, 1)}±${number(summary.std, 1)} (n=${summary.n})`).join(" / ") || "焼鈍後観測なし"}
                    </span>
                  </>
                )}
              </button>
            ))}
            {index && !index.items.length && <p className="empty-evidence">一致するキーはありません。</p>}
          </div>
          <small className="lineage-result-limit">検索結果は最大40件。選択するとグラフを開きます。</small>
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
           onLoadMore={() => setGraphLimit((current) => Math.min(200, current + 40))}
          />
          <div className="lineage-node-action-bar" aria-label="選択ノードの候補化">
            <div className="lineage-node-action-info">
              <span className="overline">選択中のノード</span>
              <strong>{data.key}</strong>
              <small>{data.node.source_sheet} / {data.node.entity_type}</small>
            </div>
            <div className="lineage-node-action-controls">
              <button
                className="primary-button"
                disabled={!supportsCandidateCreation || !data.candidate_eligible}
                title={supportsCandidateCreation ? data.candidate_reason : "この予測タスクは系譜からの候補化に対応していません"}
                onClick={() => {
                  void createCandidate();
                }}
              >
                候補ストックへ追加
              </button>
              <span className={`lineage-node-action-reason ${supportsCandidateCreation && data.candidate_eligible ? "" : "muted"}`}>
                {supportsCandidateCreation ? data.candidate_reason : "この予測タスクは系譜からの候補化に対応していません。"}
              </span>
              {candidateError && <span className="warning">{candidateError}</span>}
            </div>
          </div>
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
        <aside className="lineage-detail-panel" aria-label="選択ノード詳細">
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
          </div>
          <section className="lineage-node-facts">
            <h3>主要条件</h3>
            <div className="lineage-node-facts-scroll">
              <table>
                <tbody>
                  <tr>
                    {Object.keys(data.node.primary_conditions).map((key) => <th scope="col" key={key}>{key}</th>)}
                  </tr>
                  <tr>
                    {Object.entries(data.node.primary_conditions).map(([key, value]) => <td key={key}>{value === null ? "—" : String(value)}</td>)}
                  </tr>
                </tbody>
              </table>
            </div>
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
                  viewBox="0 0 420 135"
                  className="lineage-heat"
                  role="img"
                  aria-label="実績ヒートパターン"
                >
                  <line x1="20" x2="400" y1="120" y2="120" />
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
                      cx={20 + (point.time_s / maxTime) * 380}
                      cy={120 - (point.temperature_c / maxTemp) * 100}
                      r="3"
                      fill="#1f5fc4"
                    >
                      <title>{[point.stage_category, point.stage_name, `${point.time_s}s / ${point.temperature_c}°C`].filter(Boolean).join(" · ")}</title>
                    </circle>
                  ))}
                </svg>
                <div className="lineage-process-track" aria-label="工程区間">
                  {heatStageTrack.map((stage, index) => (
                    <div
                      className={`lineage-process-segment stage-${stage.category.toLowerCase().replaceAll("_", "-")}`}
                      key={`${stage.category}-${stage.name}-${index}`}
                      style={{ flexGrow: stage.duration }}
                      title={`${stage.category} / ${stage.name}${stage.status ? ` · ${stage.status}` : ""}`}
                    >
                      <b>{stage.category}</b>
                      <span>{stage.name}</span>
                    </div>
                  ))}
                </div>
                <div className="lineage-heat-legend">
                  <span><i className="actual" />実績温度</span>
                  <span><i className="setting" />設定温度</span>
                  {heatStages.map((stage) => (
                    <span className={stage.status && stage.status !== "確定" ? "unmapped" : ""} key={`${stage.category}-${stage.name}`}>
                      {stage.category}{stage.name ? ` / ${stage.name}` : ""}{stage.status ? ` · ${stage.status}` : ""}
                    </span>
                  ))}
                </div>
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
                          const warnings = group.observations.flatMap((observation) => (observation.output_warnings ?? {})[group.property] ?? []);
                          return <tr key={`${group.test_type}-${group.property}`} className={warnings.length ? "plausibility-warning-row" : undefined}>
                            <td><b>{group.stage}</b><br /><small>{group.test_type}</small></td>
                            <td>{outputLabel(group.property)}{warnings.length ? <span className="plausibility-warning">⚠ 物理範囲外</span> : null}</td>
                            <td>{group.count}</td>
                            <td>{number(group.min, 1)}</td>
                            <td>
                              {number(group.mean, 1)} ±{" "}
                              {number(group.std, 1)}
                            </td>
                            <td>{number(group.median, 1)}</td>
                            <td>{number(group.max, 1)}</td>
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
                          .map(([key, value]) => <span key={key} className={(observation.output_warnings ?? {})[key]?.length ? "plausibility-value" : undefined}>{outputLabel(key)} {number(value, 1)}{(observation.output_warnings ?? {})[key]?.length ? <small>⚠ 物理範囲外</small> : null}</span>) }
                        {Object.values(observation.output_warnings ?? {}).flat().map((warning) => <em className="plausibility-reason" key={warning}>{warning}</em>)}
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
            <p>左の検索結果を選ぶか、キーを直接指定すると、実在する関係線と前後工程を表示します。</p>
            {index && <p>{number(index.total_entities)}ノード / {number(index.relation_rows)} relation行 / {index.detected_issues}件の品質問題</p>}
          </section>
        </main>
      )}
      </div>
    </div>
  );
}
