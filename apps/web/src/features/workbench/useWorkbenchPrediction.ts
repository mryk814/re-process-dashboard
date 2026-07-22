import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { CandidateViewModel, RuntimeOperations } from "../candidates";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import { workbenchApi, type ApiPreview } from "../../shared/api/workbench-api";
import { workbenchInferenceKey, workbenchRequestKey, type WorkbenchIdentity } from "./workbenchIdentity";

type Options = {
  projectId: string;
  taskId: string;
  candidate?: CandidateViewModel;
  operations?: RuntimeOperations;
  onNotice: (message: string) => void;
  setApiState: Dispatch<SetStateAction<"ready" | "loading" | "offline">>;
};

type PreviewIdentity = Readonly<{
  inputIdentity: string;
  requestKey: string;
}>;

const activeKey = (requestKey: string, inputIdentity: string) => `${requestKey}\u001f${inputIdentity}`;
export function useWorkbenchPrediction({ projectId, taskId, candidate, operations, onNotice, setApiState }: Options) {
  const [previewsByCandidate, setPreviewsByCandidate] = useState<Record<string, ApiPreview>>({});
  const [error, setError] = useState("");
  const [retrySequence, setRetrySequence] = useState(0);
  const identities = useRef(new Map<string, PreviewIdentity>());
  const requestedInputIdentities = useRef(new Map<string, string>());
  const previewController = useRef<AbortController | null>(null);
  const identity: WorkbenchIdentity | null = candidate && taskId
    ? { projectId, taskId, candidateId: candidate.id, candidateRevision: candidate.raw.revision }
    : null;
  const inputIdentity = candidate ? candidateInputIdentity(candidate.raw.inputs) : "";
  const previewRequestKey = identity ? workbenchRequestKey(identity, "preview") : "";
  const currentPreviewActiveKey = candidate
    ? workbenchInferenceKey({ projectId, taskId, candidateId: candidate.id, inputIdentity }, "preview")
    : "";
  const currentPreviewActiveKeyRef = useRef(currentPreviewActiveKey);
  currentPreviewActiveKeyRef.current = currentPreviewActiveKey;
  const currentDetailedActiveKey = identity ? activeKey(workbenchRequestKey(identity, "detailed"), inputIdentity) : "";
  const currentDetailedActiveKeyRef = useRef(currentDetailedActiveKey);
  currentDetailedActiveKeyRef.current = currentDetailedActiveKey;
  const storedIdentity = candidate ? identities.current.get(candidate.id) : undefined;
  const preview = candidate ? previewsByCandidate[candidate.id] ?? null : null;

  function getPreviewInputIdentity(candidateId: string) {
    return requestedInputIdentities.current.get(candidateId) ?? identities.current.get(candidateId)?.inputIdentity;
  }

  function acceptPreview(candidateId: string, nextPreview: ApiPreview | null, nextInputIdentity?: string, candidateRevision?: number, requestError?: unknown) {
    if (nextPreview && nextInputIdentity && candidateRevision !== undefined) {
      identities.current.set(candidateId, {
        inputIdentity: nextInputIdentity,
        requestKey: workbenchRequestKey({ projectId, taskId, candidateId, candidateRevision }, "preview"),
      });
      requestedInputIdentities.current.set(candidateId, nextInputIdentity);
      setPreviewsByCandidate((current) => ({ ...current, [candidateId]: nextPreview }));
    } else if (nextInputIdentity && candidateRevision !== undefined) {
      requestedInputIdentities.current.set(candidateId, nextInputIdentity);
    } else {
      if (candidateId === candidate?.id) previewController.current?.abort();
      identities.current.delete(candidateId);
      requestedInputIdentities.current.delete(candidateId);
      setPreviewsByCandidate((current) => {
        const { [candidateId]: _, ...remaining } = current;
        return remaining;
      });
    }
    if (candidateId === candidate?.id) setError(requestError === undefined ? "" : "プレビューを取得できませんでした");
  }

  function reset() {
    identities.current.clear();
    requestedInputIdentities.current.clear();
    setPreviewsByCandidate({});
    setError("");
  }

  function acceptProjectPreviews(candidates: CandidateViewModel[], currentCandidates: CandidateViewModel[], loaded: Record<string, ApiPreview>, loadedTaskId: string) {
    const currentById = new Map(currentCandidates.map((item) => [item.id, item]));
    const accepted: Record<string, ApiPreview> = {};
    for (const item of candidates) {
      if (!loaded[item.id]) continue;
      const current = currentById.get(item.id);
      if (!current || current.raw.revision !== item.raw.revision || candidateInputIdentity(current.raw.inputs) !== candidateInputIdentity(item.raw.inputs)) continue;
      identities.current.set(item.id, {
        inputIdentity: candidateInputIdentity(item.raw.inputs),
        requestKey: workbenchRequestKey({ projectId: item.raw.project_id, taskId: loadedTaskId, candidateId: item.id, candidateRevision: item.raw.revision }, "preview"),
      });
      requestedInputIdentities.current.set(item.id, candidateInputIdentity(item.raw.inputs));
      accepted[item.id] = loaded[item.id];
    }
    setPreviewsByCandidate((current) => ({ ...current, ...accepted }));
  }

  useEffect(() => {
    if (!candidate || !identity) return;
    if (candidate.raw.archived_at) {
      setError("");
      setApiState("ready");
      return;
    }
    if (!operations?.preview) {
      setError("このタスクではプレビューを利用できません");
      setApiState("ready");
      return;
    }
    const cachedIdentity = identities.current.get(candidate.id);
    if (cachedIdentity?.inputIdentity === inputIdentity && previewsByCandidate[candidate.id]) {
      identities.current.set(candidate.id, { ...cachedIdentity, requestKey: previewRequestKey });
      setError("");
      setApiState("ready");
      return;
    }
    const controller = new AbortController();
    previewController.current?.abort();
    previewController.current = controller;
    const requestActiveKey = currentPreviewActiveKey;
    requestedInputIdentities.current.set(candidate.id, inputIdentity);
    setApiState("loading");
    setError("");
    const timer = window.setTimeout(async () => {
      try {
        const result = await workbenchApi.previewCandidate(projectId, candidate.id, candidate.raw.revision, inputIdentity, controller.signal);
        if (controller.signal.aborted || currentPreviewActiveKeyRef.current !== requestActiveKey) return;
        identities.current.set(candidate.id, { inputIdentity, requestKey: previewRequestKey });
        setPreviewsByCandidate((current) => ({ ...current, [candidate.id]: result }));
        onNotice(result.warnings?.[0] ?? "プレビューを更新しました");
      } catch {
        if (controller.signal.aborted || currentPreviewActiveKeyRef.current !== requestActiveKey) return;
        setError("プレビューを取得できませんでした");
      } finally {
        if (!controller.signal.aborted && currentPreviewActiveKeyRef.current === requestActiveKey) setApiState("ready");
      }
    }, 420);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (previewController.current === controller) previewController.current = null;
    };
  }, [candidate?.id, projectId, taskId, operations?.preview, retrySequence]);

  async function runDetailedPrediction() {
    if (!candidate || !identity || !operations?.detailed_prediction) return false;
    const requestActiveKey = currentDetailedActiveKey;
    setApiState("loading");
    try {
      const payload = await workbenchApi.predictCandidate(projectId, candidate.id, candidate.raw.revision);
      if (currentDetailedActiveKeyRef.current !== requestActiveKey) return false;
      identities.current.set(candidate.id, { inputIdentity, requestKey: previewRequestKey });
      setPreviewsByCandidate((current) => ({ ...current, [candidate.id]: payload.prediction }));
      setError("");
      onNotice("詳細予測を実行し、スナップショットを保存しました。");
      return true;
    } catch {
      if (currentDetailedActiveKeyRef.current === requestActiveKey) setError("詳細予測または保存に失敗しました");
      return false;
    } finally {
      if (currentDetailedActiveKeyRef.current === requestActiveKey) setApiState("ready");
    }
  }

  return {
    acceptPreview,
    acceptProjectPreviews,
    error,
    getPreviewInputIdentity,
    preview,
    previewsByCandidate,
    reset,
    retry: () => setRetrySequence((value) => value + 1),
    runDetailedPrediction,
  };
}
