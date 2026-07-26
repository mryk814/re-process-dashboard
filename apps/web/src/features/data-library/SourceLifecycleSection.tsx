import { useCallback, useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiConnectorLifecycleDetail,
  type ApiDataLibraryDataset,
  type ApiDataLifecycleCatalog,
} from "../../shared/api/workbench-api";

const shortDigest = (value: string) => value.replace("sha256:", "").slice(0, 12);
const splitFields = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
const reasonLabels = {
  missing_required: "必須項目がありません",
  invalid_number: "数値として解釈できません",
  filter_mismatch: "抽出条件に一致しません",
  sum_limit_exceeded: "合計値が許容範囲を超えています",
  missing_target: "目的変数がありません",
  duplicate_row_key: "行識別キーが重複しています",
} as const;

export function SourceLifecycleSection({ datasets }: { datasets: ApiDataLibraryDataset[] }) {
  const [catalog, setCatalog] = useState<ApiDataLifecycleCatalog | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<ApiConnectorLifecycleDetail | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [connectorName, setConnectorName] = useState("");
  const [sourceLocator, setSourceLocator] = useState("");
  const [primaryKey, setPrimaryKey] = useState("");
  const [objectContent, setObjectContent] = useState("");
  const [objectVersion, setObjectVersion] = useState("");
  const [credential, setCredential] = useState("");
  const [recipeName, setRecipeName] = useState("");
  const [numberFields, setNumberFields] = useState("");
  const [requiredFields, setRequiredFields] = useState("");
  const [targetFields, setTargetFields] = useState("");
  const [profileId, setProfileId] = useState("");
  const [recipeId, setRecipeId] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [trainingPurpose, setTrainingPurpose] = useState("");
  const [overrideRowKeys, setOverrideRowKeys] = useState<string[]>([]);
  const [overrideReasons, setOverrideReasons] = useState<Record<string, string>>({});

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
    setOverrideRowKeys([]);
    setOverrideReasons({});
    setApprovalReason("");
    setTrainingPurpose("");
    refreshDetail(selectedId).catch((cause) => setError(cause instanceof Error ? cause.message : "接続先を取得できませんでした。"));
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
  const currentActor = catalog?.current_actor;
  const overrideCandidates = latestRun?.rows.filter((row) => row.status === "quarantined") ?? [];
  const overrideReasonMissing = overrideRowKeys.some((rowKey) => !overrideReasons[rowKey]?.trim());
  const approvalBlocked = busy === "approve"
    || (overrideRowKeys.length > 0 && (!approvalReason.trim() || overrideReasonMissing));

  const actorLabel = (actor: string) => actor === currentActor?.id ? currentActor.label : actor;
  const reasonLabel = (reason: keyof typeof reasonLabels) => reasonLabels[reason];

  async function createConnector() {
    await act("connector", async () => {
      const created = await workbenchApi.createSourceConnector({
        schema_version: "source-connector/v1",
        name: connectorName.trim(),
        connector_type: "object_storage_json_v1",
        source_locator: sourceLocator.trim(),
        selection: {
          schema_version: "object-selection/v1",
          format: "json_array",
          primary_key: primaryKey.trim() || null,
          included_fields: [],
        },
        trigger_policy: "manual_only",
        schedule: null,
      });
      await refreshCatalog(created.id);
      setNotice("接続先を登録しました。データ取得はまだ実行していません。");
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
        object_version: objectVersion.trim(),
        retry_of: null,
      }, oneTimeCredential);
      await refreshDetail(selectedId);
      setNotice(fetched.attempt.reused_existing_snapshot
        ? "同じ内容のため既存の取得スナップショットへ統合しました。"
        : "不変の取得スナップショットを作成しました。承認や学習はまだ行っていません。");
    });
  }

  async function createRecipe() {
    await act("recipe", async () => {
      const numeric = splitFields(numberFields);
      const required = splitFields(requiredFields);
      const targets = splitFields(targetFields);
      const rowKey = detail?.connector.selection.primary_key;
      const fields = [...new Set([...(rowKey ? [rowKey] : []), ...numeric, ...required, ...targets])];
      const recipe = await workbenchApi.createCurationRecipe({
        schema_version: "curation-recipe/v1",
        recipe_id: `json-curation-${Date.now()}`,
        version: 1,
        name: recipeName.trim(),
        steps: [
          { kind: "trim_strings_v1", fields },
          ...(numeric.length ? [{ kind: "coerce_number_v1" as const, fields: numeric }] : []),
          { kind: "required_fields_v1", fields: required },
          { kind: "target_eligibility_v1", fields: targets },
        ],
      });
      await refreshCatalog(selectedId);
      setRecipeId(recipe.id);
      setNotice("版管理された品質判定レシピを登録しました。");
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
      setNotice("品質判定済みの候補を作成しました。承認前のため学習には使われません。");
    });
  }

  async function approve() {
    if (!latestRun || !selectedId) return;
    await act("approve", async () => {
      await workbenchApi.approveCurationRun(latestRun.id, {
        reason: approvalReason.trim(),
        overrides: overrideRowKeys.map((rowKey) => ({
          row_key: rowKey,
          reason: overrideReasons[rowKey].trim(),
        })),
      });
      await refreshDetail(selectedId);
      setNotice("正規データセットの版を承認しました。再学習・有効化は行っていません。");
    });
  }

  async function createTraining() {
    if (!latestRevision || !selectedId) return;
    await act("training", async () => {
      await workbenchApi.createApprovedTrainingSnapshot(latestRevision.id, {
        purpose: trainingPurpose.trim() || "モデル候補の再評価",
      });
      await refreshDetail(selectedId);
      setNotice("学習用スナップショットを明示作成しました。モデルパッケージは変更していません。");
    });
  }

  const toggleOverride = (rowKey: string) => {
    setOverrideRowKeys((current) => current.includes(rowKey)
      ? current.filter((item) => item !== rowKey)
      : [...current, rowKey]);
  };

  return <section className="data-library-section source-lifecycle-section">
    <div className="panel-title">
      <div><h3>データ更新</h3><span>取得・品質確認・承認・学習用スナップショットを分離</span></div>
      <details className="source-create">
        <summary>＋ 接続先</summary>
        <label>名前<input value={connectorName} onChange={(event) => setConnectorName(event.target.value)} /></label>
        <label>データの場所<input value={sourceLocator} onChange={(event) => setSourceLocator(event.target.value)} /></label>
        <label>行識別キー<input value={primaryKey} onChange={(event) => setPrimaryKey(event.target.value)} /></label>
        <button className="primary-button" type="button" disabled={!connectorName.trim() || !sourceLocator.trim() || busy === "connector"} onClick={() => void createConnector()}>登録</button>
      </details>
    </div>
    <div className="source-trust-note">データ取得・承認・再学習・有効化は、それぞれ別の操作です</div>
    {error && <p className="panel-error" role="alert">{error}</p>}
    {notice && <p className="source-notice" role="status">{notice}</p>}
    <div className="source-lifecycle-layout">
      <nav aria-label="接続先の選択">
        {catalog?.connectors.map((connector) => <button type="button" key={connector.id} className={connector.id === selectedId ? "active" : ""} onClick={() => setSelectedId(connector.id)}>
          <strong>{connector.name}</strong><span>{connector.source_locator}</span>
        </button>)}
        {catalog?.connectors.length === 0 && <p>接続先はまだありません。</p>}
      </nav>
      {detail ? <div className="source-lifecycle-detail">
        <header><div><strong>{detail.connector.name}</strong><span>{detail.connector.source_locator}</span></div><code>{shortDigest(detail.connector.configuration_digest)}</code></header>
        <ol className="source-stage-rail" aria-label="データ更新の信頼境界">
          <li className={latestRaw ? "complete" : "current"}><b>1</b><span>取得スナップショット<small>{detail.raw_snapshots.length}版</small></span></li>
          <li className={latestRun ? "complete" : latestRaw ? "current" : ""}><b>2</b><span>品質判定<small>{latestRun ? `${latestRun.quality.quarantined}件隔離` : "未実行"}</small></span></li>
          <li className={latestRevision ? "complete" : latestRun ? "current" : ""}><b>3</b><span>承認<small>{latestRevision ? actorLabel(latestRevision.actor) : "未承認"}</small></span></li>
          <li className={latestTraining ? "complete" : latestRevision ? "current" : ""}><b>4</b><span>学習用スナップショット<small>{latestTraining ? `${latestTraining.row_count}行` : "未作成"}</small></span></li>
        </ol>
        <details className="source-action-panel source-validation-mode">
          <summary>検証モード：JSONを直接入力</summary>
          <p>接続確認用です。入力内容と認証情報は初期表示せず、認証情報は保存しません。</p>
          <div className="source-action-grid">
            <label>元データの版<input value={objectVersion} onChange={(event) => setObjectVersion(event.target.value)} /></label>
            <label>一時認証情報<input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} /></label>
          </div>
          <label>JSONデータ<textarea rows={5} value={objectContent} onChange={(event) => setObjectContent(event.target.value)} /></label>
          <button className="outline-button" type="button" disabled={!objectVersion.trim() || !objectContent.trim() || busy === "fetch"} onClick={() => void fetchRaw()}>{busy === "fetch" ? "取得中…" : "検証データを取得"}</button>
        </details>
        {latestRaw && <div className="source-snapshot-summary">
          <div><span>最新の取得版</span><strong>{latestRaw.row_count}行</strong><code>{shortDigest(latestRaw.snapshot_digest)}</code></div>
          <dl><div><dt>追加</dt><dd>+{latestRaw.diff.added_rows}</dd></div><div><dt>変更</dt><dd>{latestRaw.diff.changed_rows}</dd></div><div><dt>消失</dt><dd>-{latestRaw.diff.removed_rows}</dd></div></dl>
        </div>}
        <details className="source-action-panel" open={Boolean(latestRaw && !latestRun)}>
          <summary>品質判定レシピとデータセットプロファイル</summary>
          <div className="source-action-grid">
            <label>品質判定レシピ<select value={recipeId} onChange={(event) => setRecipeId(event.target.value)}><option value="">選択</option>{catalog?.recipes.map((recipe) => <option value={recipe.id} key={recipe.id}>{recipe.name} v{recipe.version}</option>)}</select></label>
            <label>データセットプロファイル<select value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">選択</option>{profiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</select></label>
          </div>
          {catalog?.recipes.length === 0 && <div className="source-recipe-builder">
            <label>レシピ名<input value={recipeName} onChange={(event) => setRecipeName(event.target.value)} /></label>
            <label>数値列<input value={numberFields} onChange={(event) => setNumberFields(event.target.value)} /></label>
            <label>必須列<input value={requiredFields} onChange={(event) => setRequiredFields(event.target.value)} /></label>
            <label>目的変数列<input value={targetFields} onChange={(event) => setTargetFields(event.target.value)} /></label>
            <button className="outline-button" type="button" disabled={!recipeName.trim()} onClick={() => void createRecipe()}>レシピを登録</button>
          </div>}
          <button className="outline-button" type="button" disabled={!latestRaw || !recipeId || !selectedProfile || busy === "curate"} onClick={() => void curate()}>{busy === "curate" ? "判定中…" : "品質判定を実行"}</button>
        </details>
        {latestRun && <div className="source-quality-summary">
          <div><span>採用</span><strong>{latestRun.quality.accepted}</strong></div>
          <div><span>注意</span><strong>{latestRun.quality.warning}</strong></div>
          <div><span>隔離</span><strong>{latestRun.quality.quarantined}</strong></div>
          <div><span>停止</span><strong>{latestRun.quality.blocked}</strong></div>
          <details><summary>理由付きの行</summary>{latestRun.rows.filter((row) => row.reason_codes.length).map((row) => <p key={row.row_key}><b>{row.row_key}</b><span>{row.reason_codes.map((code) => reasonLabel(code)).join(" / ")}</span><em>{row.target_eligible ? "目的変数として利用可" : "目的変数として利用不可"}</em></p>)}</details>
          {latestRun.quality_delta.comparable && <p className="source-quality-delta">前回比　採用 {latestRun.quality_delta.accepted_delta >= 0 ? "+" : ""}{latestRun.quality_delta.accepted_delta} / 注意 {latestRun.quality_delta.warning_delta >= 0 ? "+" : ""}{latestRun.quality_delta.warning_delta} / 隔離 {latestRun.quality_delta.quarantined_delta >= 0 ? "+" : ""}{latestRun.quality_delta.quarantined_delta}</p>}
        </div>}
        {latestRun && !latestRevision && <div className="source-approval-block">
          {overrideCandidates.length > 0 && <details className="source-override-panel">
            <summary>判定を上書きして採用する行を選ぶ</summary>
            <p>上書きする場合は、行ごとの根拠と全体の承認理由が必須です。</p>
            {overrideCandidates.map((row) => <div key={row.row_key}>
              <label><input type="checkbox" checked={overrideRowKeys.includes(row.row_key)} onChange={() => toggleOverride(row.row_key)} />{row.row_key} · {row.reason_codes.map((code) => reasonLabel(code)).join(" / ")}</label>
              {overrideRowKeys.includes(row.row_key) && <label>この行を採用する根拠<input aria-label={`${row.row_key}の上書き理由`} value={overrideReasons[row.row_key] ?? ""} onChange={(event) => setOverrideReasons((current) => ({ ...current, [row.row_key]: event.target.value }))} /></label>}
            </div>)}
          </details>}
          <div className="source-approval-action">
            <div className="source-actor"><span>記録される主体</span><strong>{currentActor?.label ?? "確認中…"}</strong><small>{currentActor?.id}</small></div>
            <label>承認理由{overrideRowKeys.length > 0 && "（必須）"}<input value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} placeholder={overrideRowKeys.length > 0 ? "上書きを含めて承認する根拠" : "任意"} /></label>
            <button className="primary-button" type="button" disabled={approvalBlocked} onClick={() => void approve()}>{overrideRowKeys.length > 0 ? "上書きを含めて承認" : latestRun.quality.quarantined ? "隔離行を除いて承認" : "正規データセットを承認"}</button>
          </div>
        </div>}
        {latestRevision && !latestTraining && <div className="source-approval-action">
          <div className="source-actor"><span>記録される主体</span><strong>{currentActor?.label ?? "確認中…"}</strong><small>{currentActor?.id}</small></div>
          <label>用途<input value={trainingPurpose} onChange={(event) => setTrainingPurpose(event.target.value)} placeholder="例: 再学習候補の比較" /></label>
          <button className="primary-button" type="button" disabled={busy === "training"} onClick={() => void createTraining()}>学習用スナップショットを作成</button>
        </div>}
        {latestTraining && <div className="source-training-ready"><strong>学習用スナップショット作成済み</strong><span>{latestTraining.row_count}行 · {latestTraining.purpose}</span><code>{shortDigest(latestTraining.snapshot_digest)}</code><small>再学習・モデル検証・有効化は別の操作です。</small></div>}
      </div> : <div className="source-empty">接続先を登録すると、更新履歴と承認状態をここで確認できます。</div>}
    </div>
  </section>;
}
