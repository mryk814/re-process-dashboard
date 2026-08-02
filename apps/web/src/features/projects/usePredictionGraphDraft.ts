import { useCallback, useEffect, useState } from "react";

import { ApiClientError } from "../../shared/api/client";
import type {
  ApiPredictionGraphDraftConflict,
  ApiPredictionGraphDraftContent,
  ApiPredictionGraphDraftDocument,
} from "../../shared/api/workbench-api";
import { workbenchApi } from "../../shared/api/workbench-api";
import {
  predictionGraphDraftIdFromLocation,
  predictionGraphDraftUrl,
} from "./predictionGraphDraftPersistence";

type DraftPhase = "loading" | "ready" | "saving";

export function usePredictionGraphDraft() {
  const [requestedDraftId, setRequestedDraftId] = useState(
    () => predictionGraphDraftIdFromLocation(window.location.search, window.location.hash),
  );
  const [phase, setPhase] = useState<DraftPhase>("loading");
  const [document, setDocument] = useState<ApiPredictionGraphDraftDocument>();
  const [resumedDocument, setResumedDocument] = useState<ApiPredictionGraphDraftDocument>();
  const [conflict, setConflict] = useState<ApiPredictionGraphDraftConflict>();
  const [error, setError] = useState<string>();
  const [resumeFailed, setResumeFailed] = useState(false);

  const resume = useCallback(async (signal?: AbortSignal) => {
    if (!requestedDraftId) {
      setError(undefined);
      setResumeFailed(false);
      setPhase("ready");
      return;
    }
    setPhase("loading");
    setError(undefined);
    setResumeFailed(false);
    try {
      const current = await workbenchApi.predictionGraphDraft(requestedDraftId, signal);
      if (signal?.aborted) return;
      setDocument(current);
      setResumedDocument(current);
    } catch (reason) {
      if (signal?.aborted) return;
      setResumeFailed(true);
      setError(reason instanceof ApiClientError && reason.kind === "not_found"
        ? `draft ${requestedDraftId} は現在のWorkspaceにありません。`
        : reason instanceof Error ? reason.message : "保存済みdraftを再開できませんでした。");
    } finally {
      if (!signal?.aborted) setPhase("ready");
    }
  }, [requestedDraftId]);

  useEffect(() => {
    const controller = new AbortController();
    void resume(controller.signal);
    return () => controller.abort();
  }, [resume]);

  useEffect(() => {
    if (!document) return;
    window.history.replaceState(
      window.history.state,
      "",
      predictionGraphDraftUrl(window.location.href, document.draft_id),
    );
  }, [document]);

  const save = useCallback(async (
    content: ApiPredictionGraphDraftContent,
    expectedVersion?: number,
  ): Promise<ApiPredictionGraphDraftDocument | undefined> => {
    setPhase("saving");
    setError(undefined);
    setConflict(undefined);
    try {
      if (!document) {
        const created = await workbenchApi.createPredictionGraphDraft(content);
        setDocument(created);
        return created;
      }
      const result = await workbenchApi.updatePredictionGraphDraft(
        document.draft_id,
        expectedVersion ?? document.version,
        content,
      );
      if (result.status === "conflict") {
        setConflict(result.conflict);
        return undefined;
      }
      setDocument(result.document);
      return result.document;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Prediction Graph draftを保存できませんでした。");
      return undefined;
    } finally {
      setPhase("ready");
    }
  }, [document]);

  const useServerVersion = useCallback(() => {
    if (!conflict) return undefined;
    setDocument(conflict.current);
    setConflict(undefined);
    setError(undefined);
    return conflict.current;
  }, [conflict]);

  const overwriteServerVersion = useCallback((
    content: ApiPredictionGraphDraftContent,
  ) => conflict ? save(content, conflict.current.version) : Promise.resolve(undefined), [conflict, save]);

  const startNewDraft = useCallback(() => {
    window.history.replaceState(
      window.history.state,
      "",
      predictionGraphDraftUrl(window.location.href, undefined),
    );
    setDocument(undefined);
    setResumedDocument(undefined);
    setConflict(undefined);
    setError(undefined);
    setResumeFailed(false);
    setPhase("ready");
    setRequestedDraftId(undefined);
  }, []);

  return {
    phase,
    requestedDraftId: document?.draft_id ?? requestedDraftId,
    document,
    resumedDocument,
    conflict,
    error,
    resumeFailed,
    retryResume: resume,
    startNewDraft,
    save,
    useServerVersion,
    overwriteServerVersion,
  };
}
