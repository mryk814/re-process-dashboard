import type { RefObject } from "react";
import { ModelPackageDecisionCard } from "../../shared/ui/ModelPackageDecisionCard";
import type { ApiModelPackageRef } from "../../shared/api/workbench-api";
import {
  projectCreationSubmitDisabled,
  type ProjectCreationMode,
  type ProjectGroupChoice,
} from "./projectCreationState";

export type ProjectCreationChoice = {
  id: string;
  label: string;
};

export type ProjectCreationBindingSummary = {
  dataset: { label: string; detail: string; detailTitle?: string };
  prediction: { label: string; detail: string };
  package: { label: string; detail: string };
  group: { label: string; detail: string };
};

export type PreparedBindingReview = {
  dataset: { label: string; revision: string; viewRevision: string };
  source: { filename: string; sha256: string };
  task: { label: string; id: string; contractDigest: string };
  modelPackage: { label: string; refId: string; manifestDigest: string };
  estimator: { label: string; id: string; capabilities: string[] };
  preparationResult: "new" | "reused";
  workspace: { kind: string; databasePath: string };
  blockers: string[];
};

export type ProjectCreationPanelProps = {
  open: boolean;
  loading: boolean;
  disabled: boolean;
  error: string;
  preparedBindingReview?: PreparedBindingReview;
  manualBindingSelectionOpen: boolean;
  projectNameInputRef: RefObject<HTMLInputElement | null>;
  projectName: string;
  datasetViewId: string;
  predictionConfiguration: string;
  chainId: string;
  chainRevisionId: string;
  modelPackageRefId: string;
  mode: ProjectCreationMode;
  groupChoice: ProjectGroupChoice;
  projectSeriesId: string;
  projectSeriesName: string;
  showContinuationReason: boolean;
  continuationReason: string;
  copyTaskId?: string;
  copyDisabled: boolean;
  copyDescription: string;
  usedDatasetChoices: ProjectCreationChoice[];
  unusedDatasetChoices: ProjectCreationChoice[];
  predictionConfigurationChoices: ProjectCreationChoice[];
  chainRevisionChoices: ProjectCreationChoice[];
  modelPackageChoices: ProjectCreationChoice[];
  activeProjectSeries: ProjectCreationChoice[];
  modelPackage?: ApiModelPackageRef;
  bindingSummary: ProjectCreationBindingSummary;
  onClose: () => void;
  onProjectNameChange: (name: string) => void;
  onDatasetChange: (datasetViewId: string) => void;
  onPredictionConfigurationChange: (selection: string) => void;
  onChainRevisionChange: (chainRevisionId: string) => void;
  onModelPackageChange: (modelPackageRefId: string) => void;
  onGroupChoiceChange: (choice: ProjectGroupChoice) => void;
  onProjectSeriesChange: (projectSeriesId: string) => void;
  onProjectSeriesNameChange: (projectSeriesName: string) => void;
  onContinuationReasonChange: (reason: string) => void;
  onModeChange: (mode: ProjectCreationMode) => void;
  onOpenManualBindingSelection: () => void;
  onRestorePreparedBinding: () => void;
  onSubmit: () => void | Promise<void>;
};

export function ProjectCreationPanel({
  open,
  loading,
  disabled,
  error,
  preparedBindingReview,
  manualBindingSelectionOpen,
  projectNameInputRef,
  projectName,
  datasetViewId,
  predictionConfiguration,
  chainId,
  chainRevisionId,
  modelPackageRefId,
  mode,
  groupChoice,
  projectSeriesId,
  projectSeriesName,
  showContinuationReason,
  continuationReason,
  copyTaskId,
  copyDisabled,
  copyDescription,
  usedDatasetChoices,
  unusedDatasetChoices,
  predictionConfigurationChoices,
  chainRevisionChoices,
  modelPackageChoices,
  activeProjectSeries,
  modelPackage,
  bindingSummary,
  onClose,
  onProjectNameChange,
  onDatasetChange,
  onPredictionConfigurationChange,
  onChainRevisionChange,
  onModelPackageChange,
  onGroupChoiceChange,
  onProjectSeriesChange,
  onProjectSeriesNameChange,
  onContinuationReasonChange,
  onModeChange,
  onOpenManualBindingSelection,
  onRestorePreparedBinding,
  onSubmit,
}: ProjectCreationPanelProps) {
  if (!open) return null;

  const submitDisabled = projectCreationSubmitDisabled({
    loading,
    disabled,
    projectName,
    datasetViewId,
    mode,
    copyTaskId,
    taskId: predictionConfiguration.startsWith("task:")
      ? predictionConfiguration.slice("task:".length)
      : "",
    modelPackageRefId,
    chainId,
    chainRevisionId,
    groupChoice,
    projectSeriesId,
    projectSeriesName,
  });
  const selectedChain = Boolean(chainId);
  const preparedReviewMode = Boolean(preparedBindingReview && !manualBindingSelectionOpen);
  const preparedBindingBlocked = Boolean(preparedReviewMode && preparedBindingReview!.blockers.length);

  return <section className="project-create-panel" aria-label="新規プロジェクトの開始方法" aria-busy={loading}>
    <div className="panel-title project-create-heading">
      <div><h3>新しいプロジェクト</h3><span>開始方法を選んでから作成します</span></div>
      <button type="button" className="outline-button" disabled={loading} onClick={onClose}>作成をやめる</button>
    </div>
    {error && <p className="panel-error" role="alert">{error}</p>}
    {preparedBindingReview && <section className="project-preparation-receipt" aria-label="準備済みbindingの確認" role="status">
      <header>
        <strong>準備済みbindingを確認</strong>
        <span>{preparedBindingReview.preparationResult === "reused" ? "検証済みの既存resourceを再利用しました" : "新しいresourceを準備し、再読込しました"}</span>
      </header>
      <dl>
        <div><dt>Dataset</dt><dd><strong>{preparedBindingReview.dataset.label}</strong><code>{preparedBindingReview.dataset.revision}</code><small>view: {preparedBindingReview.dataset.viewRevision}</small></dd></div>
        <div><dt>Source / content</dt><dd><strong>{preparedBindingReview.source.filename}</strong><code>{preparedBindingReview.source.sha256}</code></dd></div>
        <div><dt>Prediction Task</dt><dd><strong>{preparedBindingReview.task.label}</strong><code>{preparedBindingReview.task.id}</code><small>contract: {preparedBindingReview.task.contractDigest}</small></dd></div>
        <div><dt>Model Package</dt><dd><strong>{preparedBindingReview.modelPackage.label}</strong><code>{preparedBindingReview.modelPackage.refId}</code><small>manifest: {preparedBindingReview.modelPackage.manifestDigest}</small></dd></div>
        <div><dt>Estimator / capability</dt><dd><strong>{preparedBindingReview.estimator.label}</strong><code>{preparedBindingReview.estimator.id}</code><small>{preparedBindingReview.estimator.capabilities.join(" / ") || "point capabilityの記録なし"}</small></dd></div>
        <div><dt>準備結果</dt><dd><strong>{preparedBindingReview.preparationResult === "reused" ? "reused" : "new"}</strong></dd></div>
        <div><dt>Workspace保存先</dt><dd><strong>{preparedBindingReview.workspace.kind}</strong><code>{preparedBindingReview.workspace.databasePath}</code></dd></div>
      </dl>
      {preparedBindingReview.blockers.length > 0 && <div className="project-prepared-binding-error" role="alert">
        <strong>このbindingは現在のWorkspaceで解決できません</strong>
        <ul>{preparedBindingReview.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>
      </div>}
      {preparedReviewMode
        ? <button type="button" className="outline-button" disabled={loading} onClick={onOpenManualBindingSelection}>別のデータ・Task・モデルを選ぶ</button>
        : <button type="button" className="outline-button" disabled={loading} onClick={onRestorePreparedBinding}>準備済みbindingへ戻す</button>}
    </section>}
    <label>プロジェクト名<input ref={projectNameInputRef} value={projectName} onChange={(event) => onProjectNameChange(event.target.value)} placeholder="例: 2026年7月 焼鈍条件の再検討" /></label>
    {!preparedReviewMode && <div className="project-binding-flow">
      <label className="project-dataset-choice"><b aria-hidden="true">1</b><span>Dataset</span><select disabled={mode === "copy"} value={datasetViewId} onChange={(event) => onDatasetChange(event.target.value)}><option value="">選択してください</option>{usedDatasetChoices.length > 0 && <optgroup label="利用中のデータ">{usedDatasetChoices.map((choice) => <option key={choice.id} value={choice.id}>{choice.label}</option>)}</optgroup>}{unusedDatasetChoices.length > 0 && <optgroup label="未使用のデータ">{unusedDatasetChoices.map((choice) => <option key={choice.id} value={choice.id}>{choice.label}</option>)}</optgroup>}</select><small aria-hidden="true">利用中のProject数が多い順。同数なら新しい登録順。</small></label>
      <label><b aria-hidden="true">2</b><span>予測構成</span><select disabled={mode === "copy" || !datasetViewId} value={predictionConfiguration} onChange={(event) => onPredictionConfigurationChange(event.target.value)}><option value="">{datasetViewId ? "選択してください" : "先にDatasetを選択"}</option>{predictionConfigurationChoices.map((choice) => <option key={choice.id} value={choice.id}>{choice.label}</option>)}</select></label>
      <label><b aria-hidden="true">3</b><span>{selectedChain ? "Chain Revision" : "Model Package"}</span>{selectedChain ? <select disabled={!chainId} value={chainRevisionId} onChange={(event) => onChainRevisionChange(event.target.value)}><option value="">Revisionを選択</option>{chainRevisionChoices.map((choice) => <option key={choice.id} value={choice.id}>{choice.label}</option>)}</select> : <select disabled={mode === "copy" || !predictionConfiguration} value={modelPackageRefId} onChange={(event) => onModelPackageChange(event.target.value)}><option value="">{predictionConfiguration ? "手法を選択してください" : "先にPrediction Taskを選択"}</option>{modelPackageChoices.map((choice) => <option key={choice.id} value={choice.id}>{choice.label}</option>)}</select>}</label>
      <fieldset className="project-group-choice">
        <legend><b aria-hidden="true">4</b><span>検討グループ</span></legend>
        <p>同じ目的で続けた複数の検討をまとめます。続き元の関係とは別です。</p>
        <div>
          <label><input type="radio" name="project-group-choice" checked={groupChoice === "none"} onChange={() => onGroupChoiceChange("none")} />グループなし<span>既定。単独のプロジェクトとして作成</span></label>
          <label><input type="radio" name="project-group-choice" checked={groupChoice === "existing"} disabled={activeProjectSeries.length === 0} onChange={() => onGroupChoiceChange("existing")} />既存グループ<span>{activeProjectSeries.length ? "ほかの検討と同じまとまりに追加" : "追加できるグループがありません"}</span></label>
          <label><input type="radio" name="project-group-choice" checked={groupChoice === "new"} onChange={() => onGroupChoiceChange("new")} />新しい検討グループ<span>名前を付けて新しいまとまりを作成</span></label>
        </div>
        {groupChoice === "existing" && <label>追加する検討グループ<select value={projectSeriesId} onChange={(event) => onProjectSeriesChange(event.target.value)}><option value="">選択してください</option>{activeProjectSeries.map((series) => <option key={series.id} value={series.id}>{series.label}</option>)}</select></label>}
        {groupChoice === "new" && <label>新しい検討グループ名<input required value={projectSeriesName} onChange={(event) => onProjectSeriesNameChange(event.target.value)} placeholder="例: 焼鈍条件の再検討" /></label>}
      </fieldset>
    </div>}
    {!preparedReviewMode && modelPackage && !selectedChain && <ModelPackageDecisionCard modelPackage={modelPackage} />}
    {!preparedReviewMode && <section className="project-binding-confirmation" aria-label="作成後に固定される内容">
      <header><strong>作成後に固定される内容</strong><span>{selectedChain ? "Chain Revisionと各StageのPackage・Dataset・Profileは後から変わりません" : "Dataset・Prediction Task・Model Packageは後から変更できません"}</span></header>
      <div><span>参照Dataset</span><strong>{bindingSummary.dataset.label}</strong><small title={bindingSummary.dataset.detailTitle}>{bindingSummary.dataset.detail}</small></div>
      <div><span>{selectedChain ? "Chain Template" : "Prediction Task"}</span><strong>{bindingSummary.prediction.label}</strong><small>{bindingSummary.prediction.detail}</small></div>
      <div><span>{selectedChain ? "Chain Revision" : "Model Package"}</span><strong>{bindingSummary.package.label}</strong><small>{bindingSummary.package.detail}</small></div>
    </section>}
    {!preparedReviewMode && <div className="project-group-summary"><span>検討グループ</span><strong>{bindingSummary.group.label}</strong><small>{bindingSummary.group.detail}</small></div>}
    {!preparedReviewMode && showContinuationReason && <label>続ける理由（任意）<textarea value={continuationReason} onChange={(event) => onContinuationReasonChange(event.target.value)} placeholder="予測タスク変更、データ追加、条件変更、判断の再検討など" /></label>}
    {!preparedReviewMode && <div className="project-start-options">
      <label><input type="radio" checked={mode === "empty"} onChange={() => onModeChange("empty")} />空から開始<span>候補を持たない検討として作成</span></label>
      <label><input type="radio" checked={mode === "copy"} disabled={copyDisabled} onChange={() => onModeChange("copy")} />現在候補をコピー<span>{copyDescription}</span></label>
    </div>}
    <button className="primary-button" disabled={submitDisabled || preparedBindingBlocked} onClick={() => void onSubmit()}>{loading ? "作成中…" : preparedReviewMode ? "この組合せでProjectを作成" : "固定してプロジェクトを作成"}</button>
  </section>;
}
