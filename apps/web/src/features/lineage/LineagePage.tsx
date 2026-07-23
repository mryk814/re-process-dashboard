import { useEffect, useRef, useState } from "react";
import { fromApiCandidate, type CandidateViewModel as Candidate, type TaskOutputDefinition } from "../candidates";
import { assessOutputValues, resolveOutputDefinition } from "../../shared/outputPresentation";
import { workbenchApi, type ApiLineage, type ApiLineageIndex } from "../../shared/api/workbench-api";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { SvgChartTooltip } from "../../shared/ui/SvgChartTooltip";
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
  startTime: number;
  endTime: number;
};

type LineageGroupSelection = {
  parentKey: string;
  entityType: string;
  nodeKeys: string[];
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
  const [issueOnly, setIssueOnly] = useState(false);
  const [graphLimit, setGraphLimit] = useState(40);
  const [index, setIndex] = useState<ApiLineageIndex | null>(null);
  const [data, setData] = useState<ApiLineage | null>(null);
  const [error, setError] = useState("");
  const [candidateError, setCandidateError] = useState("");
  const [candidateOptionIndex, setCandidateOptionIndex] = useState(0);
  const [selectedGroup, setSelectedGroup] = useState<LineageGroupSelection | null>(null);
  const [hoveredHeatPoint, setHoveredHeatPoint] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const activeProjectRef = useRef(projectId);
  const outputLabel = (raw: string) => {
    const definition = outputs.find((output) => raw === output.key || raw.startsWith(`${output.key}[`) || (output.key === "lambda" && raw.startsWith("λ")));
    return definition ? `${definition.label}${definition.unit ? ` (${definition.unit})` : ""}` : raw;
  };
  activeProjectRef.current = projectId;
  const candidateOptions = data?.candidate_options ?? [];
  useEffect(() => {
    setEntityKey(initialEntityKey ?? "");
    setGraphLimit(40);
  }, [projectId, initialEntityKey]);
  useEffect(() => {
    setSelectedGroup(null);
    setCandidateOptionIndex(0);
  }, [entityKey]);
  useEffect(() => {
    setQuery("");
    setEntityType("");
    setIssueOnly(false);
    setIndex(null);
    setData(null);
    setError("");
    setCandidateError("");
  }, [projectId]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      workbenchApi.lineageIndex(projectId, query.trim(), entityType, issueOnly, controller.signal)
        .then(setIndex)
        .catch(() => undefined);
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [projectId, query, entityType, issueOnly]);
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
    workbenchApi.lineage(projectId, entityKey, graphLimit, controller.signal)
      .then((lineage) => {
        if (!controller.signal.aborted) {
          setData(lineage);
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
  }, [projectId, entityKey, graphLimit]);
  const createCandidate = async () => {
    const requestProjectId = projectId;
    const requestEntityKey = entityKey;
    const option = candidateOptions[candidateOptionIndex];
    try {
      const created = fromApiCandidate(await workbenchApi.createCandidateFromLineage(
        requestEntityKey,
        requestProjectId,
        option?.process_key,
        option?.melt_key,
      ));
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
  const heatX = (time: number) => 20 + (time / maxTime) * 380;
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
          <small className="lineage-result-limit">
            {index ? `${number(index.matched_entities ?? index.items.length)}件中${number(index.items.length)}件を表示` : "検索中"}
            {" · "}最大200件。選択するとグラフを開きます。
          </small>
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
                  <label className="lineage-candidate-option">
                    上流条件
                    <select
                      value={candidateOptionIndex}
                      onChange={(event) => setCandidateOptionIndex(Number(event.target.value))}
                    >
                      {candidateOptions.map((option, index) => (
                        <option key={`${option.process_key}-${option.melt_key}`} value={index}>
                          {option.process_label} {option.process_key} / 成分 {option.melt_key}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <CandidateAddButton
                  disabled={!supportsCandidateCreation || !data.candidate_eligible || !candidateOptions[candidateOptionIndex]}
                  title={supportsCandidateCreation ? data.candidate_reason : "この予測タスクは系譜からの候補化に対応していません"}
                  onClick={() => {
                    void createCandidate();
                  }}
                >
                  候補ストックへ追加
                </CandidateAddButton>
                <span className={`lineage-detail-action-reason ${supportsCandidateCreation && data.candidate_eligible ? "" : "muted"}`}>
                  {supportsCandidateCreation ? data.candidate_reason : "この予測タスクは系譜からの候補化に対応していません。"}
                </span>
                {candidateError && <span className="warning">{candidateError}</span>}
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
            </section>
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
                  viewBox="0 0 420 135"
                  className="lineage-heat"
                  role="img"
                  aria-label="実績ヒートパターン"
                >
                  {heatTemperatureTicks.map((tick) => <g key={`temp-${tick}`} className="lineage-heat-grid"><line x1="20" x2="400" y1={heatY(tick)} y2={heatY(tick)} /><text x="17" y={heatY(tick) + 3} textAnchor="end">{number(tick)}</text></g>)}
                  {heatTimeTicks.map((tick) => <g key={`time-${tick}`} className="lineage-heat-grid"><line x1={heatX(tick)} x2={heatX(tick)} y1="20" y2="120" /><text x={heatX(tick)} y="132" textAnchor="middle">{number(tick)}</text></g>)}
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
                      className="svg-chart-hit-target"
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
                  {hoveredHeatPoint && <SvgChartTooltip {...hoveredHeatPoint} chartWidth={420} chartHeight={135} />}
                </svg>
                <div className="lineage-process-track" aria-label="工程区間">
                  {heatStageTrack.map((stage, index) => (
                    <div
                      className={`lineage-process-segment stage-${stage.category.toLowerCase().replaceAll("_", "-")}`}
                      key={`${stage.category}-${stage.name}-${index}`}
                      style={{ flexBasis: `${((stage.endTime - stage.startTime) / maxTime) * 100}%` }}
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
            {index && <p>{number(index.total_entities)}ノード / {number(index.relation_rows)} relation行 / {index.detected_issues}件の品質問題</p>}
          </section>
        </main>
      )}
      </div>
    </div>
  );
}
