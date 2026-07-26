import { useCallback, useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiConnectorLifecycleDetail,
  type ApiDataLibraryDataset,
  type ApiDataLifecycleCatalog,
} from "../../shared/api/workbench-api";

const DEMO_OBJECT = `[
  {"id":"A-01","value":"12.4","target":"98.1"},
  {"id":"A-02","value":" 15.0 ","target":"97.5"},
  {"id":"CHECK-03","value":"","target":"96.8"}
]`;

const shortDigest = (value: string) => value.replace("sha256:", "").slice(0, 12);
const splitFields = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);

export function SourceLifecycleSection({ datasets }: { datasets: ApiDataLibraryDataset[] }) {
  const [catalog, setCatalog] = useState<ApiDataLifecycleCatalog | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<ApiConnectorLifecycleDetail | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [connectorName, setConnectorName] = useState("共有objectの品質更新");
  const [sourceLocator, setSourceLocator] = useState("s3://example-bucket/material/latest.json");
  const [primaryKey, setPrimaryKey] = useState("id");
  const [objectContent, setObjectContent] = useState(DEMO_OBJECT);
  const [objectVersion, setObjectVersion] = useState("manual-1");
  const [credential, setCredential] = useState("");
  const [recipeName, setRecipeName] = useState("標準JSON curation");
  const [numberFields, setNumberFields] = useState("value,target");
  const [requiredFields, setRequiredFields] = useState("id,value");
  const [targetFields, setTargetFields] = useState("target");
  const [profileId, setProfileId] = useState("");
  const [recipeId, setRecipeId] = useState("");
  const [actor, setActor] = useState("local-user");
  const [reason, setReason] = useState("");

  const profiles = useMemo(() => {
    const seen = new Set<string>();
    return datasets.map((item) => item.profile_revision).filter((profile) => {
      if (seen.has(profile.id)) return false;
      seen.add(profile.id);
      return true;
    });
  }, [datasets]);

  const refreshCatalog = useCallback(async (preferredId?: string) => {
    const next = await workbenchApi.dataLifecycleCatalog();
    setCatalog(next);
    setSelectedId((current) => {
      const preferred = preferredId ?? current;
      return next.connectors.some((item) => item.id === preferred)
        ? preferred
        : next.connectors[0]?.id ?? "";
    });
    setRecipeId((current) => next.recipes.some((item) => item.id === current) ? current : next.recipes.at(-1)?.id ?? "");
  }, []);

  const refreshDetail = useCallback(async (connectorId: string) => {
    if (!connectorId) {
      setDetail(null);
      return;
    }
    setDetail(await workbenchApi.sourceConnectorDetail(connectorId));
  }, []);

  useEffect(() => {
    refreshCatalog().catch((cause) => setError(cause instanceof Error ? cause.message : "データ更新履歴を取得できませんでした。"));
  }, [refreshCatalog]);

  useEffect(() => {
    refreshDetail(selectedId).catch((cause) => setError(cause instanceof Error ? cause.message : "Source Connectorを取得できませんでした。"));
  }, [refreshDetail, selectedId]);

  useEffect(() => {
    if (!profileId && profiles[0]) setProfileId(profiles[0].id);
  }, [profileId, profiles]);

  async function act(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "処理を完了できませんでした。");
    } finally {
      setBusy("");
    }
  }

  const latestRaw = detail?.raw_snapshots.at(-1);
  const latestRun = detail?.curation_runs.at(-1);
  const latestRevision = detail?.canonical_revisions.at(-1);
  const latestTraining = detail?.training_snapshots.at(-1);
  const selectedProfile = profiles.find((item) => item.id === profileId);

  async function createConnector() {
    await act("connector", async () => {
      const created = await workbenchApi.createSourceConnector({
        schema_version: "source-connector/v1",
        name: connectorName,
        connector_type: "object_storage_json_v1",
        source_locator: sourceLocator,
        selection: {
          schema_version: "object-selection/v1",
          format: "json_array",
          primary_key: primaryKey || null,
          included_fields: [],
        },
        trigger_policy: "manual_only",
        schedule: null,
      });
      await refreshCatalog(created.id);
      setNotice("Connectorを登録しました。取得はまだ実行していません。");
    });
  }

  async function fetchRaw() {
    if (!selectedId) return;
    await act("fetch", async () => {
      const oneTimeCredential = credential;
      setCredential("");
      const fetched = await workbenchApi.fetchSourceConnector(selectedId, {
        schema_version: "source-fetch-request/v1",
        trigger_kind: "manual",
        object_content: objectContent,
        object_version: objectVersion,
        retry_of: null,
      }, oneTimeCredential);
      await refreshDetail(selectedId);
      setNotice(fetched.attempt.reused_existing_snapshot
        ? "同じ内容のため既存Raw Snapshotへ統合しました。"
        : "不変Raw Snapshotを取得しました。承認や学習はまだ行っていません。");
    });
  }

  async function createRecipe() {
    await act("recipe", async () => {
      const numeric = splitFields(numberFields);
      const required = splitFields(requiredFields);
      const targets = splitFields(targetFields);
      const fields = [...new Set(["id", ...numeric, ...required, ...targets])];
      const recipe = await workbenchApi.createCurationRecipe({
        schema_version: "curation-recipe/v1",
        recipe_id: `json-curation-${Date.now()}`,
        version: 1,
        name: recipeName,
        steps: [
          { kind: "trim_strings_v1", fields },
          ...(numeric.length ? [{ kind: "coerce_number_v1" as const, fields: numeric }] : []),
          { kind: "required_fields_v1", fields: required },
          { kind: "target_eligibility_v1", fields: targets },
        ],
      });
      await refreshCatalog(selectedId);
      setRecipeId(recipe.id);
      setNotice("Versioned Curation Recipeを登録しました。");
    });
  }

  async function curate() {
    if (!latestRaw || !selectedProfile || !recipeId || !selectedId) return;
    await act("curate", async () => {
      await workbenchApi.curateRawSnapshot(latestRaw.id, {
        recipe_resource_id: recipeId,
        profile_revision_id: selectedProfile.id,
        profile_digest: selectedProfile.profile_digest,
      });
      await refreshDetail(selectedId);
      setNotice("Canonical候補を作成しました。承認前のため学習には使われません。");
    });
  }

  async function approve() {
    if (!latestRun || !selectedId) return;
    await act("approve", async () => {
      await workbenchApi.approveCurationRun(latestRun.id, {
        actor,
        reason,
        overrides: [],
      });
      await refreshDetail(selectedId);
      setNotice("Canonical Dataset Revisionを承認しました。再学習・active化は行っていません。");
    });
  }

  async function createTraining() {
    if (!latestRevision || !selectedId) return;
    await act("training", async () => {
      await workbenchApi.createApprovedTrainingSnapshot(latestRevision.id, {
        actor,
        purpose: reason.trim() || "モデル候補の再評価",
      });
      await refreshDetail(selectedId);
      setNotice("Training Snapshotを明示作成しました。Model Packageは変更していません。");
    });
  }

  return <section className="data-library-section source-lifecycle-section">
    <div className="panel-title">
      <div><h3>Source更新</h3><span>取得・品質確認・承認・学習Snapshotを分離</span></div>
      <details className="source-create">
        <summary>＋ Connector</summary>
        <label>名前<input value={connectorName} onChange={(event) => setConnectorName(event.target.value)} /></label>
        <label>Object locator<input value={sourceLocator} onChange={(event) => setSourceLocator(event.target.value)} /></label>
        <label>Row key<input value={primaryKey} onChange={(event) => setPrimaryKey(event.target.value)} /></label>
        <button className="primary-button" type="button" disabled={busy === "connector"} onClick={() => void createConnector()}>登録</button>
      </details>
    </div>
    <div className="source-trust-note">Source refresh ≠ retraining ≠ activation</div>
    {error && <p className="panel-error" role="alert">{error}</p>}
    {notice && <p className="source-notice" role="status">{notice}</p>}
    <div className="source-lifecycle-layout">
      <nav aria-label="Source Connectorの選択">
        {catalog?.connectors.map((connector) => <button type="button" key={connector.id} className={connector.id === selectedId ? "active" : ""} onClick={() => setSelectedId(connector.id)}>
          <strong>{connector.name}</strong><span>{connector.source_locator}</span>
        </button>)}
        {catalog?.connectors.length === 0 && <p>Connectorはまだありません。</p>}
      </nav>
      {detail ? <div className="source-lifecycle-detail">
        <header><div><strong>{detail.connector.name}</strong><span>{detail.connector.source_locator}</span></div><code>{shortDigest(detail.connector.configuration_digest)}</code></header>
        <ol className="source-stage-rail" aria-label="データ更新の信頼境界">
          <li className={latestRaw ? "complete" : "current"}><b>1</b><span>Raw Snapshot<small>{detail.raw_snapshots.length} revision</small></span></li>
          <li className={latestRun ? "complete" : latestRaw ? "current" : ""}><b>2</b><span>Curation<small>{latestRun ? `${latestRun.quality.quarantined}件隔離` : "未実行"}</small></span></li>
          <li className={latestRevision ? "complete" : latestRun ? "current" : ""}><b>3</b><span>承認<small>{latestRevision ? latestRevision.actor : "未承認"}</small></span></li>
          <li className={latestTraining ? "complete" : latestRevision ? "current" : ""}><b>4</b><span>Training Snapshot<small>{latestTraining ? `${latestTraining.row_count}行` : "未作成"}</small></span></li>
        </ol>
        <details className="source-action-panel" open={!latestRaw}>
          <summary>Raw Snapshotを取得</summary>
          <div className="source-action-grid">
            <label>Object version<input value={objectVersion} onChange={(event) => setObjectVersion(event.target.value)} /></label>
            <label>Credential（保存しません）<input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} /></label>
          </div>
          <label>Object JSON<textarea rows={5} value={objectContent} onChange={(event) => setObjectContent(event.target.value)} /></label>
          <button className="outline-button" type="button" disabled={busy === "fetch"} onClick={() => void fetchRaw()}>{busy === "fetch" ? "取得中…" : "手動取得"}</button>
        </details>
        {latestRaw && <div className="source-snapshot-summary">
          <div><span>Latest Raw</span><strong>{latestRaw.row_count}行</strong><code>{shortDigest(latestRaw.snapshot_digest)}</code></div>
          <dl><div><dt>追加</dt><dd>+{latestRaw.diff.added_rows}</dd></div><div><dt>変更</dt><dd>{latestRaw.diff.changed_rows}</dd></div><div><dt>消失</dt><dd>-{latestRaw.diff.removed_rows}</dd></div></dl>
        </div>}
        <details className="source-action-panel" open={Boolean(latestRaw && !latestRun)}>
          <summary>Curation RecipeとProfile</summary>
          <div className="source-action-grid">
            <label>Recipe<select value={recipeId} onChange={(event) => setRecipeId(event.target.value)}><option value="">選択</option>{catalog?.recipes.map((recipe) => <option value={recipe.id} key={recipe.id}>{recipe.name} v{recipe.version}</option>)}</select></label>
            <label>Dataset Profile<select value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">選択</option>{profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</select></label>
          </div>
          {catalog?.recipes.length === 0 && <div className="source-recipe-builder">
            <label>Recipe名<input value={recipeName} onChange={(event) => setRecipeName(event.target.value)} /></label>
            <label>数値列<input value={numberFields} onChange={(event) => setNumberFields(event.target.value)} /></label>
            <label>必須列<input value={requiredFields} onChange={(event) => setRequiredFields(event.target.value)} /></label>
            <label>目的変数列<input value={targetFields} onChange={(event) => setTargetFields(event.target.value)} /></label>
            <button className="outline-button" type="button" onClick={() => void createRecipe()}>Recipeを登録</button>
          </div>}
          <button className="outline-button" type="button" disabled={!latestRaw || !recipeId || !selectedProfile || busy === "curate"} onClick={() => void curate()}>{busy === "curate" ? "判定中…" : "品質判定を実行"}</button>
        </details>
        {latestRun && <div className="source-quality-summary">
          <div><span>採用</span><strong>{latestRun.quality.accepted}</strong></div>
          <div><span>注意</span><strong>{latestRun.quality.warning}</strong></div>
          <div><span>隔離</span><strong>{latestRun.quality.quarantined}</strong></div>
          <div><span>停止</span><strong>{latestRun.quality.blocked}</strong></div>
          <details><summary>理由付きrow</summary>{latestRun.rows.filter((row) => row.reason_codes.length).map((row) => <p key={row.row_key}><b>{row.row_key}</b><span>{row.reason_codes.join(" / ")}</span><em>{row.target_eligible ? "target利用可" : "target利用不可"}</em></p>)}</details>
          {latestRun.quality_delta.comparable && <p className="source-quality-delta">前回比　採用 {latestRun.quality_delta.accepted_delta >= 0 ? "+" : ""}{latestRun.quality_delta.accepted_delta} / 注意 {latestRun.quality_delta.warning_delta >= 0 ? "+" : ""}{latestRun.quality_delta.warning_delta} / 隔離 {latestRun.quality_delta.quarantined_delta >= 0 ? "+" : ""}{latestRun.quality_delta.quarantined_delta}</p>}
        </div>}
        {latestRun && !latestRevision && <div className="source-approval-action">
          <label>Actor<input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
          <label>承認理由<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="任意。override時は必須" /></label>
          <button className="primary-button" type="button" disabled={!actor.trim() || busy === "approve"} onClick={() => void approve()}>{latestRun.quality.quarantined ? "隔離行を除いて承認" : "Canonical Datasetを承認"}</button>
        </div>}
        {latestRevision && !latestTraining && <div className="source-approval-action">
          <label>Actor<input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
          <label>用途<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="例: 再学習候補の比較" /></label>
          <button className="primary-button" type="button" disabled={!actor.trim() || busy === "training"} onClick={() => void createTraining()}>Training Snapshotを作成</button>
        </div>}
        {latestTraining && <div className="source-training-ready"><strong>Training Snapshot ready</strong><span>{latestTraining.row_count}行 · {latestTraining.purpose}</span><code>{shortDigest(latestTraining.snapshot_digest)}</code><small>再学習・Package検証・active化は別操作です。</small></div>}
      </div> : <div className="source-empty">Connectorを登録すると、更新履歴と承認状態をここで確認できます。</div>}
    </div>
  </section>;
}
