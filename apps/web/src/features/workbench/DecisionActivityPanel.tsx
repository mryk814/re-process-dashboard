import { useEffect, useMemo, useRef, useState } from "react";
import type { CandidateViewModel, TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiDecisionActivityAvailability,
  type ApiDecisionActivityRun,
  type ApiCandidate,
} from "../../shared/api/workbench-api";
import {
  acceptsDecisionActivityResponse,
  decisionActivityIdentity,
} from "./decisionActivityState";
import { decisionActivityView } from "./decisionActivities/registry";
import type { DecisionActivityParameters } from "./decisionActivities/types";
import type { TargetGoal } from "../../shared/targetGoals";

/**
 * Activity-agnostic shell. It owns availability, saved runs, request identity and
 * abort handling; each activity owns its own parameters and result surface.
 */
export function DecisionActivityPanel({
  projectId,
  candidate,
  candidates,
  taskDefinition,
  displayDecimalOverrides,
  targetValues,
  ready,
  requestedActivityId,
  requestedRunId,
  onStateChange,
  onConfigureGoals,
  onClose,
  onCandidateCreated,
}: {
  projectId: string;
  candidate: CandidateViewModel;
  candidates: CandidateViewModel[];
  taskDefinition: TaskDefinitionContract;
  displayDecimalOverrides?: Record<string, number>;
  targetValues: Record<string, TargetGoal>;
  ready: boolean;
  requestedActivityId?: string;
  requestedRunId?: string;
  onStateChange: (activityId?: string, activityRunId?: string) => void;
  onConfigureGoals: () => void;
  onClose: () => void;
  onCandidateCreated: (candidate: ApiCandidate) => void;
}) {
  const identity = decisionActivityIdentity(projectId, candidate.id, candidate.raw.revision);
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const requestControllerRef = useRef<AbortController | null>(null);
  const [activities, setActivities] = useState<ApiDecisionActivityAvailability[]>([]);
  const [runs, setRuns] = useState<ApiDecisionActivityRun[]>([]);
  const [selectedId, setSelectedId] = useState(requestedActivityId ?? "");
  const [activeRunId, setActiveRunId] = useState<string | null>(requestedRunId ?? null);
  const [loading, setLoading] = useState(true);
  const [loadedIdentity, setLoadedIdentity] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [locationError, setLocationError] = useState("");

  useEffect(() => {
    requestControllerRef.current?.abort();
    setRunning(false);
    setActivities([]);
    setRuns([]);
    setActiveRunId(null);
  }, [identity]);

  useEffect(() => {
    const controller = new AbortController();
    const requestedIdentity = identity;
    setLoadedIdentity("");
    setLoading(true);
    setError("");
    Promise.all([
      workbenchApi.decisionActivities(projectId, candidate.id, candidate.raw.revision, controller.signal),
      workbenchApi.decisionActivityRuns(projectId, candidate.id, controller.signal),
    ]).then(([items, savedRuns]) => {
      if (!acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) return;
      setActivities(items);
      setRuns(savedRuns);
      setLoadedIdentity(requestedIdentity);
    }).catch((cause: unknown) => {
      if (controller.signal.aborted || !acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) return;
      setError(cause instanceof Error ? cause.message : "検討アクティビティを取得できませんでした。");
    }).finally(() => {
      if (!controller.signal.aborted && acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) setLoading(false);
    });
    return () => controller.abort();
  }, [identity, projectId, candidate.id, candidate.raw.revision]);

  useEffect(() => () => requestControllerRef.current?.abort(), [identity]);

  useEffect(() => {
    if (loadedIdentity !== identity) return;
    const requestedRun = requestedRunId
      ? runs.find((run) => run.id === requestedRunId)
      : undefined;
    const requestedActivityExists = requestedActivityId
      ? activities.some((item) => item.definition.activity_id === requestedActivityId)
      : false;

    if (requestedRunId && !requestedRun) {
      setSelectedId(requestedActivityExists ? requestedActivityId! : activities[0]?.definition.activity_id ?? "");
      setActiveRunId(null);
      setLocationError(`保存済みRun「${requestedRunId}」は、この候補では見つかりません。URLまたは候補を確認してください。`);
      return;
    }
    if (requestedRun) {
      setSelectedId(requestedRun.definition.activity_id);
      setActiveRunId(requestedRun.id);
      setLocationError("");
      return;
    }
    if (requestedActivityId && !requestedActivityExists) {
      setSelectedId(activities[0]?.definition.activity_id ?? "");
      setActiveRunId(null);
      setLocationError(`検討アクティビティ「${requestedActivityId}」は、この候補では利用できません。URLを確認してください。`);
      return;
    }
    setSelectedId((current) => (
      requestedActivityId
      ?? (activities.some((item) => item.definition.activity_id === current)
        ? current
        : activities[0]?.definition.activity_id ?? "")
    ));
    setActiveRunId(null);
    setLocationError("");
  }, [activities, identity, loadedIdentity, requestedActivityId, requestedRunId, runs]);

  useEffect(() => {
    if (loadedIdentity !== identity || !selectedId || locationError) return;
    if (requestedActivityId && selectedId !== requestedActivityId) return;
    if (requestedRunId && activeRunId !== requestedRunId) return;
    onStateChange(selectedId, activeRunId ?? undefined);
  }, [
    activeRunId,
    identity,
    loadedIdentity,
    locationError,
    requestedActivityId,
    requestedRunId,
    selectedId,
  ]);

  const selected = activities.find((item) => item.definition.activity_id === selectedId) ?? null;
  const View = selected ? decisionActivityView(selected.definition.activity_id) : null;
  const activityRuns = useMemo(
    () => runs.filter((run) => run.definition.activity_id === selectedId),
    [runs, selectedId],
  );

  function selectRun(activityId: string, runId: string | null) {
    setSelectedId(activityId);
    setActiveRunId(runId);
    setLocationError("");
    onStateChange(activityId, runId ?? undefined);
  }

  async function runActivity(parameters: DecisionActivityParameters) {
    if (!selected || running) return;
    const controller = new AbortController();
    requestControllerRef.current?.abort();
    requestControllerRef.current = controller;
    const requestedIdentity = identity;
    setRunning(true);
    setError("");
    try {
      const result = await workbenchApi.runDecisionActivity(
        projectId,
        candidate.id,
        selected.definition.activity_id,
        { expected_revision: candidate.raw.revision, parameters },
        controller.signal,
      );
      if (!acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) return;
      setRuns((current) => [result, ...current.filter((item) => item.id !== result.id)]);
      selectRun(result.definition.activity_id, result.id);
    } catch (cause) {
      if (!controller.signal.aborted && acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) {
        setError(cause instanceof Error ? cause.message : `${selected.definition.label}を実行できませんでした。`);
      }
    } finally {
      if (requestControllerRef.current === controller) requestControllerRef.current = null;
      if (!controller.signal.aborted && acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) setRunning(false);
    }
  }

  return <aside className="decision-activity-panel" aria-label="検討アクティビティ">
    <header>
      <div>
        <span className="overline">DECISION ACTIVITY</span>
        <h2>{selected?.definition.label ?? "検討アクティビティ"}</h2>
      </div>
      <button type="button" className="outline-button" onClick={onClose}>閉じる</button>
    </header>
    {activities.length > 1 && <nav className="activity-tabs" aria-label="検討アクティビティの選択">
      {activities.map((item) => <button
        type="button"
        key={item.definition.activity_id}
        className={item.definition.activity_id === selectedId ? "active" : ""}
        aria-current={item.definition.activity_id === selectedId ? "page" : undefined}
        onClick={() => {
          setSelectedId(item.definition.activity_id);
          setActiveRunId(null);
          setLocationError("");
          onStateChange(item.definition.activity_id, undefined);
        }}
      >{item.definition.label}</button>)}
    </nav>}
    {loading ? <p className="empty-evidence">利用条件を確認しています。</p> : selected && !selected.available ? (
      <section className="activity-unavailable" aria-label="実行前に必要な準備">
        <span className="overline">実行前の確認</span>
        <strong>このActivityには準備が必要です</strong>
        <ul>{selected.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      </section>
    ) : null}
    {error && <p className="panel-error" role="alert">{error}</p>}
    {locationError && <p className="panel-error" role="alert">{locationError}</p>}
    {!loading && selected && !View && <p className="empty-evidence">
      この検討アクティビティの表示はこの版では未対応です。
    </p>}
    {loadedIdentity === identity && selected && View && !locationError && <View
      projectId={projectId}
      candidate={candidate}
      candidates={candidates}
      taskDefinition={taskDefinition}
      displayDecimalOverrides={displayDecimalOverrides}
      targetValues={targetValues}
      ready={ready}
      availability={selected}
      runs={activityRuns}
      activeRunId={activeRunId}
      onSelectRun={(runId) => selectRun(selectedId, runId)}
      running={running}
      onRun={runActivity}
      onCandidateCreated={onCandidateCreated}
      onConfigureGoals={onConfigureGoals}
    />}
  </aside>;
}
