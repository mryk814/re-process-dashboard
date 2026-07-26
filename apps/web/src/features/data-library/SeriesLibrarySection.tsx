import { useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiCanonicalSeriesRevision,
  type ApiRawSeriesAsset,
  type ApiSeriesAssetDetail,
  type ApiSeriesFeaturePreview,
} from "../../shared/api/workbench-api";

type PlotPoint = {
  coordinate: number;
  value: number;
  channel: string;
};

const numberFormat = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 3 });
const statusLabel = {
  accepted: "そのまま採用",
  normalized: "正規化済み",
  warning: "注意あり",
  quarantined: "隔離",
  blocked: "変換停止",
} as const;

function SeriesSparkline({
  title,
  points,
  coordinateUnit,
  valueUnit,
  tone,
}: {
  title: string;
  points: PlotPoint[];
  coordinateUnit: string;
  valueUnit: string;
  tone: "raw" | "canonical";
}) {
  if (points.length === 0) return <div className="series-chart-empty">表示できる点がありません</div>;
  const width = 420;
  const height = 116;
  const inset = 18;
  const xs = points.map((item) => item.coordinate);
  const ys = points.map((item) => item.value);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const x = (value: number) => inset + ((value - xMin) / (xMax - xMin || 1)) * (width - inset * 2);
  const y = (value: number) => height - inset - ((value - yMin) / (yMax - yMin || 1)) * (height - inset * 2);
  const path = points.map((item, index) => `${index === 0 ? "M" : "L"}${x(item.coordinate)},${y(item.value)}`).join(" ");
  return <figure className={`series-mini-chart ${tone}`}>
    <figcaption><strong>{title}</strong><span>{points.length}点</span></figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}。${points.length}点、${coordinateUnit}と${valueUnit}`}>
      <title>{title}</title>
      <line x1={inset} y1={height - inset} x2={width - inset} y2={height - inset} className="axis" />
      <path d={path} className="series-line" />
      {points.map((item, index) => <circle key={`${item.coordinate}-${item.value}-${index}`} cx={x(item.coordinate)} cy={y(item.value)} r="3" />)}
      <text x={inset} y={height - 3}>{numberFormat.format(xMin)} {coordinateUnit}</text>
      <text x={width - inset} y={height - 3} textAnchor="end">{numberFormat.format(xMax)} {coordinateUnit}</text>
      <text x={inset} y={11}>{numberFormat.format(yMax)} {valueUnit}</text>
      <text x={inset} y={height - inset - 4}>{numberFormat.format(yMin)} {valueUnit}</text>
    </svg>
  </figure>;
}

function SeriesChartGroup({
  title,
  points,
  coordinateUnit,
  valueUnit,
  tone,
}: {
  title: string;
  points: PlotPoint[];
  coordinateUnit: string;
  valueUnit: string;
  tone: "raw" | "canonical";
}) {
  const channels = new Map<string, PlotPoint[]>();
  points.forEach((point) => {
    channels.set(point.channel, [...(channels.get(point.channel) ?? []), point]);
  });
  if (channels.size === 0) return <div className="series-chart-empty">表示できる点がありません</div>;
  return <div className="series-chart-group">
    {[...channels.entries()].map(([channel, channelPoints]) => (
      <SeriesSparkline
        key={channel}
        title={`${title}${channels.size > 1 ? ` — ${channel}` : ""}`}
        points={channelPoints}
        coordinateUnit={coordinateUnit}
        valueUnit={valueUnit}
        tone={tone}
      />
    ))}
  </div>;
}

function RawTable({ series }: { series: ApiRawSeriesAsset }) {
  return <details className="series-point-table">
    <summary>Raw points（{series.points.length}点）</summary>
    <div className="series-table-scroll"><table><thead><tr><th>Source</th><th>{series.coordinate_name}</th><th>{series.value_name}</th><th>Channel</th></tr></thead><tbody>
      {series.points.slice(0, 30).map((point) => <tr key={point.source_position}><td>{point.source_row ? `row ${point.source_row}` : `#${point.source_position}`}</td><td>{numberFormat.format(point.coordinate)} {series.coordinate_unit}</td><td>{numberFormat.format(point.value)} {series.value_unit}</td><td>{point.channel}</td></tr>)}
    </tbody></table></div>
    {series.points.length > 30 && <small>先頭30点を表示しています。</small>}
  </details>;
}

function CanonicalTable({ revision }: { revision: ApiCanonicalSeriesRevision }) {
  return <details className="series-point-table">
    <summary>Canonical points（{revision.points.length}点）</summary>
    <div className="series-table-scroll"><table><thead><tr><th>Source positions</th><th>{revision.coordinate_name}</th><th>{revision.value_name}</th><th>Channel</th></tr></thead><tbody>
      {revision.points.slice(0, 30).map((point, index) => <tr key={`${point.channel}-${point.coordinate}-${index}`}><td>{point.source_positions.join(", ")}</td><td>{numberFormat.format(point.coordinate)} {revision.coordinate_unit}</td><td>{numberFormat.format(point.value)} {revision.value_unit}</td><td>{point.channel}</td></tr>)}
    </tbody></table></div>
    {revision.points.length > 30 && <small>先頭30点を表示しています。</small>}
  </details>;
}

export function SeriesLibrarySection() {
  const [assets, setAssets] = useState<ApiRawSeriesAsset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<ApiSeriesAssetDetail | null>(null);
  const [feature, setFeature] = useState<ApiSeriesFeaturePreview | null>(null);
  const [representation, setRepresentation] = useState<"segment_statistics_v1" | "linear_resample_v1" | "sequence_tensor_v1">("segment_statistics_v1");
  const [loading, setLoading] = useState(true);
  const [featureLoading, setFeatureLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    workbenchApi.listSeriesAssets()
      .then((items) => {
        if (!active) return;
        setAssets(items);
        setSelectedId((current) => items.some((item) => item.id === current) ? current : items[0]?.id ?? "");
      })
      .catch((cause) => active && setError(cause instanceof Error ? cause.message : "系列データを取得できませんでした。"))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let active = true;
    setFeature(null);
    workbenchApi.seriesAsset(selectedId)
      .then((item) => active && setDetail(item))
      .catch((cause) => active && setError(cause instanceof Error ? cause.message : "系列データを取得できませんでした。"));
    return () => { active = false; };
  }, [selectedId]);

  const canonical = detail?.canonical_revisions.at(-1) ?? null;
  const rawPoints = useMemo(
    () => detail?.raw.points.map((point) => ({ coordinate: point.coordinate, value: point.value, channel: point.channel })) ?? [],
    [detail],
  );
  const canonicalPoints = useMemo(
    () => canonical?.points.map((point) => ({ coordinate: point.coordinate, value: point.value, channel: point.channel })) ?? [],
    [canonical],
  );

  async function inspectFeatures() {
    if (!canonical) return;
    setFeatureLoading(true);
    setError("");
    try {
      setFeature(await workbenchApi.seriesFeaturePreview(canonical.id, {
        schema_version: "series-feature-contract/v1",
        representation_id: representation,
        input_schema_version: "canonical-series/v1",
        sample_count: representation === "linear_resample_v1" ? 12 : null,
        include_coordinate: true,
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "系列特徴量を確認できませんでした。");
    } finally {
      setFeatureLoading(false);
    }
  }

  return <section className="data-library-section series-library-section">
    <div className="panel-title">
      <h3>系列データ（Series）</h3>
      <span>Raw → Canonical → Featureを分けて確認</span>
    </div>
    {error && <p className="panel-error" role="alert">{error}</p>}
    {loading ? <p className="library-empty">系列データを読み込んでいます…</p> : <div className="series-library-layout">
      <nav aria-label="系列データの選択">
        {assets.map((asset) => <button type="button" className={asset.id === selectedId ? "active" : ""} key={asset.id} onClick={() => setSelectedId(asset.id)}>
          <strong>{asset.name}</strong><span>{asset.series_kind} · {asset.points.length}点</span>
        </button>)}
      </nav>
      {detail && <div className="series-inspector">
        <div className="series-identity">
          <div><strong>{detail.raw.name}</strong><span>{detail.raw.provenance.source_locator}</span></div>
          <code title={detail.raw.content_digest}>{detail.raw.content_digest.replace("sha256:", "").slice(0, 12)}</code>
        </div>
        <div className="series-chart-pair">
          <SeriesChartGroup title="Raw（source順）" points={rawPoints} coordinateUnit={detail.raw.coordinate_unit} valueUnit={detail.raw.value_unit} tone="raw" />
          {canonical && canonical.points.length > 0
            ? <SeriesChartGroup title="Canonical（意味・単位を正規化）" points={canonicalPoints} coordinateUnit={canonical.coordinate_unit} valueUnit={canonical.value_unit} tone="canonical" />
            : <div className="series-chart-empty">Canonical seriesは公開されていません</div>}
        </div>
        <RawTable series={detail.raw} />
        {canonical && <>
          <div className="series-canonical-summary">
            <span className={`series-status ${canonical.status}`}>{statusLabel[canonical.status]}</span>
            <span>Recipe {canonical.recipe.recipe_id} {canonical.recipe.version}</span>
            <code title={canonical.canonical_digest}>{canonical.canonical_digest.replace("sha256:", "").slice(0, 12)}</code>
          </div>
          {canonical.transformation_log.length > 0 && <ol className="series-transform-log">{canonical.transformation_log.map((item) => <li key={item}>{item}</li>)}</ol>}
          {canonical.findings.length > 0 && <ul className="series-findings">{canonical.findings.map((finding, index) => <li className={finding.severity} key={`${finding.reason_code}-${index}`}><b>{finding.reason_code}</b><span>{finding.message}</span></li>)}</ul>}
          {canonical.points.length > 0 && <CanonicalTable revision={canonical} />}
          <div className="series-feature-inspector">
            <label>Feature representation
              <select value={representation} onChange={(event) => setRepresentation(event.target.value as typeof representation)}>
                <option value="segment_statistics_v1">区間統計量</option>
                <option value="linear_resample_v1">線形resampling（12点）</option>
                <option value="sequence_tensor_v1">可変長sequence tensor</option>
              </select>
            </label>
            <button type="button" className="outline-button" disabled={featureLoading || canonical.points.length === 0} onClick={() => void inspectFeatures()}>{featureLoading ? "変換中…" : "モデル入力を確認"}</button>
          </div>
          {feature && <details className="series-feature-result" open>
            <summary>{feature.feature_contract.representation_id} · shape [{feature.shape.join(" × ")}]</summary>
            <div>{feature.feature_names.slice(0, 18).map((name, index) => <span key={`${name}-${index}`}><small>{name}</small><b>{numberFormat.format(feature.values[index])}</b></span>)}</div>
            {feature.values.length > 18 && <small>先頭18要素を表示。契約digest {feature.feature_contract_digest.replace("sha256:", "").slice(0, 12)}</small>}
          </details>}
        </>}
      </div>}
    </div>}
  </section>;
}
