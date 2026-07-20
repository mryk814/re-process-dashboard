import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { CandidateViewModel, RuntimeOperations } from "../candidates";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import { workbenchApi, type ApiPreview } from "../../shared/api/workbench-api";
import { workbenchInferenceKey, workbenchRequestKey, type WorkbenchIdentity } from "./workbenchIdentity";

export type PredictionMetric = {
  key: string;
  unit: string;
  value: number;
  low: number;
  high: number;
  status: string;
  goalValue?: number | null;
  goalProbability?: number | null;
  modelStd?: number | null;
  observationStd?: number | null;
};

export function metricsFromPreview(preview: ApiPreview): PredictionMetric[] {
  return Object.entries(preview.predictions ?? {}).map(([key, prediction]) => ({
    key: key === "lambda" ? "λ" : key,
    unit: prediction.unit,
    value: prediction.value,
    low: prediction.lower,
    high: prediction.upper,
    status: preview.support?.status ?? "supported",
    goalValue: prediction.goal_value,
    goalProbability: prediction.goal_probability,
    modelStd: prediction.uncertainty_components?.latent_model_std
      ?? (prediction.uncertainty_components?.latent_model_variance !== undefined ? Math.sqrt(prediction.uncertainty_components.latent_model_variance) : null),
    observationStd: prediction.uncertainty_components?.observation_noise_std
      ?? (prediction.uncertainty_components?.observation_noise_variance !== undefined ? Math.sqrt(prediction.uncertainty_components.observation_noise_variance) : null),
  }));
}

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
  const preview = candidate
    && storedIdentity?.inputIdentity === inputIdentity
    ? previewsByCandidate[candidate.id] ?? null
    : null;
  const metrics = preview ? metricsFromPreview(preview) : [];

  function getPreviewInputIdentity(candidateId: string) {
    return identities.current.get(candidateId)?.inputIdentity;
  }

  function acceptPreview(candidateId: string, nextPreview: ApiPreview | null, nextInputIdentity?: string, candidateRevision?: number) {
    if (nextPreview && nextInputIdentity && candidateRevision !== undefined) {
      identities.current.set(candidateId, {
        inputIdentity: nextInputIdentity,
        requestKey: workbenchRequestKey({ projectId, taskId, candidateId, candidateRevision }, "preview"),
      });
      setPreviewsByCandidate((current) => ({ ...current, [candidateId]: nextPreview }));
    } else {
      identities.current.delete(candidateId);
      setPreviewsByCandidate((current) => {
        const { [candidateId]: _, ...remaining } = current;
        return remaining;
      });
    }
    if (candidateId === candidate?.id) setError("");
  }

  function reset() {
    identities.current.clear();
    setPreviewsByCandidate({});
    setError("");
  }

  function acceptProjectPreviews(candidates: CandidateViewModel[], loaded: Record<string, ApiPreview>, loadedTaskId: string) {
    identities.current.clear();
    for (const item of candidates) {
      if (!loaded[item.id]) continue;
      identities.current.set(item.id, {
        inputIdentity: candidateInputIdentity(item.raw.inputs),
        requestKey: workbenchRequestKey({ projectId: item.raw.project_id, taskId: loadedTaskId, candidateId: item.id, candidateRevision: item.raw.revision }, "preview"),
      });
    }
    setPreviewsByCandidate(loaded);
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
    const requestActiveKey = currentPreviewActiveKey;
    setApiState("loading");
    setError("");
    const timer = window.setTimeout(async () => {
      try {
        const result = await workbenchApi.previewCandidate(projectId, candidate.id, inputIdentity, controller.signal);
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
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [candidate?.id, candidate?.raw.revision, projectId, taskId, operations?.preview, retrySequence]);

  async function runDetailedPrediction() {
    if (!candidate || !identity || !operations?.detailed_prediction) return false;
    const requestActiveKey = currentDetailedActiveKey;
    setApiState("loading");
    try {
      const payload = await workbenchApi.predictCandidate(projectId, candidate.id);
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
    metrics,
    preview,
    previewsByCandidate,
    reset,
    retry: () => setRetrySequence((value) => value + 1),
    runDetailedPrediction,
  };
}
