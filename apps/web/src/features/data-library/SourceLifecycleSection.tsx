import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  workbenchApi,
  type ApiConnectorLifecycleDetail,
  type ApiCurationRunRowPage,
  type ApiDataLibraryDataset,
  type ApiDataLifecycleCatalog,
  type ApiRawSnapshotRowPage,
} from "../../shared/api/workbench-api";
import {
  collectTrainingTargetFields,
  trainingRecipeIdForRevision,
} from "./trainingSnapshotPresentation";
import type { DataLibraryLocation } from "./location";

const shortDigest = (value: string) => value.replace("sha256:", "").slice(0, 12);
const formatTimestamp = (value: string) => new Date(value).toLocaleString("ja-JP");
const splitFields = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
type LifecycleStage = "raw" | "curation" | "approval" | "training";

function lifecycleDigest(item: object): string {
  if ("snapshot_digest" in item && typeof item.snapshot_digest === "string") return item.snapshot_digest;
  if ("curation_digest" in item && typeof item.curation_digest === "string") return item.curation_digest;
  if ("dataset_digest" in item && typeof item.dataset_digest === "string") return item.dataset_digest;
  return "";
}
const reasonLabels = {
  missing_required: "必須項目がありません",
  invalid_number: "数値として解釈できません",
  filter_mismatch: "抽出条件に一致しません",
  sum_limit_exceeded: "合計値が許容範囲を超えています",
  missing_target: "目的変数がありません",
  duplicate_row_key: "行識別キーが重複しています",
} as const;

export function SourceLifecycleSection({
  datasets,
  location,
  onNavigate,
}: {
  datasets: ApiDataLibraryDataset[];
  location: DataLibraryLocation;
  onNavigate: (location: DataLibraryLocation, replace?: boolean) => void;
}) {
  const [catalog, setCatalog] = useState<ApiDataLifecycleCatalog | null>(null);
  const selectedId = location.connectorId ?? "";
  const selectedStage = location.stage ?? "";
  const selectedVersionId = location.revisionId ?? "";
  const [detail, setDetail] = useState<ApiConnectorLifecycleDetail | null>(null);
  const [rawPage, setRawPage] = useState<ApiRawSnapshotRowPage | null>(null);
  const [curationPage, setCurationPage] = useState<ApiCurationRunRowPage | null>(null);
  const [overridePage, setOverridePage] = useState<ApiCurationRunRowPage | null>(null);
  const [reasonPage, setReasonPage] = useState<ApiCurationRunRowPage | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [connectorName, setConnectorName] = useState("");
  const [sourceLocator, setSourceLocator] = useState("");
  const [primaryKey, setPrimaryKey] = useState("");
  const [objectContent, setObjectContent] = useState("");
  const [objectVersion, setObjectVersion] = useState("");
  const [credential, setCredential] = useState("");
  const [expectedContentDigest, setExpectedContentDigest] = useState("");
  const [expectedRowCount, setExpectedRowCount] = useState("");
  const [recipeName, setRecipeName] = useState("");
  const [numberFields, setNumberFields] = useState("");
  const [requiredFields, setRequiredFields] = useState("");
  const [targetFields, setTargetFields] = useState("");
  const [profileId, setProfileId] = useState("");
  const [recipeId, setRecipeId] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [trainingPurpose, setTrainingPurpose] = useState("");
  const [trainingGroupField, setTrainingGroupField] = useState("");
  const [trainingFolds, setTrainingFolds] = useState("2");
  const [overrideRowKeys, setOverrideRowKeys] = useState<string[]>([]);
  const [overrideReasons, setOverrideReasons] = useState<Record<string, string>>({});
  const selectedIdRef = useRef("");
  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);
  const selectConnector = useCallback((connectorId: string) => {
    // Keep the request guard in sync before React commits the selection.
    // A slow response for the previous connector can otherwise win the
    // click-to-render race and briefly replace the newly selected detail.
    selectedIdRef.current = connectorId;
    onNavigate({ tab: "update", connectorId });
  }, [onNavigate]);

  const profiles = useMemo(() => {
    const seen = new Set<string>();
    return datasets.map((item) => item.profile_revision).filter((profile) => {
      if (seen.has(profile.id)) return false;
      seen.add(profile.id);
      return true;
    });
  }, [datasets]);

  const refreshCatalog = useCallback(async () => {
    const next = await workbenchApi.dataLifecycleCatalog();
    setCatalog(next);
    setRecipeId((current) => next.recipes.some((item) => item.id === current) ? current : next.recipes.at(-1)?.id ?? "");
  }, []);

  const refreshDetail = useCallback(async (
    connectorId: string,
    signal?: AbortSignal,
  ) => {
    if (!connectorId) {
      setDetail(null);
      return;
    }
    const loaded = await workbenchApi.sourceConnectorDetail(connectorId, signal);
    if (!signal?.aborted && selectedIdRef.current === connectorId) {
      setDetail(loaded);
    }
  }, []);

  useEffect(() => {
    refreshCatalog().catch((cause) => setError(cause instanceof Error ? cause.message : "データ更新履歴を取得できませんでした。"));
  }, [refreshCatalog]);

  useEffect(() => {
    if (!catalog) return;
    if (!selectedId && catalog.connectors[0]) {
      onNavigate({ tab: "update", connectorId: catalog.connectors[0].id }, true);
    }
  }, [catalog, onNavigate, selectedId]);

  useEffect(() => {
    const controller = new AbortController();
    setCredential("");
    setExpectedContentDigest("");
    setExpectedRowCount("");
    setOverrideRowKeys([]);
    setOverrideReasons({});
    setApprovalReason("");
    setTrainingPurpose("");
    setDetail(null);
    setRawPage(null);
    setCurationPage(null);
    setOverridePage(null);
    setReasonPage(null);
    setTrainingGroupField("");
    setTrainingFolds("2");
    refreshDetail(selectedId, controller.signal).catch((cause) => {
      if (!controller.signal.aborted && selectedIdRef.current === selectedId) {
        setError(cause instanceof Error ? cause.message : "接続先を取得できませんでした。");
      }
    });
    return () => controller.abort();
  }, [refreshDetail, selectedId]);

  useEffect(() => {
    if (!detail || !selectedStage) return;
    const versions = selectedStage === "raw"
      ? detail.raw_snapshots
      : selectedStage === "curation"
        ? detail.curation_runs
        : selectedStage === "approval"
          ? detail.canonical_revisions
          : detail.training_snapshots;
    if (!versions.some((item) => item.id === selectedVersionId)) {
      onNavigate({
        tab: "update",
        connectorId: selectedId,
        stage: selectedStage,
        revisionId: versions.at(-1)?.id,
      }, true);
    }
  }, [detail, onNavigate, selectedId, selectedStage, selectedVersionId]);

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
  const latestRawNeedsCuration = Boolean(
    latestRaw
    && !detail?.curation_runs.some((item) => item.raw_snapshot_id === latestRaw.id),
  );
  const latestRunNeedsApproval = Boolean(
    latestRun
    && !detail?.canonical_revisions.some(
      (item) => item.curation_run_id === latestRun.id,
    ),
  );
  const latestRevisionNeedsTraining = Boolean(
    latestRevision
    && !detail?.training_snapshots.some(
      (item) => item.canonical_dataset_revision_id === latestRevision.id,
    ),
  );
  const selectedRaw = detail?.raw_snapshots.find((item) => item.id === selectedVersionId);
  const selectedRun = detail?.curation_runs.find((item) => item.id === selectedVersionId);
  const selectedRevision = detail?.canonical_revisions.find((item) => item.id === selectedVersionId);
  const selectedTraining = detail?.training_snapshots.find((item) => item.id === selectedVersionId);
  useEffect(() => {
    const controller = new AbortController();
    if (!selectedRaw) {
      setRawPage(null);
      return () => controller.abort();
    }
    workbenchApi.rawSnapshotRows(selectedRaw.id, 0, 50, controller.signal)
      .then((page) => {
        if (!controller.signal.aborted) setRawPage(page);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "取得行を読み込めませんでした。");
        }
      });
    return () => controller.abort();
  }, [selectedRaw]);

  useEffect(() => {
    const controller = new AbortController();
    if (!selectedRun) {
      setCurationPage(null);
      return () => controller.abort();
    }
    workbenchApi.curationRunRows(selectedRun.id, 0, 100, controller.signal)
      .then((page) => {
        if (!controller.signal.aborted) setCurationPage(page);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "品質判定行を読み込めませんでした。");
        }
    });
    return () => controller.abort();
  }, [selectedRun]);

  useEffect(() => {
    const controller = new AbortController();
    if (!latestRunNeedsApproval || !latestRun?.quality.quarantined) {
      setOverridePage(null);
      return () => controller.abort();
    }
    workbenchApi.curationRunRows(
      latestRun.id,
      0,
      200,
      controller.signal,
      "quarantined",
    )
      .then((page) => {
        if (!controller.signal.aborted) setOverridePage(page);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "隔離行を読み込めませんでした。");
        }
      });
    return () => controller.abort();
  }, [latestRun?.id, latestRun?.quality.quarantined, latestRunNeedsApproval]);

  useEffect(() => {
    const controller = new AbortController();
    const reasonCount = latestRun
      ? latestRun.quality.warning
        + latestRun.quality.quarantined
        + latestRun.quality.blocked
      : 0;
    if (!latestRun || reasonCount === 0) {
      setReasonPage(null);
      return () => controller.abort();
    }
    workbenchApi.curationRunRows(
      latestRun.id,
      0,
      200,
      controller.signal,
      undefined,
      true,
    )
      .then((page) => {
        if (!controller.signal.aborted) setReasonPage(page);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "理由付きの行を読み込めませんでした。");
        }
      });
    return () => controller.abort();
  }, [
    latestRun?.id,
    latestRun?.quality.blocked,
    latestRun?.quality.quarantined,
    latestRun?.quality.warning,
  ]);
  const trainingRecipeId = trainingRecipeIdForRevision(
    latestRevision?.curation_run_id,
    detail?.curation_runs ?? [],
  );
  const trainingRecipe = catalog?.recipes.find(
    (item) => item.id === trainingRecipeId,
  );
  const trainingTargetFields = collectTrainingTargetFields(
    trainingRecipe?.steps ?? [],
  );
  const resolvedTrainingGroupField = trainingGroupField.trim()
    || detail?.connector.selection.primary_key
    || "";
  const resolvedTrainingFolds = Number(trainingFolds);
  const selectedProfile = profiles.find((item) => item.id === profileId);
  const currentActor = catalog?.current_actor;
  const locatorFetchSupported = Boolean(
    detail
    && (
      detail.connector.source_locator.startsWith("file://")
      || /^[A-Za-z]:[\\/]/.test(detail.connector.source_locator)
    ),
  );
  const overrideCandidates = overridePage && overridePage.resource_id === latestRun?.id
    ? overridePage.rows
    : [];
  const overrideReasonMissing = overrideRowKeys.some((rowKey) => !overrideReasons[rowKey]?.trim());
  const approvalBlocked = busy === "approve"
    || (overrideRowKeys.length > 0 && (!approvalReason.trim() || overrideReasonMissing));

  const actorLabel = (actor: string) => actor === currentActor?.id ? currentActor.label : actor;
  const reasonLabel = (reason: keyof typeof reasonLabels) => reasonLabels[reason];

  const selectStage = (stage: LifecycleStage) => {
    if (!detail) return;
    const versions = stage === "raw"
      ? detail.raw_snapshots
      : stage === "curation"
        ? detail.curation_runs
        : stage === "approval"
          ? detail.canonical_revisions
          : detail.training_snapshots;
    onNavigate({
      tab: "update",
      connectorId: selectedId,
      stage,
      revisionId: versions.at(-1)?.id,
    });
  };

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
      await refreshCatalog();
      onNavigate({ tab: "update", connectorId: created.id });
      setNotice("接続先を登録しました。データ取得はまだ実行していません。");
    });
  }

  async function fetchRaw(ingress: "inline" | "source_locator") {
    if (!selectedId) return;
    await act("fetch", async () => {
      const oneTimeCredential = credential;
      setCredential("");
      const fetched = await workbenchApi.fetchSourceConnector(selectedId, {
        schema_version: "source-fetch-request/v1",
        trigger_kind: "manual",
        ingress,
        object_content: ingress === "inline" ? objectContent : null,
        object_version: ingress === "inline" ? objectVersion.trim() : null,
        retry_of: null,
        expected_content_sha256: ingress === "source_locator" && expectedContentDigest.trim()
          ? expectedContentDigest.trim()
          : null,
        expected_row_count: ingress === "source_locator" && expectedRowCount.trim()
          ? Number(expectedRowCount)
          : null,
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
      await refreshCatalog();
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
        targets: trainingTargetFields.map((field) => ({
          target_key: field,
          field,
        })),
        split: {
          strategy_id: "sorted-group-round-robin-v1",
          group_field: resolvedTrainingGroupField,
          folds: resolvedTrainingFolds,
        },
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

  const loadMoreCurationRows = async () => {
    if (!curationPage?.has_more) return;
    const next = await workbenchApi.curationRunRows(
      curationPage.resource_id,
      curationPage.offset + curationPage.rows.length,
      curationPage.limit,
    );
    setCurationPage({
      ...next,
      offset: 0,
      rows: [...curationPage.rows, ...next.rows],
    });
  };

  const loadMoreOverrideRows = async () => {
    if (!overridePage?.has_more) return;
    const next = await workbenchApi.curationRunRows(
      overridePage.resource_id,
      overridePage.offset + overridePage.rows.length,
      overridePage.limit,
      undefined,
      "quarantined",
    );
    setOverridePage({
      ...next,
      offset: 0,
      rows: [...overridePage.rows, ...next.rows],
    });
  };

  const loadMoreReasonRows = async () => {
    if (!reasonPage?.has_more) return;
    const next = await workbenchApi.curationRunRows(
      reasonPage.resource_id,
      reasonPage.offset + reasonPage.rows.length,
      reasonPage.limit,
      undefined,
      undefined,
      true,
    );
    setReasonPage({
      ...next,
      offset: 0,
      rows: [...reasonPage.rows, ...next.rows],
    });
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
        {catalog?.connectors.map((connector) => <button type="button" key={connector.id} className={connector.id === selectedId ? "active" : ""} onClick={() => selectConnector(connector.id)}>
          <strong>{connector.name}</strong><span>{connector.source_locator}</span>
        </button>)}
        {catalog?.connectors.length === 0 && <p>接続先はまだありません。</p>}
      </nav>
      {detail ? <div className="source-lifecycle-detail">
        <header><div><strong>{detail.connector.name}</strong><span>{detail.connector.source_locator}</span></div><code>{shortDigest(detail.connector.configuration_digest)}</code></header>
        <ol className="source-stage-rail" aria-label="データ更新の信頼境界">
          <li className={`${latestRaw ? "complete" : "current"} ${selectedStage === "raw" ? "selected" : ""}`}><button type="button" aria-pressed={selectedStage === "raw"} onClick={() => selectStage("raw")}><b>1</b><span>取得スナップショット<small>{detail.raw_snapshots.length}版</small></span></button></li>
          <li className={`${latestRun ? "complete" : latestRaw ? "current" : ""} ${selectedStage === "curation" ? "selected" : ""}`}><button type="button" aria-pressed={selectedStage === "curation"} onClick={() => selectStage("curation")}><b>2</b><span>品質判定<small>{detail.curation_runs.length}版</small></span></button></li>
          <li className={`${latestRevision ? "complete" : latestRun ? "current" : ""} ${selectedStage === "approval" ? "selected" : ""}`}><button type="button" aria-pressed={selectedStage === "approval"} onClick={() => selectStage("approval")}><b>3</b><span>承認<small>{detail.canonical_revisions.length}版</small></span></button></li>
          <li className={`${latestTraining ? "complete" : latestRevision ? "current" : ""} ${selectedStage === "training" ? "selected" : ""}`}><button type="button" aria-pressed={selectedStage === "training"} onClick={() => selectStage("training")}><b>4</b><span>学習用スナップショット<small>{detail.training_snapshots.length}版</small></span></button></li>
        </ol>
        {selectedStage && <section className="source-history" aria-label="データ更新の版履歴">
          <div className="source-history-list">
            <strong>{selectedStage === "raw" ? "取得" : selectedStage === "curation" ? "品質判定" : selectedStage === "approval" ? "承認" : "学習用"}の版</strong>
            {(selectedStage === "raw"
              ? detail.raw_snapshots
              : selectedStage === "curation"
                ? detail.curation_runs
                : selectedStage === "approval"
                  ? detail.canonical_revisions
                  : detail.training_snapshots
            ).map((item, index, versions) => {
              const timestamp = "captured_at" in item ? item.captured_at : "approved_at" in item ? item.approved_at : item.created_at;
              const digest = lifecycleDigest(item);
              return <button type="button" key={item.id} className={item.id === selectedVersionId ? "active" : ""} aria-pressed={item.id === selectedVersionId} onClick={() => onNavigate({ tab: "update", connectorId: selectedId, stage: selectedStage, revisionId: item.id })}>
                <span>v{index + 1}{index === versions.length - 1 && " · 最新"}</span>
                <time dateTime={timestamp}>{formatTimestamp(timestamp)}</time>
                <code>{shortDigest(digest)}</code>
              </button>;
            }).reverse()}
            {(selectedStage === "raw" ? detail.raw_snapshots : selectedStage === "curation" ? detail.curation_runs : selectedStage === "approval" ? detail.canonical_revisions : detail.training_snapshots).length === 0 && <span>この段階の版はありません。</span>}
          </div>
          <div className="source-history-detail">
            {selectedRaw && <>
              <header><strong>取得スナップショット v{detail.raw_snapshots.indexOf(selectedRaw) + 1}</strong><code>{shortDigest(selectedRaw.snapshot_digest)}</code></header>
              <dl><div><dt>取得日時</dt><dd>{formatTimestamp(selectedRaw.captured_at)}</dd></div><div><dt>Source版</dt><dd>{selectedRaw.object_version}</dd></div><div><dt>行数</dt><dd>{selectedRaw.row_count}</dd></div><div><dt>差分</dt><dd>追加 +{selectedRaw.diff.added_rows} / 変更 {selectedRaw.diff.changed_rows} / 消失 -{selectedRaw.diff.removed_rows}</dd></div></dl>
              {rawPage?.resource_id === selectedRaw.id && <p>{rawPage.rows.length} / {rawPage.total}行を遅延取得済み</p>}
            </>}
            {selectedRun && <>
              <header><strong>品質判定 v{detail.curation_runs.indexOf(selectedRun) + 1}</strong><code>{shortDigest(selectedRun.curation_digest)}</code></header>
              <div className="source-history-quality"><span>採用 <b>{selectedRun.quality.accepted}</b></span><span>注意 <b>{selectedRun.quality.warning}</b></span><span>隔離 <b>{selectedRun.quality.quarantined}</b></span><span>停止 <b>{selectedRun.quality.blocked}</b></span></div>
              <p className="source-quality-meaning"><b>隔離</b>は該当行を除いて次へ進めます。<b>停止</b>は入力として成立せず、その行を承認候補にしません。</p>
              <div className="source-history-rows">{curationPage?.resource_id === selectedRun.id && curationPage.rows.filter((row) => row.reason_codes.length).map((row) => <p key={row.row_key}><b>{row.row_key}</b><span>{row.status === "blocked" ? "停止" : row.status === "quarantined" ? "隔離" : row.status === "warning" ? "注意" : "採用"} · {row.reason_codes.map((code) => reasonLabel(code)).join(" / ")}</span></p>)}</div>
              {curationPage?.resource_id === selectedRun.id && curationPage.has_more && <button className="outline-button" type="button" onClick={() => void loadMoreCurationRows()}>次の行を読み込む</button>}
            </>}
            {selectedRevision && <>
              <header><strong>承認 v{detail.canonical_revisions.indexOf(selectedRevision) + 1}</strong><code>{shortDigest(selectedRevision.dataset_digest)}</code></header>
              <dl><div><dt>承認日時</dt><dd>{formatTimestamp(selectedRevision.approved_at)}</dd></div><div><dt>承認者</dt><dd>{actorLabel(selectedRevision.actor)}</dd></div><div><dt>承認理由</dt><dd>{selectedRevision.reason || "理由の記録なし"}</dd></div><div><dt>採用 / 除外</dt><dd>{selectedRevision.approved_row_count} / {selectedRevision.excluded_row_count}行</dd></div></dl>
              <div className="source-history-overrides"><strong>上書き</strong><span>{selectedRevision.override_count ? `${selectedRevision.override_count}行` : "上書きなし"}</span></div>
            </>}
            {selectedTraining && <>
              <header><strong>学習用スナップショット v{detail.training_snapshots.indexOf(selectedTraining) + 1}</strong><code>{shortDigest(selectedTraining.snapshot_digest)}</code></header>
              <dl><div><dt>契約</dt><dd>{selectedTraining.schema_version}</dd></div><div><dt>作成日時</dt><dd>{formatTimestamp(selectedTraining.created_at)}</dd></div><div><dt>作成者</dt><dd>{actorLabel(selectedTraining.actor)}</dd></div><div><dt>用途</dt><dd>{selectedTraining.purpose}</dd></div><div><dt>Snapshot採用 / 対象外</dt><dd>{selectedTraining.included_row_count} / {selectedTraining.excluded_row_count}行</dd></div>{selectedTraining.split && <><div><dt>分割group field</dt><dd>{selectedTraining.split.group_field}</dd></div><div><dt>分割</dt><dd>{selectedTraining.split.strategy_id} · {selectedTraining.split.folds} fold</dd></div></>}</dl>
              <section className="training-selection-audit" aria-label="学習行の選択方針">
                <header>
                  <strong>学習行の選択方針</strong>
                  {selectedTraining.selection_policy_digest && <code title={selectedTraining.selection_policy_digest}>{shortDigest(selectedTraining.selection_policy_digest)}</code>}
                </header>
                {selectedTraining.selection_policy
                  ? <p><b>{selectedTraining.selection_policy.policy_id}</b> · revision {selectedTraining.selection_policy.revision}</p>
                  : <p>{selectedTraining.schema_version === "approved-training-snapshot/v1"
                    ? "旧契約のため、選択方針は記録されていません。"
                    : "追加の除外ルールはありません。"}</p>}
                <div className="training-selection-counts">
                  <span>承認済み <b>{selectedTraining.approved_row_count}</b></span>
                  <span>Snapshot採用 <b>{selectedTraining.included_row_count}</b></span>
                  <span>Snapshot対象外 <b>{selectedTraining.excluded_row_count}</b></span>
                </div>
                {selectedTraining.selection_policy && <p>policyによる追加除外 <b>{selectedTraining.policy_excluded_row_count}行</b></p>}
                {selectedTraining.exclusion_reasons.length > 0
                  ? <ul>{selectedTraining.exclusion_reasons.map((reason) => <li key={reason.code}>
                    <span>{reason.label}</span><b>{reason.count}行</b>
                  </li>)}</ul>
                  : <small>追加除外された行はありません。</small>}
                {selectedTraining.exclusion_reasons.length > 1
                  && <small>1行が複数ルールに当たる場合、理由別件数では重複して数えます。</small>}
              </section>
              {selectedTraining.target_cohorts.length
                ? <div className="source-history-cohorts"><strong>target別cohortとsplit</strong>{selectedTraining.target_cohorts.map((cohort) => <details key={cohort.target_key}>
                  <summary>{cohort.target_field} · 採用 {cohort.row_count} / 対象外 {cohort.excluded_row_count}行</summary>
                  <dl><div><dt>cohort digest</dt><dd><code>{shortDigest(cohort.cohort_digest)}</code></dd></div><div><dt>split digest</dt><dd><code>{shortDigest(cohort.split_digest)}</code></dd></div></dl>
                  <p>{cohort.split_group_count} groupの割当を固定</p>
                </details>)}</div>
                : <p>旧契約のため、target別cohortとsplit割当は記録されていません。</p>}
            </>}
          </div>
        </section>}
        {locatorFetchSupported && <details className="source-action-panel" open={!latestRaw}>
          <summary>登録した場所から取得</summary>
          <p>Connectorに登録したファイルを直接読み込みます。内容はrequestへ展開せず、読込時にSHA-256と行数を検証します。</p>
          <div className="source-action-grid">
            <label>期待するSHA-256（任意）<input value={expectedContentDigest} onChange={(event) => setExpectedContentDigest(event.target.value)} /></label>
            <label>期待する行数（任意）<input type="number" min="0" step="1" value={expectedRowCount} onChange={(event) => setExpectedRowCount(event.target.value)} /></label>
            <label>一時認証情報<input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} /></label>
          </div>
          <button className="primary-button" type="button" disabled={busy === "fetch" || (expectedRowCount.trim() !== "" && (!Number.isInteger(Number(expectedRowCount)) || Number(expectedRowCount) < 0))} onClick={() => void fetchRaw("source_locator")}>{busy === "fetch" ? "取得中…" : "登録した場所から取得"}</button>
        </details>}
        <details className="source-action-panel source-validation-mode">
          <summary>検証モード：JSONを直接入力</summary>
          <p>接続確認用です。入力内容と認証情報は初期表示せず、認証情報は保存しません。</p>
          <div className="source-action-grid">
            <label>元データの版<input value={objectVersion} onChange={(event) => setObjectVersion(event.target.value)} /></label>
            <label>一時認証情報<input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} /></label>
          </div>
          <label>JSONデータ<textarea rows={5} value={objectContent} onChange={(event) => setObjectContent(event.target.value)} /></label>
          <button className="outline-button" type="button" disabled={!objectVersion.trim() || !objectContent.trim() || busy === "fetch"} onClick={() => void fetchRaw("inline")}>{busy === "fetch" ? "取得中…" : "検証データを取得"}</button>
        </details>
        {latestRaw && <div className="source-snapshot-summary">
          <div><span>最新の取得版</span><strong>{latestRaw.row_count}行</strong><code>{shortDigest(latestRaw.snapshot_digest)}</code>{latestRaw.source_byte_count && <small>{latestRaw.source_byte_count.toLocaleString("ja-JP")} bytes</small>}</div>
          <dl><div><dt>追加</dt><dd>+{latestRaw.diff.added_rows}</dd></div><div><dt>変更</dt><dd>{latestRaw.diff.changed_rows}</dd></div><div><dt>消失</dt><dd>-{latestRaw.diff.removed_rows}</dd></div></dl>
        </div>}
        <details className="source-action-panel" open={latestRawNeedsCuration}>
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
          <details><summary>理由付きの行</summary>{reasonPage?.resource_id === latestRun.id && reasonPage.rows.map((row) => <p key={row.row_key}><b>{row.row_key}</b><span>{row.status === "blocked" ? "停止 · " : row.status === "quarantined" ? "隔離 · " : row.status === "warning" ? "注意 · " : ""}{row.reason_codes.map((code) => reasonLabel(code)).join(" / ")}</span><em>{row.target_eligible ? "目的変数として利用可" : "目的変数として利用不可"}</em></p>)}
            {reasonPage?.resource_id === latestRun.id && reasonPage.has_more && <button className="outline-button" type="button" onClick={() => void loadMoreReasonRows()}>次の理由付き行を読み込む</button>}
          </details>
          {latestRun.quality_delta.comparable && <p className="source-quality-delta">前回比　採用 {latestRun.quality_delta.accepted_delta >= 0 ? "+" : ""}{latestRun.quality_delta.accepted_delta} / 注意 {latestRun.quality_delta.warning_delta >= 0 ? "+" : ""}{latestRun.quality_delta.warning_delta} / 隔離 {latestRun.quality_delta.quarantined_delta >= 0 ? "+" : ""}{latestRun.quality_delta.quarantined_delta}</p>}
        </div>}
        {latestRunNeedsApproval && latestRun && <div className="source-approval-block">
          {overrideCandidates.length > 0 && <details className="source-override-panel">
            <summary>判定を上書きして採用する行を選ぶ</summary>
            <p>上書きする場合は、行ごとの根拠と全体の承認理由が必須です。</p>
            {overrideCandidates.map((row) => <div key={row.row_key}>
              <label><input type="checkbox" checked={overrideRowKeys.includes(row.row_key)} onChange={() => toggleOverride(row.row_key)} />{row.row_key} · {row.reason_codes.map((code) => reasonLabel(code)).join(" / ")}</label>
              {overrideRowKeys.includes(row.row_key) && <label>この行を採用する根拠<input aria-label={`${row.row_key}の上書き理由`} value={overrideReasons[row.row_key] ?? ""} onChange={(event) => setOverrideReasons((current) => ({ ...current, [row.row_key]: event.target.value }))} /></label>}
            </div>)}
            {overridePage?.has_more && <button className="outline-button" type="button" onClick={() => void loadMoreOverrideRows()}>次の隔離行を読み込む</button>}
          </details>}
          <div className="source-approval-action">
            <div className="source-actor"><span>記録される主体</span><strong>{currentActor?.label ?? "確認中…"}</strong><small>{currentActor?.id}</small></div>
            <label>承認理由{overrideRowKeys.length > 0 && "（必須）"}<input value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} placeholder={overrideRowKeys.length > 0 ? "上書きを含めて承認する根拠" : "任意"} /></label>
            <button className="primary-button" type="button" disabled={approvalBlocked} onClick={() => void approve()}>{overrideRowKeys.length > 0 ? "上書きを含めて承認" : latestRun.quality.quarantined ? "隔離行を除いて承認" : "正規データセットを承認"}</button>
          </div>
        </div>}
        {latestRevisionNeedsTraining && latestRevision && <div className="source-approval-action">
          <div className="source-actor"><span>記録される主体</span><strong>{currentActor?.label ?? "確認中…"}</strong><small>{currentActor?.id}</small></div>
          <label>用途<input value={trainingPurpose} onChange={(event) => setTrainingPurpose(event.target.value)} placeholder="例: 再学習候補の比較" /></label>
          <label>分割group field<input value={trainingGroupField} onChange={(event) => setTrainingGroupField(event.target.value)} placeholder={detail.connector.selection.primary_key ?? "例: lot_id"} /></label>
          <label>fold数<input type="number" min="2" step="1" value={trainingFolds} onChange={(event) => setTrainingFolds(event.target.value)} /></label>
          <button className="primary-button" type="button" disabled={busy === "training" || trainingTargetFields.length === 0 || !resolvedTrainingGroupField || !Number.isInteger(resolvedTrainingFolds) || resolvedTrainingFolds < 2} onClick={() => void createTraining()}>学習用スナップショットを作成</button>
        </div>}
        {latestTraining && <div className="source-training-ready"><strong>学習用スナップショット作成済み</strong><span>{latestTraining.row_count}行 · {latestTraining.purpose}</span><code>{shortDigest(latestTraining.snapshot_digest)}</code><small>再学習・モデル検証・有効化は別の操作です。</small></div>}
      </div> : <div className="source-empty">接続先を登録すると、更新履歴と承認状態をここで確認できます。</div>}
    </div>
  </section>;
}
