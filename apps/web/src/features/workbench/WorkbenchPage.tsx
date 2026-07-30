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
  saveLayoutBoolean,
  saveLayoutNumber,
  SplitResizer,
  storedLayoutBoolean,
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
import { ResponseContourPanel } from "./ResponseContourPanel";
import { PredictionSpacePanel } from "./PredictionSpacePanel";
import {
  workbenchSurfaceRegistry,
  workbenchSurfacesInZone,
  type WorkbenchSurface,
  type WorkbenchSurfaceKind,
} from "./workbenchSurfaceRegistry";

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
  const [activePrimarySurface, setActivePrimarySurface] =
    useState<WorkbenchSurfaceKind>("response_curve");
  // A shared link to a saved run opens the panel without a second click.
  const activityPanelOpen = activityOpen || Boolean(activityId || activityRunId);
  const [inspectorWidth, setInspectorWidth] = useState(() => clampLayoutValue(storedLayoutNumber(workbenchLayoutStorage.inspectorWidth, 330), 260, 520));
  const [inspectorDragWidth, setInspectorDragWidth] = useState<number | null>(null);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(() => storedLayoutBoolean(workbenchLayoutStorage.inspectorCollapsed, false));
  const [inspectorMax, setInspectorMax] = useState(520);
  const [curveShare, setCurveShare] = useState(() => clampLayoutValue(storedLayoutNumber(workbenchLayoutStorage.curveShare, 50), 30, 70));
  const [comparisonHeight, setComparisonHeight] = useState(() => clampLayoutValue(storedLayoutNumber(workbenchLayoutStorage.comparisonHeight, 270), 180, 900));
  const [curveShareRange, setCurveShareRange] = useState({ min: 30, max: 70 });
  const workbenchRef = useRef<HTMLDivElement>(null);
  const lowerPanelsRef = useRef<HTMLDivElement>(null);
  const actualMeasurementRef = useRef<HTMLDivElement>(null);
  const inspectorCollapseButtonRef = useRef<HTMLButtonElement>(null);
  const inspectorExpandButtonRef = useRef<HTMLButtonElement>(null);
  const inspectorFocusTarget = useRef<"collapse" | "expand" | null>(null);
  const collapsedInspectorWidth = 44;
  const effectiveInspectorWidth = inspectorCollapsed
    ? collapsedInspectorWidth
    : clampLayoutValue(inspectorDragWidth ?? inspectorWidth, 260, inspectorMax);
  const effectiveCurveShare = clampLayoutValue(curveShare, curveShareRange.min, curveShareRange.max);
  const beforeActivitySurfaces = workbenchSurfacesInZone(application, "before_activity");
  const primarySurfaces = workbenchSurfacesInZone(application, "analysis_primary");
  const evidenceSurfaces = workbenchSurfacesInZone(application, "analysis_evidence");
  const afterAnalysisSurfaces = workbenchSurfacesInZone(application, "after_analysis");
  const selectedPrimarySurface = primarySurfaces.find(
    (surface) => surface.kind === activePrimarySurface,
  ) ?? primarySurfaces[0];
  useEffect(() => {
    if (primarySurfaces.some((surface) => surface.kind === activePrimarySurface)) return;
    if (primarySurfaces[0]) setActivePrimarySurface(primarySurfaces[0].kind);
  }, [application?.workbench_surfaces, activePrimarySurface]);
  useEffect(() => {
    if (
      candidateSection !== "actuals"
      || !beforeActivitySurfaces.some((surface) => surface.kind === "actual_measurement")
    ) return;
    actualMeasurementRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [candidateSection, application?.workbench_surfaces, selected.id]);
  useEffect(() => saveLayoutNumber(workbenchLayoutStorage.inspectorWidth, inspectorWidth), [inspectorWidth]);
  useEffect(() => saveLayoutBoolean(workbenchLayoutStorage.inspectorCollapsed, inspectorCollapsed), [inspectorCollapsed]);
  useEffect(() => {
    if (inspectorFocusTarget.current === null) return;
    const target = inspectorFocusTarget.current === "expand"
      ? inspectorExpandButtonRef.current
      : inspectorCollapseButtonRef.current;
    inspectorFocusTarget.current = null;
    target?.focus();
  }, [inspectorCollapsed]);
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

  const collapseInspector = () => {
    inspectorFocusTarget.current = "expand";
    setInspectorCollapsed(true);
  };
  const expandInspector = () => {
    inspectorFocusTarget.current = "collapse";
    setInspectorCollapsed(false);
  };
  const resizeInspectorDuringDrag = (nextWidth: number) => {
    setInspectorDragWidth(clampLayoutValue(nextWidth, 260, inspectorMax));
  };
  const finishInspectorResize = (nextWidth: number) => {
    setInspectorDragWidth(null);
    if (nextWidth < 260) {
      collapseInspector();
      return;
    }
    setInspectorWidth(clampLayoutValue(nextWidth, 260, inspectorMax));
  };
  const collapsedSaveLabel: Record<CandidateSaveState, string> = {
    idle: "",
    dirty: "未保存",
    saving: "保存中",
    saved: "保存済み",
    conflict: "競合",
    error: "保存失敗",
  };

  const renderSurface = (surface: WorkbenchSurface) => {
    if (!taskDefinition) return null;
    switch (surface.kind) {
      case "blend_tools":
        return <div className="blend-tools-surface">
          <BlendComparisonPanel projectId={projectId} candidates={candidates} selected={selected} />
          <BlendOptimizationPanel projectId={projectId} candidate={selected.raw} onCandidateCreated={onOptimizedCandidate} />
        </div>;
      case "actual_measurement":
        return <div ref={actualMeasurementRef} id="candidate-actuals">
          <ActualMeasurementPanel
            projectId={projectId}
            candidate={selected}
            taskDefinition={taskDefinition}
            displayDecimalOverrides={project?.display_decimals}
            ready={["idle", "saved"].includes(saveState)}
          />
        </div>;
      case "curve_family":
        return <CurveFamilyPanel
          projectId={projectId}
          candidate={selected}
          taskDefinition={taskDefinition}
          targetValues={targetValues}
          ready={["idle", "saved"].includes(saveState)}
        />;
      case "response_curve":
        return <LiveResponseCurves
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
        />;
      case "prediction_space":
        return <PredictionSpacePanel
          active={surface.kind === selectedPrimarySurface?.kind}
          projectId={projectId}
          candidates={candidates}
          selectedId={selectedId}
          taskDefinition={taskDefinition}
          surface={surface}
          previewsByCandidate={previewsByCandidate}
          targetValues={targetValues}
          pendingPreviewCount={pendingPreviewCount}
          loadingRemainingPreviews={loadingRemainingPreviews}
          onLoadRemainingPreviews={onLoadRemainingPreviews}
          onSelect={onSelect}
          onAddCandidate={onAddCandidateFromLineage}
        />;
      case "response_contour":
        return <ResponseContourPanel
          key={`${projectId}:${selected.id}:${taskDefinition.id}`}
          projectId={projectId}
          candidate={selected}
          taskDefinition={taskDefinition}
          surface={surface}
          ready={["idle", "saved"].includes(saveState)}
        />;
      case "similarity":
        return <SimilarityEvidencePanel
          projectId={projectId}
          candidate={selected}
          outputs={taskDefinition.outputs}
          taskDefinition={taskDefinition}
          displayDecimalOverrides={project?.display_decimals}
          available
          targetSpecific={operations?.target_specific_similarity === true}
          ready={["idle", "saved"].includes(saveState)}
          onAddCandidate={onAddCandidateFromLineage}
        />;
      case "feature_engineering":
        return <FeatureEngineeringPanel preview={preview} />;
    }
  };
  return (
    <div
      ref={workbenchRef}
      className={`workbench-grid candidate-workbench-grid${taskDefinition ? " has-inspector" : ""}${inspectorCollapsed ? " inspector-collapsed" : ""}`}
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
        onCollapse={collapseInspector}
        collapseButtonRef={inspectorCollapseButtonRef}
        hidden={inspectorCollapsed}
        heatPattern={taskDefinition.input_groups.some((group) => group.key === "heat_pattern") ? <HeatPattern candidates={candidates} candidate={selected} onTimeBasisChange={(basis) => onHeatTimeBasis(selected.id, basis)} onUpdate={onHeat} onAdd={onAddHeat} onDelete={onDeleteHeat} /> : undefined}
      />}
      {taskDefinition && <aside
        className="candidate-inspector-collapsed"
        aria-label={`折りたたまれた選択候補の入力${collapsedSaveLabel[saveState] ? `、${collapsedSaveLabel[saveState]}` : ""}`}
        title={selected.label}
        hidden={!inspectorCollapsed}
      >
        <button
          type="button"
          ref={inspectorExpandButtonRef}
          aria-label="選択候補の入力を開く"
          title="選択候補の入力を開く"
          onClick={expandInspector}
        >›</button>
        {collapsedSaveLabel[saveState] && <i
          className={`collapsed-save-state ${saveState}`}
          role="status"
          aria-label={collapsedSaveLabel[saveState]}
          title={collapsedSaveLabel[saveState]}
        />}
        <span aria-hidden="true">入力</span>
      </aside>}
      {taskDefinition && !inspectorCollapsed ? <SplitResizer
        className="candidate-inspector-resizer"
        label="選択候補の入力パネル幅を調整"
        value={effectiveInspectorWidth}
        min={260}
        dragMin={44}
        max={inspectorMax}
        step={10}
        onChange={(nextWidth) => {
          if (nextWidth < 260) collapseInspector();
          else setInspectorWidth(nextWidth);
        }}
        onDragChange={resizeInspectorDuringDrag}
        onDrag={(startValue, deltaX) => startValue + deltaX}
        onDragEnd={finishInspectorResize}
        onDragCancel={() => setInspectorDragWidth(null)}
        onReset={() => {
          setInspectorWidth(330);
          expandInspector();
        }}
      /> : taskDefinition ? <div className="candidate-inspector-resizer collapsed-divider" aria-hidden="true" /> : null}
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
        {beforeActivitySurfaces.map((surface) => (
          <div key={surface.kind} data-workbench-surface={surface.kind}>
            {renderSurface(surface)}
          </div>
        ))}
        {(primarySurfaces.length > 0 || evidenceSurfaces.length > 0) && <div
          ref={lowerPanelsRef}
          className={`workbench-lower-grid${primarySurfaces.length ? "" : " no-response-curves"}`}
          style={{ "--response-curve-share": `${effectiveCurveShare}%` } as CSSProperties}
          data-workbench-surface-zone="analysis"
        >
          {selectedPrimarySurface ? <div className="workbench-surface-deck">
            {primarySurfaces.length > 1 ? <div className="workbench-surface-tabs" role="tablist" aria-label="予測の見方">
              {primarySurfaces.map((surface) => <button
                key={surface.kind}
                id={`workbench-surface-tab-${surface.kind}`}
                type="button"
                role="tab"
                aria-selected={surface.kind === selectedPrimarySurface.kind}
                aria-controls={`workbench-surface-panel-${surface.kind}`}
                tabIndex={surface.kind === selectedPrimarySurface.kind ? 0 : -1}
                className={surface.kind === selectedPrimarySurface.kind ? "active" : ""}
                onClick={() => setActivePrimarySurface(surface.kind)}
                onKeyDown={(event) => {
                  const tabs = Array.from(
                    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? [],
                  );
                  const current = tabs.indexOf(event.currentTarget);
                  const next = event.key === "ArrowRight"
                    ? (current + 1) % tabs.length
                    : event.key === "ArrowLeft"
                      ? (current - 1 + tabs.length) % tabs.length
                      : event.key === "Home"
                        ? 0
                        : event.key === "End"
                          ? tabs.length - 1
                          : -1;
                  if (next < 0) return;
                  event.preventDefault();
                  tabs[next]?.focus();
                  tabs[next]?.click();
                }}
              >{workbenchSurfaceRegistry[surface.kind].label}</button>)}
            </div> : null}
            {primarySurfaces.map((surface) => <div
              key={surface.kind}
              id={`workbench-surface-panel-${surface.kind}`}
              role={primarySurfaces.length > 1 ? "tabpanel" : undefined}
              aria-labelledby={primarySurfaces.length > 1 ? `workbench-surface-tab-${surface.kind}` : undefined}
              hidden={surface.kind !== selectedPrimarySurface.kind}
              data-workbench-surface={surface.kind}
            >
              {renderSurface(surface)}
            </div>)}
          </div> : null}
          {primarySurfaces.length > 0 && evidenceSurfaces.length > 0 && <SplitResizer
            className="lower-panel-resizer"
            label="予測ビューと近い過去実績の幅を調整"
            value={effectiveCurveShare}
            min={curveShareRange.min}
            max={curveShareRange.max}
            step={2}
            onChange={setCurveShare}
            onDrag={(startValue, deltaX) => startValue + (deltaX / Math.max(lowerPanelsRef.current?.clientWidth ?? 1, 1)) * 100}
            onReset={() => setCurveShare(50)}
          />}
          {evidenceSurfaces.map((surface) => <div key={surface.kind} data-workbench-surface={surface.kind}>
            {renderSurface(surface)}
          </div>)}
        </div>}
        {afterAnalysisSurfaces.map((surface) => <div key={surface.kind} data-workbench-surface={surface.kind}>
          {renderSurface(surface)}
        </div>)}
      </section>
    </div>
  );
}
