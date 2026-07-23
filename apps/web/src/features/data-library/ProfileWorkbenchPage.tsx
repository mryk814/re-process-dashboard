import { useEffect, useMemo, useRef, useState } from "react";
import {
  workbenchApi,
  type ApiProfileWorkbenchInspection,
  type ApiProfileWorkbenchProfile,
  type ApiProfileWorkbenchRegistration,
} from "../../shared/api/workbench-api";

const shortDigest = (value: string) => value.slice(0, 12);

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
  const [loading, setLoading] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState("");
  const inspectController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    workbenchApi.listProfileWorkbenchProfiles()
      .then((items) => { if (!controller.signal.aborted) setProfiles(items); })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Dataset Profileを取得できませんでした。"); });
    return () => {
      controller.abort();
      inspectController.current?.abort();
    };
  }, []);

  const selectedProfileDigest = inspection?.selected_profile_digest ?? (profileSelection === "auto" ? undefined : profileSelection);
  const selectedProfile = profiles.find((item) => item.profile_digest === selectedProfileDigest);
  const rejectedTotal = useMemo(
    () => Object.values(inspection?.validation?.rejected_by_policy ?? {}).reduce((sum, count) => sum + count, 0),
    [inspection?.validation?.rejected_by_policy],
  );
  const canRegister = Boolean(
    file
    && selectedProfileDigest
    && inspection?.validation?.registration_ready
    && !inspection.profile_error
    && !loading
    && !registering
    && !registration
    && !error,
  );

  function cancelInspection() {
    inspectController.current?.abort();
    inspectController.current = null;
    setLoading(false);
  }

  function selectFile(next: File | null) {
    cancelInspection();
    setFile(next);
    setProfileSelection("auto");
    setDatasetName(next?.name.replace(/\.xlsx$/i, "") ?? "");
    setInspection(null);
    setRegistration(null);
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
      if (!controller.signal.aborted) setInspection(result);
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
    setError("");
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
      ));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Datasetを登録できませんでした。");
    } finally {
      setRegistering(false);
    }
  }

  return <div className="page-panel profile-workbench-page">
    <div className="page-intro">
      <div><span className="overline">PROFILE WORKBENCH</span><h2>新しいDatasetを準備</h2><p>Excelを変更せず、既存Profileとの対応と正規化結果を確認してからData Libraryへ登録します。</p></div>
      <button className="outline-button" onClick={onOpenDataLibrary}>データライブラリに戻る</button>
    </div>

    <section className="profile-workbench-inputs" aria-label="ExcelとDataset Profileの選択">
      <label className={file ? "profile-file-picker selected" : "profile-file-picker"}>
        <b>1</b><span><strong>{file?.name ?? "Excelを選択"}</strong><small>{file ? `${(file.size / 1024 / 1024).toLocaleString("ja-JP", { maximumFractionDigits: 1 })} MB` : ".xlsx · 100 MB以下"}</small></span>
        <input type="file" disabled={registering || Boolean(registration)} accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onClick={(event) => { event.currentTarget.value = ""; }} onChange={(event) => selectFile(event.target.files?.[0] ?? null)} />
      </label>
      <label className="profile-select-field"><b>2</b><span>Dataset Profile</span><select value={profileSelection} disabled={registering || Boolean(registration)} onChange={(event) => selectProfile(event.target.value)}>
        <option value="auto">自動検出</option>
        {profiles.map((item) => <option value={item.profile_digest} key={item.profile_digest}>{item.profile_id} · {item.source_name.replace("dataset-input-profile-", "")}</option>)}
      </select></label>
      <button className="primary-button profile-inspect-button" disabled={!file || loading || registering || Boolean(registration)} onClick={() => void inspect()}>{loading ? "確認中…" : "3  内容を確認"}</button>
    </section>

    {error && <p className="panel-error" role="alert">{error}</p>}

    {inspection && <>
      <section className="profile-inspection-summary">
        <header><div><span>確認したExcel</span><strong>{inspection.source_filename}</strong><code title={inspection.source_sha256}>{shortDigest(inspection.source_sha256)}</code></div><div><span>Dataset Profile</span><strong>{selectedProfile?.profile_id ?? "検出できませんでした"}</strong>{inspection.auto_detected && <b>自動検出</b>}</div></header>
        {inspection.profile_error && <div className="profile-validation-error" role="alert"><strong>このProfileでは登録できません</strong><p>{inspection.profile_error}</p><small>Profileを選び直して、もう一度「内容を確認」してください。</small></div>}
        <div className="profile-sheet-list" aria-label="Workbook inventory">{inspection.sheets.map((sheet) => <article key={sheet.name}><header><strong>{sheet.name}</strong><span>{sheet.rows.toLocaleString("ja-JP")}行</span></header><p title={sheet.headers.join(" / ")}>{sheet.headers.slice(0, 6).join(" / ")}{sheet.headers.length > 6 ? ` / ほか${sheet.headers.length - 6}列` : ""}</p></article>)}</div>
      </section>

      {inspection.validation && <section className="profile-validation-result">
        <div className="panel-title"><h3>Canonical preview</h3><span className="profile-ready-badge">登録可能</span></div>
        <div className="profile-validation-metrics"><div><span>Entities</span><strong>{inspection.validation.entities.toLocaleString("ja-JP")}</strong></div><div><span>Relations</span><strong>{inspection.validation.relations.toLocaleString("ja-JP")}</strong></div><div><span>Observations</span><strong>{inspection.validation.observations.toLocaleString("ja-JP")}</strong></div><div><span>Heat series</span><strong>{inspection.validation.heat_series_parents.toLocaleString("ja-JP")}</strong></div></div>
        <div className="profile-task-row"><span>対応Prediction Task</span>{inspection.validation.task_ids.map((task) => <b key={task}>{task}</b>)}</div>
        {rejectedTotal > 0 && <details className="profile-rejection-details"><summary>Eligibilityで除外される観測: {rejectedTotal.toLocaleString("ja-JP")}件</summary>{Object.entries(inspection.validation.rejected_by_policy).map(([policy, count]) => <span key={policy}><code>{policy}</code><b>{count.toLocaleString("ja-JP")}件</b></span>)}</details>}
        {inspection.validation.entity_preview.length > 0 && <details className="profile-preview-details"><summary>正規化後の先頭{inspection.validation.entity_preview.length}件</summary><table><thead><tr><th>Entity</th><th>Key</th><th>Canonical fields</th></tr></thead><tbody>{inspection.validation.entity_preview.map((item, index) => <tr key={`${previewValue(item, "entity_type")}-${previewValue(item, "entity_key")}-${index}`}><td>{previewValue(item, "entity_type")}</td><td>{previewValue(item, "entity_key")}</td><td>{previewFields(item)}</td></tr>)}</tbody></table></details>}
      </section>}

      {inspection.validation?.registration_ready && !inspection.profile_error && !registration && <section className="profile-registration-panel">
        <div><span className="overline">REGISTER DATASET</span><h3>Data Libraryへ登録</h3><p>Excelの内容とProfileの組み合わせを不変のDatasetとして登録します。同じ組み合わせは重複しません。</p></div>
        <label>Dataset名<input value={datasetName} maxLength={160} onChange={(event) => setDatasetName(event.target.value)} /></label>
        <button className="primary-button" disabled={!canRegister} onClick={() => void register()}>{registering ? "登録中…" : "4  この内容で登録"}</button>
      </section>}
    </>}

    {registration && <section className="profile-registration-success" role="status"><div><strong>{registration.reused_existing ? "既存Datasetを確認しました" : "Data Libraryへ登録しました"}</strong><span>{registration.profile_id} · {registration.task_ids.join(" / ")}</span><code>{shortDigest(registration.dataset_revision_id)}</code></div><div className="profile-registration-success-actions"><button className="primary-button" onClick={() => onStartProject(registration.dataset_view_revision_id)}>このDatasetでプロジェクト作成</button><button className="outline-button" onClick={onOpenDataLibrary}>データライブラリで確認</button><button className="text-button" onClick={() => selectFile(null)}>別のExcelを確認</button></div></section>}
  </div>;
}
