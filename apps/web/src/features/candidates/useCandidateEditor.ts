import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import {
  candidateSaveContractError,
  fromApiCandidate,
  toApiCandidate,
  type CandidateViewModel,
} from "./candidateModel";
import { LatestSaveQueue, rebaseChangedFields } from "./latestSaveQueue";
import { ApiClientError } from "../../shared/api/client";
import type { WorkspaceNoticeKind } from "../../shared/workspaceNotice";
import { workbenchApi, type ApiCandidate, type ApiCandidateInput, type ApiCandidateUpdate, type ApiPreview } from "../../shared/api/workbench-api";
import { candidateInferenceChanged, candidateInferencePrefix, candidateInputIdentity, inferenceRequestCache, shouldRefreshPreviewAfterSave } from "../../shared/api/inferenceRequestCache";

export type CandidateSaveState = "idle" | "dirty" | "saving" | "saved" | "conflict" | "error";

type CandidateEditorOptions = {
  projectId: string;
  setCandidates: Dispatch<SetStateAction<CandidateViewModel[]>>;
  previewAvailable: boolean;
  onPreview: (candidateId: string, preview: ApiPreview | null, inputIdentity?: string, candidateRevision?: number, requestError?: unknown) => void;
  getPreviewInputIdentity?: (candidateId: string) => string | undefined;
  onNotify: (kind: WorkspaceNoticeKind, message: string) => void;
};

export function useCandidateEditor({ projectId, setCandidates, previewAvailable, onPreview, getPreviewInputIdentity, onNotify }: CandidateEditorOptions) {
  const queue = useRef(new LatestSaveQueue<ApiCandidate>());
  const authoritative = useRef(new Map<string, ApiCandidate>());
  const scheduled = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const scheduledDrafts = useRef(new Map<string, { candidate: CandidateViewModel; previous?: CandidateViewModel }>());
  const inFlightFlushes = useRef(new Set<Promise<boolean>>());
  const previewControllers = useRef(new Map<string, AbortController>());
  const activeProjectId = useRef(projectId);
  activeProjectId.current = projectId;
  const [saveStates, setSaveStates] = useState<Record<string, CandidateSaveState>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, Array<{ path: string; message: string }>>>({});

  const setSaveState = (candidateId: string, state: CandidateSaveState) => {
    setSaveStates((current) => ({ ...current, [candidateId]: state }));
  };

  function acceptServerCandidates(candidates: ApiCandidate[]) {
    authoritative.current = new Map(candidates.map((candidate) => [candidate.id, candidate]));
    setSaveStates(Object.fromEntries(candidates.map((candidate) => [candidate.id, "idle" as const])));
    setFieldErrors({});
  }

  async function persist(candidate: CandidateViewModel, previous?: CandidateViewModel) {
    const candidateId = candidate.id;
    const timer = scheduled.current.get(candidateId);
    if (timer) clearTimeout(timer);
    scheduled.current.delete(candidateId);
    scheduledDrafts.current.delete(candidateId);
    const initial = authoritative.current.get(candidateId) ?? previous?.raw ?? candidate.raw;
    const basePayload = toApiCandidate(fromApiCandidate(initial));
    const draftPayload = toApiCandidate(candidate);
    const baseInputIdentity = candidateInputIdentity(basePayload.inputs);
    const previewInputIdentityAtStart = getPreviewInputIdentity?.(candidateId);
    setSaveState(candidateId, "saving");
    setFieldErrors((current) => ({ ...current, [candidateId]: [] }));
    const queued = queue.current.enqueue(candidateId, initial, async (serverCandidate) => {
      const rebased = rebaseChangedFields<ApiCandidateInput>(
        basePayload,
        draftPayload,
        toApiCandidate(fromApiCandidate(serverCandidate)),
      );
      return workbenchApi.updateCandidate(projectId, candidateId, {
        ...rebased,
        expected_revision: serverCandidate.revision,
      } satisfies ApiCandidateUpdate);
    }, (error) => {
      const current = error instanceof ApiClientError ? error.currentCandidate : undefined;
      if (!current) throw error;
      authoritative.current.set(candidateId, current);
      return current;
    });

    try {
      const saved = await queued.promise;
      authoritative.current.set(candidateId, saved);
      if (!queued.isLatest() || activeProjectId.current !== projectId) return true;
      const contractError = candidateSaveContractError(saved, draftPayload);
      if (contractError) {
        scheduledDrafts.current.set(candidateId, { candidate, previous });
        setSaveState(candidateId, "error");
        onNotify("error", contractError);
        return false;
      }
      setCandidates((items) => items.map((item) => item.id === candidateId ? fromApiCandidate(saved) : item));
      setSaveState(candidateId, "saved");
      if (!previewAvailable) return true;
      const inputIdentity = candidateInputIdentity(saved.inputs);
      if (!shouldRefreshPreviewAfterSave(baseInputIdentity, inputIdentity, previewInputIdentityAtStart)) return true;
      inferenceRequestCache.invalidatePrefix(candidateInferencePrefix(projectId, candidateId));
      onPreview(candidateId, null, inputIdentity, saved.revision);
      previewControllers.current.get(candidateId)?.abort();
      const previewController = new AbortController();
      previewControllers.current.set(candidateId, previewController);
      void (async () => {
        try {
          const preview = await workbenchApi.previewCandidate(projectId, candidateId, saved.revision, inputIdentity, previewController.signal);
          const current = authoritative.current.get(candidateId);
          if (
            activeProjectId.current !== projectId
            || previewControllers.current.get(candidateId) !== previewController
            || candidateInputIdentity(current?.inputs) !== inputIdentity
          ) return;
          onPreview(candidateId, preview, inputIdentity, saved.revision);
        } catch (cause) {
          const current = authoritative.current.get(candidateId);
          if (
            activeProjectId.current !== projectId
            || previewControllers.current.get(candidateId) !== previewController
            || candidateInputIdentity(current?.inputs) !== inputIdentity
          ) return;
          if (previewController.signal.aborted) return;
          onPreview(candidateId, null, inputIdentity, saved.revision, cause);
          onNotify("error", "入力は保存しましたが、予測結果を更新できませんでした");
        } finally {
          if (previewControllers.current.get(candidateId) === previewController) {
            previewControllers.current.delete(candidateId);
          }
        }
      })();
      return true;
    } catch (error) {
      if (!queued.isLatest() || activeProjectId.current !== projectId) return true;
      const apiError = error instanceof ApiClientError ? error : undefined;
      if (apiError?.currentCandidate) authoritative.current.set(candidateId, apiError.currentCandidate);
      scheduledDrafts.current.set(candidateId, { candidate, previous });
      setFieldErrors((current) => ({ ...current, [candidateId]: apiError?.fieldErrors ?? [] }));
      setSaveState(candidateId, apiError?.kind === "conflict" ? "conflict" : "error");
      onNotify("error", apiError?.kind === "conflict"
        ? "別の更新と競合しました。入力値は保持しています。再読込するか変更内容をコピーしてください"
        : "入力を保存できません。値とエラー表示を確認してください");
      return false;
    } finally {
      queued.release();
    }
  }

  function flush(candidate: CandidateViewModel, previous?: CandidateViewModel) {
    const operation = persist(candidate, previous);
    inFlightFlushes.current.add(operation);
    void operation.finally(() => inFlightFlushes.current.delete(operation));
    return operation;
  }

  async function settlePending() {
    while (true) {
      const drafts = [...scheduledDrafts.current.values()];
      for (const { candidate, previous } of drafts) void flush(candidate, previous);
      const operations = [...inFlightFlushes.current];
      if (!operations.length) return scheduledDrafts.current.size === 0;
      const results = await Promise.all(operations);
      if (!results.every(Boolean)) return false;
      if (scheduledDrafts.current.size === 0 && inFlightFlushes.current.size === 0) return true;
    }
  }

  function markDirty(candidateId: string) {
    setSaveState(candidateId, "dirty");
  }

  function schedule(candidate: CandidateViewModel, previous?: CandidateViewModel) {
    markDirty(candidate.id);
    queue.current.supersede(candidate.id);
    if (previous && candidateInferenceChanged(previous.raw.inputs, candidate.raw.inputs)) {
      onPreview(candidate.id, null, candidateInputIdentity(candidate.raw.inputs), candidate.raw.revision);
      previewControllers.current.get(candidate.id)?.abort();
    }
    const timer = scheduled.current.get(candidate.id);
    if (timer) clearTimeout(timer);
    scheduledDrafts.current.set(candidate.id, { candidate, previous });
    scheduled.current.set(candidate.id, setTimeout(() => {
      scheduled.current.delete(candidate.id);
      scheduledDrafts.current.delete(candidate.id);
      void flush(candidate, previous);
    }, 250));
  }

  function reload(candidateId: string) {
    const serverCandidate = authoritative.current.get(candidateId);
    if (!serverCandidate) return;
    const timer = scheduled.current.get(candidateId);
    if (timer) clearTimeout(timer);
    scheduled.current.delete(candidateId);
    scheduledDrafts.current.delete(candidateId);
    queue.current.supersede(candidateId);
    setCandidates((items) => items.map((item) => item.id === candidateId ? fromApiCandidate(serverCandidate) : item));
    setFieldErrors((current) => ({ ...current, [candidateId]: [] }));
    setSaveState(candidateId, "idle");
    onPreview(candidateId, null);
    onNotify("success", "サーバー上の候補を再読込しました");
  }

  async function copyDraft(candidate: CandidateViewModel) {
    try {
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(JSON.stringify(toApiCandidate(candidate), null, 2));
      onNotify("success", "編集中の候補をクリップボードへコピーしました");
    } catch {
      onNotify("error", "クリップボードへコピーできません。ブラウザの権限を確認してください");
    }
  }

  useEffect(() => () => {
    for (const timer of scheduled.current.values()) clearTimeout(timer);
    scheduled.current.clear();
    scheduledDrafts.current.clear();
    for (const controller of previewControllers.current.values()) controller.abort();
    previewControllers.current.clear();
  }, [projectId]);

  return { acceptServerCandidates, copyDraft, fieldErrors, flush, reload, saveStates, schedule, settlePending };
}
