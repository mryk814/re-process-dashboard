import { useEffect, useMemo, useRef, useState } from "react";
import { useTaskLabels } from "../../shared/useTaskLabels";
import {
  workbenchApi,
  type ApiProfileWorkbenchInspection,
  type ApiProfileWorkbenchProfile,
  type ApiProfileWorkbenchRegistration,
  type ApiProfileWorkbenchDraft,
  type ApiDataLibraryDataset,
} from "../../shared/api/workbench-api";
import { ObservationAuthoringPanel } from "./ObservationAuthoringPanel";

type ProfileBindingSlot = NonNullable<ApiProfileWorkbenchInspection["binding_draft"]>["slots"][number];

const shortDigest = (value: string) => value.slice(0, 12);

function headerUnit(column: string): string {
  const bracketed = column.match(/\[([^\[\]]+)\]\s*$/)?.[1];
  if (bracketed) return bracketed;
  return column.endsWith("%") && column.length > 1 ? "%" : "";
}

function initialSourceUnit(slot: ProfileBindingSlot, sourceName: string): string {
  if (!slot.canonical_unit || !sourceName) return "";
  const declared = headerUnit(sourceName);
  if (declared) return declared;
  return sourceName === slot.expected_source_name ? (slot.source_unit ?? "") : "";
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString("ja-JP")} KB`;
  }
  return `${(bytes / 1024 / 1024).toLocaleString("ja-JP", { maximumFractionDigits: 1 })} MB`;
}

function previewValue(value: unknown, key: string): string {
  if (typeof value !== "object" || value === null) return "—";
  const selected = Reflect.get(value, key);
  return typeof selected === "string" ? selected : "—";
}

function previewFields(value: unknown): string {
  if (typeof value !== "object" || value === null) return "—";
  const fields = Reflect.get(value, "values");
  if (typeof fields !== "object" || fields === null) return "—";
  const keys = Object.keys(fields);
  return keys.length ? `${keys.slice(0, 4).join(" / ")}${keys.length > 4 ? ` ほか${keys.length - 4}項目` : ""}` : "値なし";
}

const bindingKindLabels = {
  entity_key: "キー",
  relation_join: "relation",
  input: "入力",
  output: "実測・出力",
  technical: "補助情報",
  policy: "採用判定",
  series: "系列",
} as const;

export function ProfileWorkbenchPage({
  onOpenDataLibrary,
  onStartProject,
}: {
  onOpenDataLibrary: () => void;
  onStartProject: (datasetViewRevisionId: string) => void;
}) {
  const [profiles, setProfiles] = useState<ApiProfileWorkbenchProfile[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [profileSelection, setProfileSelection] = useState("auto");
  const [datasetName, setDatasetName] = useState("");
  const [inspection, setInspection] = useState<ApiProfileWorkbenchInspection | null>(null);
  const [registration, setRegistration] = useState<ApiProfileWorkbenchRegistration | null>(null);
  const [savedDraft, setSavedDraft] = useState<ApiProfileWorkbenchDraft | null>(null);
  const [draftBindings, setDraftBindings] = useState<Record<string, string>>({});
  const [draftUnits, setDraftUnits] = useState<Record<string, string>>({});
  const [confirmedSlots, setConfirmedSlots] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState("");
  const params = new URLSearchParams(window.location.search);
  const onboardingMode = params.get("onboarding") === "revision" ? "revision" : "mapping";
  const baseDatasetRevisionId = params.get("base_dataset") ?? "";
  const [baseDataset, setBaseDataset] = useState<ApiDataLibraryDataset | null>(null);
  const inspectController = useRef<AbortController | null>(null);
  const taskLabel = useTaskLabels();

  async function reloadProfiles(signal?: AbortSignal) {
    const items = await workbenchApi.listProfileWorkbenchProfiles();
    if (!signal?.aborted) setProfiles(items);
    return items;
  }

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      reloadProfiles(controller.signal),
      onboardingMode === "revision" && baseDatasetRevisionId
        ? workbenchApi.listDataLibraryDatasets(true)
        : Promise.resolve([]),
    ])
      .then(([, datasets]) => {
        if (controller.signal.aborted) return;
        const base = datasets.find(
          (item) => item.dataset_revision.id === baseDatasetRevisionId,
        ) ?? null;
        setBaseDataset(base);
        if (onboardingMode === "revision") {
          if (!base) {
            setError("更新元Datasetを確認できません。データライブラリから選び直してください。");
          } else {
            setProfileSelection(base.profile_revision.profile_digest);
          }
        }
      })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Dataset Profileを取得できませんでした。"); });
    return () => {
      controller.abort();
      inspectController.current?.abort();
    };
  }, []);

  const selectedProfileDigest = inspection?.selected_profile_digest
    ?? inspection?.binding_draft?.base_profile_digest
    ?? (profileSelection === "auto" ? undefined : profileSelection);
  const selectedProfile = profiles.find((item) => item.profile_digest === selectedProfileDigest);
  const rejectedTotal = useMemo(
    () => Object.values(inspection?.validation?.rejected_by_policy ?? {}).reduce((sum, count) => sum + count, 0),
    [inspection?.validation?.rejected_by_policy],
  );
  const unresolvedHeatSeries = inspection?.validation?.unresolved_heat_series_by_task ?? {};
  const unresolvedHeatSeriesTotal = Object.values(unresolvedHeatSeries).reduce((sum, count) => sum + count, 0);
  const bindingDraft = inspection?.binding_draft;
  const slotReady = (slot: ProfileBindingSlot) => {
    const sourceName = draftBindings[slot.slot_id] ?? "";
    const sourceUnit = draftUnits[slot.slot_id] ?? "";
    return Boolean(
      confirmedSlots[slot.slot_id]
      && sourceName
      && (
        !slot.canonical_unit
        || (sourceUnit && (slot.source_unit_candidates ?? []).includes(sourceUnit))
      ),
    );
  };
  const pendingDraftSlots = bindingDraft?.slots.filter((slot) => (
    slot.required && !slotReady(slot)
  )) ?? [];
  const canSaveDraft = Boolean(
    file
    && bindingDraft
    && pendingDraftSlots.length === 0
    && !loading
    && !savingDraft
    && !registering,
  );
  const canRegister = Boolean(
    file
    && selectedProfileDigest
    && inspection?.validation?.registration_ready
    && !inspection.profile_error
    && !loading
    && !registering
    && !savingDraft
    && !registration
    && !error,
  );
  // Every step has to be reachable: inspection produces the structure diff and the
  // validation together, so they are one step instead of a stage nothing lands on.
  const currentStep = registration
    ? 6
    : inspection?.validation?.registration_ready && !inspection.profile_error
      ? 5
      : bindingDraft
        ? 3
        : inspection
          ? 4
          : file
            ? 2
            : 1;
  const steps = ["Excel", "Base Profile", "対応付け", "検証", "Dataset登録", "Project作成"];
  const nextAction = registration
    ? "登録したデータセットでプロジェクトを作成するか、データライブラリで確認します。"
    : registering
      ? "データセットを登録しています。"
      : savingDraft
        ? "対応付けたProfileを保存し、同じExcelを再検査しています。"
      : loading
        ? "Excelの構造と選択したデータセットプロファイルを確認しています。"
        : error
          ? "エラー内容を確認し、Excelまたはデータセットプロファイルを選び直します。"
          : bindingDraft && pendingDraftSlots.length > 0
            ? `未確定の対応が${pendingDraftSlots.length}件あります。候補を確認して対応付けます。`
          : bindingDraft
            ? "対応付けをProfileとして保存し、同じExcelを再検査します。"
          : inspection?.profile_error
            ? "Base Profileを選び直すか、対応付けを確認します。"
            : inspection?.validation?.registration_ready
              ? "データセット名を確認して、データライブラリへ登録します。"
              : inspection
                ? "構造差分と検証結果を確認し、必要ならデータセットプロファイルを選び直します。"
                : file
                  ? "データセットプロファイルを選び、Excelの内容を確認します。"
                  : "最初にExcelファイルを選択します。";

  function cancelInspection() {
    inspectController.current?.abort();
    inspectController.current = null;
    setLoading(false);
  }

  function selectFile(next: File | null) {
    cancelInspection();
    setFile(next);
    setProfileSelection(
      onboardingMode === "revision" && baseDataset
        ? baseDataset.profile_revision.profile_digest
        : "auto",
    );
    setDatasetName(next?.name.replace(/\.xlsx$/i, "") ?? "");
    setInspection(null);
    setRegistration(null);
    setSavedDraft(null);
    setDraftBindings({});
    setDraftUnits({});
    setConfirmedSlots({});
    setError("");
  }

  async function inspect() {
    if (!file || loading) return;
    inspectController.current?.abort();
    const controller = new AbortController();
    inspectController.current = controller;
    setLoading(true);
    setError("");
    setInspection(null);
    setRegistration(null);
    try {
      const result = await workbenchApi.inspectProfileWorkbook(
        file,
        profileSelection === "auto" ? undefined : profileSelection,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setInspection(result);
        const initialBindings = Object.fromEntries(
          (result.binding_draft?.slots ?? [])
            .filter((slot) => slot.selected_source_name)
            .map((slot) => [slot.slot_id, slot.selected_source_name as string]),
        );
        const initialConfirmed = Object.fromEntries(
          (result.binding_draft?.slots ?? [])
            .filter((slot) => slot.state === "confirmed")
            .map((slot) => [slot.slot_id, true]),
        );
        const initialUnits = Object.fromEntries(
          (result.binding_draft?.slots ?? [])
            .filter((slot) => slot.selected_source_name && slot.canonical_unit)
            .map((slot) => [
              slot.slot_id,
              initialSourceUnit(slot, slot.selected_source_name as string),
            ]),
        );
        setDraftBindings(initialBindings);
        setDraftUnits(initialUnits);
        setConfirmedSlots(initialConfirmed);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Excelの内容を確認できませんでした。");
    } finally {
      if (inspectController.current === controller) {
        inspectController.current = null;
        setLoading(false);
      }
    }
  }

  function selectProfile(next: string) {
    cancelInspection();
    setProfileSelection(next);
    setInspection(null);
    setRegistration(null);
    setSavedDraft(null);
    setDraftBindings({});
    setDraftUnits({});
    setConfirmedSlots({});
    setError("");
  }

  function bindDraftSlot(
    slot: ProfileBindingSlot,
    sourceName: string,
  ) {
    const nextBindings = { ...draftBindings, [slot.slot_id]: sourceName };
    const nextUnits = {
      ...draftUnits,
      [slot.slot_id]: initialSourceUnit(slot, sourceName),
    };
    const nextConfirmed = { ...confirmedSlots, [slot.slot_id]: Boolean(sourceName) };
    if (slot.binding_type === "sheet" && sourceName && bindingDraft) {
      const headers = new Set(
        inspection?.sheets.find((sheet) => sheet.name === sourceName)?.headers ?? [],
      );
      for (const columnSlot of bindingDraft.slots) {
        if (
          columnSlot.binding_type === "column"
          && columnSlot.role === slot.role
          && headers.has(columnSlot.expected_source_name)
        ) {
          nextBindings[columnSlot.slot_id] = columnSlot.expected_source_name;
          nextUnits[columnSlot.slot_id] = initialSourceUnit(
            columnSlot,
            columnSlot.expected_source_name,
          );
          nextConfirmed[columnSlot.slot_id] = true;
        }
      }
    }
    setDraftBindings(nextBindings);
    setDraftUnits(nextUnits);
    setConfirmedSlots(nextConfirmed);
    setSavedDraft(null);
    setError("");
  }

  function bindingRow(slot: ProfileBindingSlot) {
    const value = draftBindings[slot.slot_id] ?? "";
    const sourceUnit = draftUnits[slot.slot_id] ?? "";
    const declaredUnit = headerUnit(value);
    const unitSupported = !slot.canonical_unit
      || Boolean(sourceUnit && (slot.source_unit_candidates ?? []).includes(sourceUnit));
    const confirmed = slotReady(slot);
    const unitOptions = Array.from(new Set([
      ...(sourceUnit ? [sourceUnit] : []),
      ...(slot.source_unit_candidates ?? []),
    ]));
    return <div className={`${confirmed ? "profile-binding-row confirmed" : "profile-binding-row pending"} ${slot.required ? "required" : "optional"}`} role="row" key={slot.slot_id}>
      <div role="cell" className="profile-binding-target">
        <span>{slot.role} · {slot.binding_type === "sheet" ? "シート役割" : bindingKindLabels[slot.semantic_kind]}</span>
        <strong>{slot.binding_type === "sheet" ? slot.expected_source_name : slot.canonical_name}</strong>
        {!slot.required && <small>補助データ · 未対応でも登録可能</small>}
        {slot.canonical_unit && <small>Excel側単位 → {slot.canonical_unit}</small>}
      </div>
      <span className="profile-binding-arrow" aria-hidden="true">←</span>
      <label role="cell">
        <span>Excel側</span>
        <select
          aria-label={`${slot.canonical_name}のExcel側${slot.binding_type === "sheet" ? "シート" : "列"}`}
          value={value}
          onChange={(event) => bindDraftSlot(slot, event.target.value)}
        >
          <option value="">未解決のまま</option>
          {(slot.candidates ?? []).map((candidate) => <option key={candidate.source_name} value={candidate.source_name}>
            {candidate.source_name}{candidate.score < 1 ? ` · 候補 ${Math.round(candidate.score * 100)}%` : ""}
          </option>)}
        </select>
        {slot.binding_type === "column" && slot.canonical_unit && <select
          aria-label={`${slot.canonical_name}のExcel側単位`}
          value={sourceUnit}
          disabled={!value || Boolean(declaredUnit)}
          onChange={(event) => {
            setDraftUnits({ ...draftUnits, [slot.slot_id]: event.target.value });
            setSavedDraft(null);
            setError("");
          }}
        >
          <option value="">{declaredUnit ? "単位を判定できません" : "単位を選択"}</option>
          {unitOptions.map((unit) => <option key={unit} value={unit}>{unit}</option>)}
        </select>}
        {declaredUnit && <small>列名から検出: {declaredUnit}</small>}
      </label>
      <b className="profile-binding-state">{confirmed ? "確認済み" : value && !unitSupported ? "単位未対応" : value ? "要確認" : "未解決"}</b>
    </div>;
  }

  async function saveDraft() {
    if (!file || !bindingDraft || !canSaveDraft) return;
    setSavingDraft(true);
    setError("");
    try {
      const saved = await workbenchApi.saveProfileWorkbenchDraft(
        file,
        bindingDraft.base_profile_digest,
        bindingDraft.source_sha256,
        bindingDraft.slots.filter(slotReady).map((slot) => ({
          slot_id: slot.slot_id,
          state: "confirmed" as const,
          source_name: draftBindings[slot.slot_id],
          source_unit: slot.canonical_unit ? draftUnits[slot.slot_id] : undefined,
        })),
      );
      setSavedDraft(saved);
      setProfileSelection(saved.profile_digest);
      await reloadProfiles();
      const verified = await workbenchApi.inspectProfileWorkbook(file, saved.profile_digest);
      setInspection(verified);
      const verifiedBindings = Object.fromEntries(
        (verified.binding_draft?.slots ?? [])
          .filter((slot) => slot.selected_source_name)
          .map((slot) => [slot.slot_id, slot.selected_source_name as string]),
      );
      const verifiedUnits = Object.fromEntries(
        (verified.binding_draft?.slots ?? [])
          .filter((slot) => slot.selected_source_name && slot.canonical_unit)
          .map((slot) => [
            slot.slot_id,
            initialSourceUnit(slot, slot.selected_source_name as string),
          ]),
      );
      setDraftBindings(verifiedBindings);
      setDraftUnits(verifiedUnits);
      setConfirmedSlots(Object.fromEntries(
        (verified.binding_draft?.slots ?? [])
          .filter((slot) => slot.state === "confirmed")
          .map((slot) => [slot.slot_id, true]),
      ));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Profile draftを保存できませんでした。");
    } finally {
      setSavingDraft(false);
    }
  }

  async function register() {
    if (!file || !selectedProfileDigest || !inspection || !canRegister) return;
    setRegistering(true);
    setError("");
    try {
      setRegistration(await workbenchApi.registerProfileWorkbook(
        file,
        selectedProfileDigest,
        inspection.source_sha256,
        datasetName,
        onboardingMode === "revision" ? baseDatasetRevisionId : undefined,
      ));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Datasetを登録できませんでした。");
    } finally {
      setRegistering(false);
    }
  }

  return <div className="page-panel profile-workbench-page">
    <div className="page-intro">
      <div><span className="overline">PROFILE WORKBENCH</span><h2>{onboardingMode === "revision" ? "Datasetの更新版を登録" : "既存Taskへデータを対応付け"}</h2><p>{onboardingMode === "revision" ? "元のTaskとProfileを固定したまま、Source差分を新しいDataset Revisionとして確認します。" : "Excelを変更せず、既存Profileとの対応と正規化結果を確認してからData Libraryへ登録します。"}</p></div>
      <button className="outline-button" onClick={onOpenDataLibrary}>データライブラリに戻る</button>
    </div>
    {onboardingMode === "revision" && baseDataset && <aside className="profile-revision-base" aria-label="更新元Dataset">
      <span>更新元</span>
      <strong>{baseDataset.data_asset.original_filename}</strong>
      <code title={baseDataset.data_asset.sha256}>{shortDigest(baseDataset.data_asset.sha256)}</code>
      <small>{baseDataset.profile_revision.name} · r{baseDataset.profile_revision.revision}を引き継ぎます</small>
    </aside>}
    <ol className="profile-workbench-steps" aria-label="Dataset登録からProject作成まで">
      {steps.map((step, index) => <li key={step} aria-current={currentStep === index + 1 ? "step" : undefined} className={currentStep >= index + 1 ? currentStep === index + 1 ? "current" : "complete" : ""}><b>{index + 1}</b><span>{step}</span></li>)}
    </ol>
    <p className="profile-next-action" aria-live="polite"><b>次の操作</b><span>{nextAction}</span></p>
    <aside className="profile-data-purpose">
      <strong>ここで登録するのは参照・探索用Datasetです</strong>
      <span>登録だけならModel Packageの再構築は不要です。学習データとして採用するときは、別途Packageを作成・検証します。</span>
    </aside>

    {onboardingMode === "mapping" && !registration && <ObservationAuthoringPanel onRegistered={(value) => {
      setRegistration(value);
      void reloadProfiles();
    }} />}

    <section className="profile-workbench-inputs" aria-label="ExcelとDataset Profileの選択">
      <label className={file ? "profile-file-picker selected" : "profile-file-picker"}>
        <span><strong>{file?.name ?? "Excelを選択"}</strong><small>{file ? formatFileSize(file.size) : ".xlsx · 100 MB以下"}</small></span>
        <input type="file" disabled={registering || Boolean(registration)} accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onClick={(event) => { event.currentTarget.value = ""; }} onChange={(event) => selectFile(event.target.files?.[0] ?? null)} />
      </label>
      <label className="profile-select-field"><span>データセットプロファイル</span><select value={profileSelection} disabled={registering || Boolean(registration) || onboardingMode === "revision"} onChange={(event) => selectProfile(event.target.value)}>
        <option value="auto">自動検出</option>
        {profiles.map((item) => <option value={item.profile_digest} key={item.profile_digest}>{item.personal ? "自分のProfile · " : ""}{item.profile_id} · {item.source_name.replace("dataset-input-profile-", "")}</option>)}
      </select></label>
      <button className="primary-button profile-inspect-button" disabled={!file || loading || registering || Boolean(registration)} onClick={() => void inspect()}>{loading ? "確認中…" : "内容を確認"}</button>
    </section>

    {error && <p className="panel-error" role="alert">{error}</p>}

    {inspection && <>
      <section className="profile-inspection-summary">
        <header><div><span>確認したExcel</span><strong>{inspection.source_filename}</strong><code title={inspection.source_sha256}>{shortDigest(inspection.source_sha256)}</code></div><div><span>Dataset Profile</span><strong>{selectedProfile?.profile_id ?? "検出できませんでした"}</strong>{inspection.auto_detected && <b>自動検出</b>}</div></header>
        {inspection.profile_error && <div className="profile-validation-error" role="alert"><strong>このままでは登録できません</strong><p>{inspection.profile_error}</p><small>{bindingDraft ? "下の対応表でExcel側の名前を確認してください。" : "Base Profileを選び直して、もう一度「内容を確認」してください。"}</small></div>}
        <div className="profile-candidate-summary">
          <span>Profile候補</span>
          {profiles.filter((item) => onboardingMode !== "revision" || item.profile_digest === baseDataset?.profile_revision.profile_digest).map((item) => <button type="button" key={item.profile_digest} className={item.profile_digest === selectedProfileDigest ? "selected" : ""} disabled={registering || Boolean(registration) || onboardingMode === "revision"} onClick={() => selectProfile(item.profile_digest)}>
            <b>{item.profile_id}{item.personal ? " · 自分のProfile" : ""}</b><small title={item.task_ids.join(" / ")}>{item.task_ids.map(taskLabel).join(" / ")}</small>
          </button>)}
        </div>
        <div className={inspection.profile_error ? "profile-structure-diff mismatch" : "profile-structure-diff match"}>
          <b>{inspection.profile_error ? "構造差分あり" : "必須構造はProfileに対応"}</b>
          <span>{inspection.sheets.length}シートを確認 · 未使用の補助列はDatasetへ残し、予測入力へ自動追加しません。</span>
        </div>
        <div className="profile-sheet-list" aria-label="Workbook inventory">{inspection.sheets.map((sheet) => <article key={sheet.name}><header><strong>{sheet.name}</strong><span>{sheet.rows.toLocaleString("ja-JP")}行</span></header><p title={sheet.headers.join(" / ")}>{sheet.headers.slice(0, 6).join(" / ")}{sheet.headers.length > 6 ? ` / ほか${sheet.headers.length - 6}列` : ""}</p></article>)}</div>
      </section>

      {bindingDraft && <section className="profile-binding-editor" aria-labelledby="profile-binding-title">
        <div className="panel-title">
          <div><span className="overline">SOURCE BINDING</span><h3 id="profile-binding-title">Excel側の名前を対応付ける</h3></div>
          <span className={pendingDraftSlots.length ? "profile-binding-count pending" : "profile-binding-count ready"}>
            {pendingDraftSlots.length ? `未確定 ${pendingDraftSlots.length}件` : "登録に必要な対応は確定"}
          </span>
        </div>
        <p>Taskとrelation構造はBase Profileのままです。提案は自動確定されないため、意味と単位を確認して選択してください。</p>
        {(["sheet", "column"] as const).map((bindingType) => {
          const slots = bindingDraft.slots.filter((slot) => slot.binding_type === bindingType);
          if (!slots.length) return null;
          const needsReview = slots.filter((slot) => (
            !slotReady(slot)
          ));
          const alreadyMatched = slots.filter((slot) => (
            slotReady(slot)
          ));
          return <div className="profile-binding-group" key={bindingType}>
            <h4>{bindingType === "sheet" ? "シートと役割" : "キー・値・単位"}</h4>
            {needsReview.length > 0 && <div className="profile-binding-table" role="table" aria-label={bindingType === "sheet" ? "シート対応" : "列対応"}>
              {needsReview.map(bindingRow)}
            </div>}
            {alreadyMatched.length > 0 && <details className="profile-binding-confirmed">
              <summary>既存名で対応済み {alreadyMatched.length}件</summary>
              <div className="profile-binding-table" role="table" aria-label={bindingType === "sheet" ? "確認済みシート対応" : "確認済み列対応"}>
                {alreadyMatched.map(bindingRow)}
              </div>
            </details>}
          </div>;
        })}
        <div className="profile-binding-actions">
          <span>{pendingDraftSlots.length ? "未確定の対応がある間はDataset登録へ進みません。" : "保存後、同じExcelを新しいProfileで再検査します。"}</span>
          <button className="primary-button" disabled={!canSaveDraft} onClick={() => void saveDraft()}>
            {savingDraft ? "保存・再検査中…" : "Profileを保存して再検査"}
          </button>
        </div>
      </section>}

      {inspection.validation && <section className="profile-validation-result">
        <div className="panel-title"><h3>Canonical preview</h3><span className="profile-ready-badge">登録可能</span></div>
        <div className="profile-validation-metrics"><div><span>Entities</span><strong>{inspection.validation.entities.toLocaleString("ja-JP")}</strong></div><div><span>Relations</span><strong>{inspection.validation.relations.toLocaleString("ja-JP")}</strong></div><div><span>Observations</span><strong>{inspection.validation.observations.toLocaleString("ja-JP")}</strong></div><div><span>Heat series</span><strong>{inspection.validation.heat_series_parents.toLocaleString("ja-JP")}</strong></div></div>
        <div className="profile-task-row"><span>対応する予測タスク</span>{inspection.validation.task_ids.map((task) => <b key={task} title={task}>{taskLabel(task)}</b>)}</div>
        {unresolvedHeatSeriesTotal > 0 && <details className="profile-rejection-details"><summary>ヒートパターンを作れない工程条件: {unresolvedHeatSeriesTotal.toLocaleString("ja-JP")}件</summary>{Object.entries(unresolvedHeatSeries).map(([task, count]) => <span key={task}><code>{task}</code><b>{count.toLocaleString("ja-JP")}件</b></span>)}<p>Datasetには登録できますが、この工程条件はヒートパターンを必要とする学習・候補参照には使われません。</p></details>}
        {rejectedTotal > 0 && <details className="profile-rejection-details"><summary>Eligibilityで除外される観測: {rejectedTotal.toLocaleString("ja-JP")}件</summary>{Object.entries(inspection.validation.rejected_by_policy).map(([policy, count]) => <span key={policy}><code>{policy}</code><b>{count.toLocaleString("ja-JP")}件</b></span>)}</details>}
        {inspection.validation.entity_preview.length > 0 && <details className="profile-preview-details"><summary>正規化後の先頭{inspection.validation.entity_preview.length}件</summary><table><thead><tr><th>Entity</th><th>Key</th><th>Canonical fields</th></tr></thead><tbody>{inspection.validation.entity_preview.map((item, index) => <tr key={`${previewValue(item, "entity_type")}-${previewValue(item, "entity_key")}-${index}`}><td>{previewValue(item, "entity_type")}</td><td>{previewValue(item, "entity_key")}</td><td>{previewFields(item)}</td></tr>)}</tbody></table></details>}
      </section>}

      {savedDraft && <aside className="profile-draft-saved" role="status">
        <div><strong>自分のProfileとして保存しました</strong><span>{savedDraft.profile_id}</span><code>{shortDigest(savedDraft.profile_digest)}</code></div>
        <a className="outline-button" href={workbenchApi.profileWorkbenchExportUrl(savedDraft.profile_digest)}>JSONを出力</a>
      </aside>}

      {inspection.validation?.registration_ready && !inspection.profile_error && !registration && <section className="profile-registration-panel">
        <div><span className="overline">REGISTER DATASET</span><h3>Data Libraryへ登録</h3><p>Excelの内容とProfileの組み合わせを不変のDatasetとして登録します。同じ組み合わせは重複しません。</p></div>
        <label>Dataset名<input value={datasetName} maxLength={160} onChange={(event) => setDatasetName(event.target.value)} /></label>
        <button className="primary-button" disabled={!canRegister} onClick={() => void register()}>{registering ? "登録中…" : "この内容で登録"}</button>
      </section>}
    </>}

    {registration && <section className="profile-registration-success" role="status"><div><strong>{registration.reused_existing ? "既存Datasetを確認しました" : registration.previous_dataset_revision_id ? "更新版を新しいRevisionとして登録しました" : "Data Libraryへ登録しました"}</strong><span>{registration.profile_id} · {registration.task_ids.map(taskLabel).join(" / ")}</span>{registration.previous_source_sha256 && <small className="profile-source-diff"><code>{shortDigest(registration.previous_source_sha256)}</code><b>→</b><code>{shortDigest(registration.source_sha256)}</code></small>}<code>{shortDigest(registration.dataset_revision_id)}</code></div><div className="profile-registration-success-actions"><button className="primary-button" onClick={() => onStartProject(registration.dataset_view_revision_id)}>このDatasetでプロジェクト作成</button><button className="outline-button" onClick={onOpenDataLibrary}>データライブラリで確認</button><button className="text-button" onClick={() => selectFile(null)}>別のExcelを確認</button></div></section>}
  </div>;
}
