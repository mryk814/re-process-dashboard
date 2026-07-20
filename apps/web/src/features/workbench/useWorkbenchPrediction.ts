import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { CandidateViewModel, RuntimeOperations } from "../candidates";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import { workbenchApi, type ApiPreview } from "../../shared/api/workbench-api";
import {
  emptyInferenceSurface,
  inferenceSurfaceStatus,
  rejectInferenceSurface,
  requestInferenceSurface,
  resolveInferenceSurface,
  type InferenceSurfaceState,
} from "./inferenceSurfaceState";
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
  const [surfacesByCandidate, setSurfacesByCandidate] = useState<Record<string, InferenceSurfaceState<ApiPreview>>>({});
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
  const previewSurface = candidate ? surfacesByCandidate[candidate.id] ?? emptyInferenceSurface<ApiPreview>() : emptyInferenceSurface<ApiPreview>();
  const previewStatus = inferenceSurfaceStatus(previewSurface);
  const metrics = preview ? metricsFromPreview(preview) : [];

  function getPreviewInputIdentity(candidateId: string) {
    return requestedInputIdentities.current.get(candidateId) ?? identities.current.get(candidateId)?.inputIdentity;
  }

  function acceptPreview(candidateId: string, nextPreview: ApiPreview | null, nextInputIdentity?: string, candidateRevision?: number, requestError?: unknown) {
    if (nextPreview && nextInputIdentity && candidateRevision !== undefined) {
      const nextKey = workbenchInferenceKey({ projectId, taskId, candidateId, inputIdentity: nextInputIdentity }, "preview");
      identities.current.set(candidateId, {
        inputIdentity: nextInputIdentity,
        requestKey: workbenchRequestKey({ projectId, taskId, candidateId, candidateRevision }, "preview"),
      });
      requestedInputIdentities.current.set(candidateId, nextInputIdentity);
      setPreviewsByCandidate((current) => ({ ...current, [candidateId]: nextPreview }));
      setSurfacesByCandidate((current) => {
        let surface = current[candidateId] ?? emptyInferenceSurface<ApiPreview>();
        if (surface.requestedIdentity !== nextKey) surface = requestInferenceSurface(surface, nextKey);
        return { ...current, [candidateId]: resolveInferenceSurface(surface, surface.requestSequence, nextKey, nextPreview) };
      });
    } else if (nextInputIdentity && candidateRevision !== undefined) {
      requestedInputIdentities.current.set(candidateId, nextInputIdentity);
      const nextKey = workbenchInferenceKey({ projectId, taskId, candidateId, inputIdentity: nextInputIdentity }, "preview");
      setSurfacesByCandidate((current) => {
        let surface = current[candidateId] ?? emptyInferenceSurface<ApiPreview>();
        if (surface.requestedIdentity !== nextKey || !surface.pending) surface = requestInferenceSurface(surface, nextKey);
        if (requestError !== undefined) surface = rejectInferenceSurface(surface, surface.requestSequence, nextKey, requestError);
        return { ...current, [candidateId]: surface };
      });
    } else {
      if (candidateId === candidate?.id) previewController.current?.abort();
      identities.current.delete(candidateId);
      requestedInputIdentities.current.delete(candidateId);
      setPreviewsByCandidate((current) => {
        const { [candidateId]: _, ...remaining } = current;
        return remaining;
      });
      setSurfacesByCandidate((current) => {
        const { [candidateId]: _, ...remaining } = current;
        return remaining;
      });
    }
    if (candidateId === candidate?.id) setError("");
  }

  function reset() {
    identities.current.clear();
    requestedInputIdentities.current.clear();
    setPreviewsByCandidate({});
    setSurfacesByCandidate({});
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
    setSurfacesByCandidate((current) => {
      const next = { ...current };
      for (const item of candidates) {
        const data = accepted[item.id];
        if (!data) continue;
        const key = workbenchInferenceKey({ projectId: item.raw.project_id, taskId: loadedTaskId, candidateId: item.id, inputIdentity: candidateInputIdentity(item.raw.inputs) }, "preview");
        const requested = requestInferenceSurface(next[item.id] ?? emptyInferenceSurface<ApiPreview>(), key);
        next[item.id] = resolveInferenceSurface(requested, requested.requestSequence, key, data);
      }
      return next;
    });
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
    setSurfacesByCandidate((current) => ({
      ...current,
      [candidate.id]: requestInferenceSurface(current[candidate.id] ?? emptyInferenceSurface<ApiPreview>(), requestActiveKey),
    }));
    setApiState("loading");
    setError("");
    const timer = window.setTimeout(async () => {
      try {
        const result = await workbenchApi.previewCandidate(projectId, candidate.id, candidate.raw.revision, inputIdentity, controller.signal);
        if (controller.signal.aborted || currentPreviewActiveKeyRef.current !== requestActiveKey) return;
        identities.current.set(candidate.id, { inputIdentity, requestKey: previewRequestKey });
        setPreviewsByCandidate((current) => ({ ...current, [candidate.id]: result }));
        setSurfacesByCandidate((current) => {
          const surface = current[candidate.id] ?? emptyInferenceSurface<ApiPreview>();
          return { ...current, [candidate.id]: resolveInferenceSurface(surface, surface.requestSequence, requestActiveKey, result) };
        });
        onNotice(result.warnings?.[0] ?? "プレビューを更新しました");
      } catch (cause) {
        if (controller.signal.aborted || currentPreviewActiveKeyRef.current !== requestActiveKey) return;
        setSurfacesByCandidate((current) => {
          const surface = current[candidate.id] ?? emptyInferenceSurface<ApiPreview>();
          return { ...current, [candidate.id]: rejectInferenceSurface(surface, surface.requestSequence, requestActiveKey, cause) };
        });
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
      setSurfacesByCandidate((current) => {
        const key = currentPreviewActiveKeyRef.current;
        const requested = requestInferenceSurface(current[candidate.id] ?? emptyInferenceSurface<ApiPreview>(), key);
        return { ...current, [candidate.id]: resolveInferenceSurface(requested, requested.requestSequence, key, payload.prediction) };
      });
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
    previewStatus,
    previewsByCandidate,
    reset,
    retry: () => setRetrySequence((value) => value + 1),
    runDetailedPrediction,
  };
}
