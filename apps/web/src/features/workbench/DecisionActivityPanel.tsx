import { useEffect, useMemo, useRef, useState } from "react";
import type { CandidateViewModel, TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiDecisionActivityAvailability,
  type ApiDecisionActivityRun,
} from "../../shared/api/workbench-api";
import {
  acceptsDecisionActivityResponse,
  decisionActivityIdentity,
} from "./decisionActivityState";
import { decisionActivityView } from "./decisionActivities/registry";
import type { DecisionActivityParameters } from "./decisionActivities/types";

/**
 * Activity-agnostic shell. It owns availability, saved runs, request identity and
 * abort handling; each activity owns its own parameters and result surface.
 */
export function DecisionActivityPanel({
  projectId,
  candidate,
  candidates,
  taskDefinition,
  ready,
  onClose,
}: {
  projectId: string;
  candidate: CandidateViewModel;
  candidates: CandidateViewModel[];
  taskDefinition: TaskDefinitionContract;
  ready: boolean;
  onClose: () => void;
}) {
  const identity = decisionActivityIdentity(projectId, candidate.id, candidate.raw.revision);
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const requestControllerRef = useRef<AbortController | null>(null);
  const [activities, setActivities] = useState<ApiDecisionActivityAvailability[]>([]);
  const [runs, setRuns] = useState<ApiDecisionActivityRun[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    requestControllerRef.current?.abort();
    setRunning(false);
  }, [identity]);

  useEffect(() => {
    const controller = new AbortController();
    const requestedIdentity = identity;
    setLoading(true);
    setError("");
    Promise.all([
      workbenchApi.decisionActivities(projectId, candidate.id, candidate.raw.revision, controller.signal),
      workbenchApi.decisionActivityRuns(projectId, candidate.id, controller.signal),
    ]).then(([items, savedRuns]) => {
      if (!acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) return;
      setActivities(items);
      setRuns(savedRuns);
      setSelectedId((current) => (
        items.some((item) => item.definition.activity_id === current)
          ? current
          : items[0]?.definition.activity_id ?? ""
      ));
    }).catch((cause: unknown) => {
      if (controller.signal.aborted || !acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) return;
      setError(cause instanceof Error ? cause.message : "検討アクティビティを取得できませんでした。");
    }).finally(() => {
      if (!controller.signal.aborted && acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) setLoading(false);
    });
    return () => controller.abort();
  }, [identity, projectId, candidate.id, candidate.raw.revision]);

  useEffect(() => () => requestControllerRef.current?.abort(), [identity]);

  const selected = activities.find((item) => item.definition.activity_id === selectedId) ?? null;
  const View = selected ? decisionActivityView(selected.definition.activity_id) : null;
  const activityRuns = useMemo(
    () => runs.filter((run) => run.definition.activity_id === selectedId),
    [runs, selectedId],
  );

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
        onClick={() => setSelectedId(item.definition.activity_id)}
      >{item.definition.label}</button>)}
    </nav>}
    {selected && <p className="activity-question">{selected.definition.question}</p>}
    {loading ? <p className="empty-evidence">利用条件を確認しています。</p> : selected && !selected.available ? (
      <div className="activity-unavailable"><strong>現在は利用できません</strong>{selected.reasons.map((reason) => <span key={reason}>{reason}</span>)}</div>
    ) : null}
    {error && <p className="panel-error" role="alert">{error}</p>}
    {!loading && selected && !View && <p className="empty-evidence">
      この検討アクティビティの表示はこの版では未対応です。
    </p>}
    {selected && View && <View
      projectId={projectId}
      candidate={candidate}
      candidates={candidates}
      taskDefinition={taskDefinition}
      ready={ready}
      availability={selected}
      runs={activityRuns}
      running={running}
      onRun={runActivity}
    />}
  </aside>;
}
