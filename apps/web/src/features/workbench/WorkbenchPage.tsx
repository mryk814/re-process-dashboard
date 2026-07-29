import { type CSSProperties, useEffect, useRef, useState } from "react";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import {
  CandidateInspector,
  ComparisonTable,
  type CandidateSaveState,
  type CandidateViewModel as Candidate,
  type HeatTimeBasis,
  type NumericRange,
  type RuntimeOperations,
  type ApplicationCapability,
  type TaskDefinitionContract,
} from "../candidates";
import { CandidateFileControls, CandidateOrigin } from "./CandidateWorkspaceControls";
import {
  clampLayoutValue,
  saveLayoutNumber,
  SplitResizer,
  storedLayoutNumber,
  workbenchLayoutStorage,
} from "./WorkbenchLayout";
import { apiBaseUrl } from "../../shared/api/client";
import type { TargetGoal } from "../../shared/targetGoals";
import {
  type ApiCandidate,
  type ApiProject,
  type ApiPreview,
} from "../../shared/api/workbench-api";
import { SimilarityEvidencePanel } from "./SimilarityEvidencePanel";
import { FeatureEngineeringPanel } from "./FeatureEngineeringPanel";
import { DecisionActivityPanel } from "./DecisionActivityPanel";
import { ActualMeasurementPanel } from "./ActualMeasurementPanel";
import { BlendComparisonPanel } from "./BlendComparisonPanel";
import { BlendOptimizationPanel } from "./BlendOptimizationPanel";
import { BlendEditorPanel } from "./BlendEditorPanel";
import { HeatPattern } from "./HeatPatternPanel";
import { activityToggleLabel, type CandidateSection } from "../../shared/projectActionQuestions";
import {
  CurveFamilyPanel,
  LiveResponseCurves,
  type ResponseCurveRanges,
} from "./ResponseCurvePanels";

export function WorkbenchEmptyState({
  loading,
  error,
  errorHint,
  onCreate,
}: {
  loading: boolean;
  error: string | null;
  errorHint?: string | null;
  onCreate: () => void;
}) {
  return (
    <div className="api-empty-state" role={error ? "alert" : "status"}>
      <h2>{loading ? "候補を読み込んでいます" : "候補を表示できません"}</h2>
      <p>{error ?? "データと予測モデルを準備しています。"}</p>
      {error && errorHint !== null && (
        <p className="api-hint">
          {errorHint ?? <>FastAPI を <code>{apiBaseUrl}</code> で起動後、再読み込みしてください。</>}
        </p>
      )}
      {!loading && !error && (
        <CandidateAddButton onClick={onCreate}>
          最初の候補を作る
        </CandidateAddButton>
      )}
    </div>
  );
}

type WorkbenchProps = {
  candidates: Candidate[];
  projectId: string;
  project: ApiProject | null;
  targetValues: Record<string, TargetGoal>;
  inputRanges: Record<string, NumericRange>;
  responseCurveRanges: ResponseCurveRanges;
  decisionCandidateId: string;
  selected: Candidate;
  selectedId: string;
  taskDefinition: TaskDefinitionContract | null;
  operations?: RuntimeOperations;
  application?: ApplicationCapability;
  saveState: CandidateSaveState;
  saveStates: Record<string, CandidateSaveState>;
  fieldErrors: Array<{ path: string; message: string }>;
  onReload: () => void;
  onCopyDraft: () => void;
  preview: ApiPreview | null;
  previewError: string;
  onRetryPreview: () => void;
  previewsByCandidate: Record<string, ApiPreview>;
  onSelect: (id: string) => void;
  onHeat: (index: number, field: "time" | "temperature" | "stageName", raw: number | string) => void;
  onHeatTimeBasis: (candidateId: string, basis: HeatTimeBasis) => void;
  onInput: (id: string, path: string, value: number | string | undefined) => void;
  onText: (id: string, field: "label", value: string) => void;
  onBlend: (
    id: string,
    blend: NonNullable<Candidate["raw"]["blend"]>,
    lockedMaterialIds?: string[],
  ) => void;
  onBlendLocks: (id: string, lockedMaterialIds: string[]) => void;
  onAddHeat: () => void;
  onDeleteHeat: (index: number) => void;
  onCopy: (candidateId: string) => void;
  onOpenOrigin: () => void;
  originBroken: boolean;
  onDelete: (candidateId: string) => void;
  onSave: (candidate: Candidate) => void;
  savedRevisionsByCandidate: Record<string, number[]>;
  savingCandidateIds: string[];
  snapshotHistoryState: "loading" | "ready" | "error";
  onAdd: () => void;
  onAddCandidateFromLineage: (
    entityKey: string,
    processKey?: string,
    meltKey?: string,
  ) => Promise<boolean>;
  onImported: (items: Candidate[]) => void;
  onOptimizedCandidate: (candidate: ApiCandidate) => void;
  onProjectChanged: (project: ApiProject) => void | Promise<void>;
  onConfigureGoals: () => void;
  onConfigureSupport: () => void;
  activityId?: string;
  activityRunId?: string;
  candidateSection?: CandidateSection;
  onActivityStateChange: (activityId?: string, activityRunId?: string) => void;
  previewAvailable: boolean;
  pendingPreviewCount: number;
  loadingRemainingPreviews: boolean;
  onLoadRemainingPreviews: () => void;
};

export function WorkbenchPage(props: WorkbenchProps) {
  const {
    candidates,
    projectId,
    project,
    targetValues,
    inputRanges,
    responseCurveRanges,
    decisionCandidateId,
    selected,
    selectedId,
    taskDefinition,
    operations,
    application,
    saveState,
    saveStates,
    fieldErrors,
    onReload,
    onCopyDraft,
    preview,
    previewError,
    onRetryPreview,
    previewsByCandidate,
    onSelect,
    onInput,
    onText,
    onBlend,
    onBlendLocks,
    onHeat,
    onHeatTimeBasis,
    onAddHeat,
    onDeleteHeat,
    onCopy,
    onOpenOrigin,
    originBroken,
    onDelete,
    onSave,
    savedRevisionsByCandidate,
    savingCandidateIds,
    snapshotHistoryState,
    onAdd,
    onAddCandidateFromLineage,
    onImported,
    onOptimizedCandidate,
    onProjectChanged,
    onConfigureGoals,
    onConfigureSupport,
    activityId,
    activityRunId,
    candidateSection,
    onActivityStateChange,
    previewAvailable,
    pendingPreviewCount,
    loadingRemainingPreviews,
    onLoadRemainingPreviews,
  } = props;
  const [activityOpen, setActivityOpen] = useState(false);
  // A shared link to a saved run opens the panel without a second click.
  const activityPanelOpen = activityOpen || Boolean(activityId || activityRunId);
  const [inspectorWidth, setInspectorWidth] = useState(() => clampLayoutValue(storedLayoutNumber(workbenchLayoutStorage.inspectorWidth, 330), 260, 520));
  const [inspectorMax, setInspectorMax] = useState(520);
  const [curveShare, setCurveShare] = useState(() => clampLayoutValue(storedLayoutNumber(workbenchLayoutStorage.curveShare, 50), 30, 70));
  const [comparisonHeight, setComparisonHeight] = useState(() => clampLayoutValue(storedLayoutNumber(workbenchLayoutStorage.comparisonHeight, 270), 180, 900));
  const [curveShareRange, setCurveShareRange] = useState({ min: 30, max: 70 });
  const workbenchRef = useRef<HTMLDivElement>(null);
  const lowerPanelsRef = useRef<HTMLDivElement>(null);
  const actualMeasurementRef = useRef<HTMLDivElement>(null);
  const effectiveInspectorWidth = clampLayoutValue(inspectorWidth, 260, inspectorMax);
  const effectiveCurveShare = clampLayoutValue(curveShare, curveShareRange.min, curveShareRange.max);
  useEffect(() => {
    if (candidateSection !== "actuals" || !operations?.actual_measurement) return;
    actualMeasurementRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [candidateSection, operations?.actual_measurement, selected.id]);
  useEffect(() => saveLayoutNumber(workbenchLayoutStorage.inspectorWidth, inspectorWidth), [inspectorWidth]);
  useEffect(() => saveLayoutNumber(workbenchLayoutStorage.curveShare, curveShare), [curveShare]);
  useEffect(() => saveLayoutNumber(workbenchLayoutStorage.comparisonHeight, comparisonHeight), [comparisonHeight]);
  useEffect(() => {
    const updateWidths = () => {
      const workbenchWidth = workbenchRef.current?.clientWidth ?? 0;
      if (workbenchWidth > 0) {
        const nextMax = Math.max(260, Math.min(520, workbenchWidth - 569));
        setInspectorMax(nextMax);
      }
      const lowerWidth = lowerPanelsRef.current?.clientWidth ?? 0;
      if (lowerWidth > 0) {
        const minPanelWidth = 340;
        const nextMin = Math.min(50, (minPanelWidth / lowerWidth) * 100);
        const nextMax = Math.max(50, ((lowerWidth - 9 - minPanelWidth) / lowerWidth) * 100);
        const nextRange = { min: nextMin, max: nextMax };
        setCurveShareRange(nextRange);
      }
    };
    const observer = new ResizeObserver(updateWidths);
    if (workbenchRef.current) observer.observe(workbenchRef.current);
    if (lowerPanelsRef.current) observer.observe(lowerPanelsRef.current);
    updateWidths();
    return () => observer.disconnect();
  }, []);
  return (
    <div
      ref={workbenchRef}
      className={`workbench-grid candidate-workbench-grid${taskDefinition ? " has-inspector" : ""}`}
      style={{ "--candidate-inspector-width": `${effectiveInspectorWidth}px` } as CSSProperties}
    >
      {taskDefinition && <CandidateInspector
        candidate={selected}
        taskDefinition={taskDefinition}
        saveState={saveState}
        inputRanges={inputRanges}
        fieldErrors={fieldErrors}
        onInput={(path, value) => onInput(selected.id, path, value)}
        onReload={onReload}
        onCopyDraft={onCopyDraft}
        heatPattern={taskDefinition.input_groups.some((group) => group.key === "heat_pattern") ? <HeatPattern candidates={candidates} candidate={selected} onTimeBasisChange={(basis) => onHeatTimeBasis(selected.id, basis)} onUpdate={onHeat} onAdd={onAddHeat} onDelete={onDeleteHeat} /> : undefined}
      />}
      {taskDefinition && <SplitResizer
        className="candidate-inspector-resizer"
        label="選択候補の入力パネル幅を調整"
        value={effectiveInspectorWidth}
        min={260}
        max={inspectorMax}
        step={10}
        onChange={setInspectorWidth}
        onDrag={(startValue, deltaX) => startValue + deltaX}
        onReset={() => setInspectorWidth(330)}
      />}
      <section className="central-workspace">
        <div className="table-heading">
          <div className="table-title">
            <h2>
              候補比較表 <span>（セルを直接編集）</span>
            </h2>
          </div>
          {previewError && <span className="comparison-preview-error" role="alert">{previewError}{operations?.preview && <button type="button" onClick={onRetryPreview}>再試行</button>}</span>}
          <div className="comparison-actions" aria-label="候補操作">
            <button type="button" className="comparison-panel-toggle" aria-expanded={activityPanelOpen} onClick={() => {
              if (activityPanelOpen) {
                setActivityOpen(false);
                onActivityStateChange(undefined, undefined);
                return;
              }
              setActivityOpen(true);
            }}>{activityToggleLabel(activityId, activityPanelOpen)}</button>
            <div className="comparison-data-actions">
              <CandidateFileControls projectId={projectId} capability={application} onImported={onImported} />
              <CandidateAddButton onClick={onAdd}>候補を追加</CandidateAddButton>
            </div>
          </div>
        </div>
        <CandidateOrigin
          projectId={projectId}
          candidate={selected}
          outputs={taskDefinition?.outputs ?? []}
          taskDefinition={taskDefinition}
          displayDecimalOverrides={project?.display_decimals}
          broken={originBroken}
          onOpen={onOpenOrigin}
        />
        {(selected.raw.blend || application?.sparse_blend) && <BlendEditorPanel
          projectId={projectId}
          candidate={selected}
          transformId={application?.sparse_blend_transform_id ?? undefined}
          onBlend={onBlend}
          onLocks={onBlendLocks}
        />}
        {taskDefinition && <ComparisonTable
          projectId={projectId}
          candidates={candidates}
          selectedId={selectedId}
          comparisonHeight={comparisonHeight}
          taskDefinition={taskDefinition}
          previewsByCandidate={previewsByCandidate}
          targetValues={targetValues}
          displayDecimalOverrides={project?.display_decimals}
          decisionCandidateId={decisionCandidateId}
          detailedPredictionAvailable={operations?.detailed_prediction === true}
          saveStates={saveStates}
          savedRevisionsByCandidate={savedRevisionsByCandidate}
          savingCandidateIds={savingCandidateIds}
          snapshotHistoryState={snapshotHistoryState}
          onSelect={onSelect}
          onInput={onInput}
          onName={(id, value) => onText(id, "label", value)}
          onCopy={onCopy}
          onDelete={onDelete}
          onSave={onSave}
          onConfigureGoals={onConfigureGoals}
          onConfigureSupport={onConfigureSupport}
          pendingPreviewCount={previewAvailable ? pendingPreviewCount : 0}
          loadingRemainingPreviews={loadingRemainingPreviews}
          onLoadRemainingPreviews={onLoadRemainingPreviews}
        />}
        {taskDefinition && <SplitResizer
          className="comparison-height-resizer"
          label="候補比較表の高さを調整"
          value={comparisonHeight}
          min={180}
          max={900}
          step={20}
          orientation="horizontal"
          onChange={setComparisonHeight}
          onDrag={(startValue, deltaY) => startValue + deltaY}
          onReset={() => setComparisonHeight(270)}
        />}
        <BlendComparisonPanel
          projectId={projectId}
          candidates={candidates}
          selected={selected}
        />
        <BlendOptimizationPanel
          projectId={projectId}
          candidate={selected.raw}
          onCandidateCreated={onOptimizedCandidate}
        />
        {taskDefinition && operations?.actual_measurement && <div ref={actualMeasurementRef} id="candidate-actuals">
          <ActualMeasurementPanel
            projectId={projectId}
            candidate={selected}
            taskDefinition={taskDefinition}
            displayDecimalOverrides={project?.display_decimals}
            ready={["idle", "saved"].includes(saveState)}
          />
        </div>}
        {taskDefinition?.curve_axis_path && operations?.response_curve ? (
          <CurveFamilyPanel
            projectId={projectId}
            candidate={selected}
            taskDefinition={taskDefinition}
            targetValues={targetValues}
            ready={["idle", "saved"].includes(saveState)}
          />
        ) : null}
        {activityPanelOpen && taskDefinition && <DecisionActivityPanel
          projectId={projectId}
          candidate={selected}
          candidates={candidates}
          taskDefinition={taskDefinition}
          displayDecimalOverrides={project?.display_decimals}
          targetValues={targetValues}
          ready={["idle", "saved"].includes(saveState)}
          requestedActivityId={activityId}
          requestedRunId={activityRunId}
          onStateChange={onActivityStateChange}
          onConfigureGoals={onConfigureGoals}
          onCandidateCreated={onOptimizedCandidate}
          onClose={() => {
            setActivityOpen(false);
            onActivityStateChange(undefined, undefined);
          }}
        />}
        <div
          ref={lowerPanelsRef}
          className={`workbench-lower-grid${operations?.response_curve ? "" : " no-response-curves"}`}
          style={{ "--response-curve-share": `${effectiveCurveShare}%` } as CSSProperties}
        >
          {operations?.response_curve && (
              <LiveResponseCurves
              projectId={projectId}
              project={project}
              candidates={candidates}
              candidate={selected}
              preview={preview}
              previewsByCandidate={previewsByCandidate}
              targetValues={targetValues}
              taskDefinition={taskDefinition}
              responseCurveRanges={responseCurveRanges}
              onProjectChanged={onProjectChanged}
              available
              ready={["idle", "saved"].includes(saveState)}
            />
          )}
          {operations?.response_curve && <SplitResizer
            className="lower-panel-resizer"
            label="応答曲線と近い過去実績の幅を調整"
            value={effectiveCurveShare}
            min={curveShareRange.min}
            max={curveShareRange.max}
            step={2}
            onChange={setCurveShare}
            onDrag={(startValue, deltaX) => startValue + (deltaX / Math.max(lowerPanelsRef.current?.clientWidth ?? 1, 1)) * 100}
            onReset={() => setCurveShare(50)}
          />}
          <SimilarityEvidencePanel projectId={projectId} candidate={selected} outputs={taskDefinition?.outputs ?? []} taskDefinition={taskDefinition} displayDecimalOverrides={project?.display_decimals} available={operations?.similarity === true} targetSpecific={operations?.target_specific_similarity === true} ready={["idle", "saved"].includes(saveState)} onAddCandidate={onAddCandidateFromLineage} />
        </div>
        <FeatureEngineeringPanel preview={preview} />
      </section>
    </div>
  );
}
