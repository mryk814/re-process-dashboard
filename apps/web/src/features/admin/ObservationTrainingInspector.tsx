import { useEffect, useMemo, useRef, useState } from "react";
import {
  workbenchApi,
  type ApiObservationTrainingPage,
  type ApiObservationTrainingProfile,
} from "../../shared/api/workbench-api";

const FAMILY_LABELS: Record<string, string> = {
  tensile: "引張",
  charpy: "シャルピー",
  corrosion: "腐食",
};

const TARGET_LABELS: Record<string, string> = {
  TS: "引張強さ",
  YS: "0.2%耐力",
  EL: "破断伸び",
  RA: "絞り",
  CHARPY_ENERGY: "吸収エネルギー",
  BRITTLE_FRACTURE: "脆性破面率",
  CORROSION_RATE: "腐食速度",
};

function valueLabel(value: unknown) {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString("ja-JP", { maximumFractionDigits: 6 });
  if (typeof value === "boolean") return value ? "はい" : "いいえ";
  return String(value);
}

export function ObservationTrainingInspector() {
  const [profiles, setProfiles] = useState<ApiObservationTrainingProfile[]>([]);
  const [family, setFamily] = useState("");
  const [target, setTarget] = useState("");
  const [page, setPage] = useState<ApiObservationTrainingPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const tableRef = useRef<HTMLDivElement>(null);
  const limit = 25;
  const profile = profiles[0];
  const familySummary = profile?.families.find((item) => item.family === family);
  const availableTargets = familySummary?.targets ?? [];

  useEffect(() => {
    let live = true;
    setLoading(true);
    workbenchApi.developerObservationTrainingProfiles()
      .then((items) => {
        if (!live) return;
        setProfiles(items);
        const initialFamily = items[0]?.families[0];
        setFamily(initialFamily?.family ?? "");
        setTarget(initialFamily?.targets[0]?.target ?? "");
      })
      .catch((cause) => {
        if (live) setError(cause instanceof Error ? cause.message : "観測Profileを取得できませんでした。");
      })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (!profile || !family || !target) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    workbenchApi.developerObservationTrainingData(profile.profile_id, family, target, offset, limit, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return;
        setPage((current) => offset === 0 || !current || current.family !== next.family || current.target !== next.target
          ? next
          : { ...next, offset: 0, rows: [...current.rows, ...next.rows] });
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "観測学習データを取得できませんでした。");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [family, offset, profile?.profile_id, target]);

  const inputPaths = useMemo(
    () => Object.keys(page?.rows[0]?.inputs ?? {}),
    [page?.family, page?.target],
  );
  const resetPage = () => {
    setOffset(0);
    setPage(null);
    tableRef.current?.scrollTo({ top: 0, left: 0 });
  };
  const onScroll = () => {
    const element = tableRef.current;
    if (!element || !page || loading || page.rows.length >= page.source_rows) return;
    if (element.scrollHeight - element.scrollTop - element.clientHeight <= 80) {
      setOffset(page.rows.length);
    }
  };

  return <section className="developer-section observation-training-inspector">
    <div className="observation-training-header">
      <div>
        <h3>観測family別 学習View</h3>
        <p>relationは結合索引としてだけ使い、試験シートの観測行を学習候補行として表示します。</p>
      </div>
      {profile && <code>{profile.source_filename}</code>}
    </div>
    <div className="observation-training-controls">
      <label>観測family
        <select value={family} onChange={(event) => {
          const nextFamily = event.target.value;
          const nextSummary = profile?.families.find((item) => item.family === nextFamily);
          setFamily(nextFamily);
          setTarget(nextSummary?.targets[0]?.target ?? "");
          resetPage();
        }}>
          {profile?.families.map((item) => <option value={item.family} key={item.family}>
            {FAMILY_LABELS[item.family] ?? item.family}
          </option>)}
        </select>
      </label>
      <label>目的変数
        <select value={target} onChange={(event) => { setTarget(event.target.value); resetPage(); }}>
          {availableTargets.map((item) => <option value={item.target} key={item.target}>
            {TARGET_LABELS[item.target] ?? item.target}
          </option>)}
        </select>
      </label>
    </div>
    {familySummary && <div className="observation-training-summary">
      <div><small>観測行</small><strong>{familySummary.source_rows.toLocaleString("ja-JP")}</strong></div>
      <div><small>入力を利用可能</small><strong>{familySummary.usable_input_rows.toLocaleString("ja-JP")}</strong></div>
      <div><small>{TARGET_LABELS[target] ?? target}を利用</small><strong>{(page?.usable_rows ?? 0).toLocaleString("ja-JP")}</strong></div>
      <div><small>目的変数で除外</small><strong>{page ? (page.source_rows - page.usable_rows).toLocaleString("ja-JP") : "—"}</strong></div>
      <div><small>施工group</small><strong>{(page?.split_groups ?? familySummary.split_groups).toLocaleString("ja-JP")}</strong></div>
      {Object.keys(familySummary.exclusion_reasons).length > 0 && <details>
        <summary>入力の除外理由</summary>
        <ul>{Object.entries(familySummary.exclusion_reasons).map(([reason, count]) => <li key={reason}>
          <span>{reason}</span><b>{count.toLocaleString("ja-JP")}行</b>
        </li>)}</ul>
      </details>}
      {page && Object.keys(page.exclusion_reasons).length > 0 && <details>
        <summary>{TARGET_LABELS[target] ?? target}の除外理由</summary>
        <ul>{Object.entries(page.exclusion_reasons).map(([reason, count]) => <li key={reason}>
          <span>{reason}</span><b>{count.toLocaleString("ja-JP")}行</b>
        </li>)}</ul>
      </details>}
    </div>}
    {error && <p className="panel-error">{error}</p>}
    {loading && page == null && !error && <p className="empty-evidence">観測行を組み立てています。</p>}
    {page && <div className="observation-training-table-wrap" ref={tableRef} onScroll={onScroll}>
      <table className="training-data-table">
        <thead><tr>
          <th><small>識別</small>試験キー</th>
          <th><small>分割group</small>溶接施工キー</th>
          <th><small>provenance</small>溶着成分キー</th>
          {inputPaths.map((path) => <th key={path}><small>入力</small>{path}</th>)}
          <th><small>実測</small>{TARGET_LABELS[target] ?? target}</th>
          <th><small>判定</small>利用可否</th>
        </tr></thead>
        <tbody>{page.rows.map((row) => <tr key={row.observation_id}>
          <td>{row.observation_id}</td>
          <td>{row.split_group_key ?? "—"}</td>
          <td>{row.provenance.entity_keys.weld_metal ?? "—"}</td>
          {inputPaths.map((path) => <td key={path}>{valueLabel(row.inputs[path])}</td>)}
          <td>{valueLabel(row.outputs[target])}</td>
          <td>{row.target_status[target]?.usable ? "利用" : row.target_status[target]?.reasons.join(" / ") || "除外"}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    {page && <div className="training-data-footer">
      <span>{page.rows.length.toLocaleString("ja-JP")} / {page.source_rows.toLocaleString("ja-JP")}行</span>
      <span>{loading ? "続きを読み込み中…" : page.rows.length < page.source_rows ? "下へスクロールして続きを表示" : "すべて表示しました"}</span>
    </div>}
    {profile && <details className="training-data-identity">
      <summary>再現情報</summary>
      <dl>
        <div><dt>Profile</dt><dd>{profile.profile_id} · {profile.profile_digest}</dd></div>
        <div><dt>Source</dt><dd>sha256:{profile.source_sha256}</dd></div>
      </dl>
    </details>}
  </section>;
}
