import { type CSSProperties, useEffect, useRef, useState } from "react";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import {
  CandidateInspector,
  ComparisonTable,
  type CandidateSaveState,
  type CandidateViewModel as Candidate,
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
import {
  type ApiProject,
  type ApiPreview,
} from "../../shared/api/workbench-api";
import { SimilarityEvidencePanel } from "./SimilarityEvidencePanel";
import { HeatPattern } from "./HeatPatternPanel";
import {
  CurveFamilyPanel,
  LiveResponseCurves,
  UnavailablePanel,
  type ResponseCurveRanges,
} from "./ResponseCurvePanels";

export function WorkbenchEmptyState({
  loading,
  error,
  onCreate,
}: {
  loading: boolean;
  error: string | null;
  onCreate: () => void;
}) {
  return (
    <div className="api-empty-state" role={error ? "alert" : "status"}>
      <h2>{loading ? "候補を読み込んでいます" : "候補を表示できません"}</h2>
      <p>{error ?? "データと予測モデルを準備しています。"}</p>
      {error && (
        <p className="api-hint">
          FastAPI を <code>{apiBaseUrl}</code> で起動後、再読み込みしてください。
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
  targetValues: Record<string, number>;
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
  onInput: (id: string, path: string, value: number | string) => void;
  onText: (id: string, field: "label", value: string) => void;
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
  onAddCandidateFromLineage: (entityKey: string) => Promise<boolean>;
  onImported: (items: Candidate[]) => void;
  onProjectChanged: (project: ApiProject) => void | Promise<void>;
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
    onHeat,
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
    onProjectChanged,
  } = props;
  const [comparisonExpanded, setComparisonExpanded] = useState(false);
  const [inspectorWidth, setInspectorWidth] = useState(() => clampLayoutValue(storedLayoutNumber(workbenchLayoutStorage.inspectorWidth, 330), 260, 520));
  const [inspectorMax, setInspectorMax] = useState(520);
  const [curveShare, setCurveShare] = useState(() => clampLayoutValue(storedLayoutNumber(workbenchLayoutStorage.curveShare, 50), 30, 70));
  const [curveShareRange, setCurveShareRange] = useState({ min: 30, max: 70 });
  const workbenchRef = useRef<HTMLDivElement>(null);
  const lowerPanelsRef = useRef<HTMLDivElement>(null);
  const effectiveInspectorWidth = clampLayoutValue(inspectorWidth, 260, inspectorMax);
  const effectiveCurveShare = clampLayoutValue(curveShare, curveShareRange.min, curveShareRange.max);
  useEffect(() => {
    if (candidates.length <= 5) setComparisonExpanded(false);
  }, [candidates.length]);
  useEffect(() => saveLayoutNumber(workbenchLayoutStorage.inspectorWidth, inspectorWidth), [inspectorWidth]);
  useEffect(() => saveLayoutNumber(workbenchLayoutStorage.curveShare, curveShare), [curveShare]);
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
        heatPattern={taskDefinition.input_groups.some((group) => group.key === "heat_pattern") ? <HeatPattern candidates={candidates} candidate={selected} onUpdate={onHeat} onAdd={onAddHeat} onDelete={onDeleteHeat} /> : undefined}
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
            {candidates.length > 5 && (
              <button
                type="button"
                className="comparison-expand-button"
                aria-expanded={comparisonExpanded}
                onClick={() => setComparisonExpanded((value) => !value)}
              >
                {comparisonExpanded ? "5候補までに戻す" : `全${candidates.length}候補を表示`}
              </button>
            )}
          </div>
          {previewError && <span className="comparison-preview-error" role="alert">{previewError}{operations?.preview && <button type="button" onClick={onRetryPreview}>再試行</button>}</span>}
          <div className="comparison-actions" aria-label="候補操作">
            <CandidateFileControls projectId={projectId} capability={application} onImported={onImported} />
            <CandidateAddButton onClick={onAdd}>候補を追加</CandidateAddButton>
          </div>
        </div>
        <CandidateOrigin candidate={selected} broken={originBroken} onOpen={onOpenOrigin} />
        {taskDefinition && <ComparisonTable
          candidates={candidates}
          selectedId={selectedId}
          comparisonExpanded={comparisonExpanded}
          onToggleComparisonExpanded={() => setComparisonExpanded((value) => !value)}
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
        />}
        {taskDefinition?.curve_axis_path && operations?.response_curve ? (
          <CurveFamilyPanel
            projectId={projectId}
            candidate={selected}
            taskDefinition={taskDefinition}
            targetValues={targetValues}
            ready={["idle", "saved"].includes(saveState)}
          />
        ) : null}
        <div
          ref={lowerPanelsRef}
          className="workbench-lower-grid"
          style={{ "--response-curve-share": `${effectiveCurveShare}%` } as CSSProperties}
        >
          {operations?.response_curve ? (
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
          ) : <UnavailablePanel title="応答曲線" />}
          <SplitResizer
            className="lower-panel-resizer"
            label="応答曲線と近い過去実績の幅を調整"
            value={effectiveCurveShare}
            min={curveShareRange.min}
            max={curveShareRange.max}
            step={2}
            onChange={setCurveShare}
            onDrag={(startValue, deltaX) => startValue + (deltaX / Math.max(lowerPanelsRef.current?.clientWidth ?? 1, 1)) * 100}
            onReset={() => setCurveShare(50)}
          />
          <SimilarityEvidencePanel projectId={projectId} candidate={selected} outputs={taskDefinition?.outputs ?? []} available={operations?.similarity === true} ready={["idle", "saved"].includes(saveState)} onAddCandidate={onAddCandidateFromLineage} />
        </div>
      </section>
    </div>
  );
}
