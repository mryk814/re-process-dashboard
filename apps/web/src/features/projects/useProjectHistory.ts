import { useCallback, useEffect, useRef, useState } from "react";
import { workbenchApi, type ApiProjectHistory } from "../../shared/api/workbench-api";
import { isCurrentProjectHistoryRequest } from "./projectHistoryRequest";

export function useProjectHistory(projectId: string) {
  const [history, setHistory] = useState<ApiProjectHistory | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;

  const reload = useCallback(async (signal?: AbortSignal, expectedProjectId = projectId) => {
    const loaded = await workbenchApi.projectHistory(expectedProjectId, signal);
    if (isCurrentProjectHistoryRequest(
      expectedProjectId,
      projectIdRef.current,
      signal?.aborted,
    )) {
      setHistory(loaded);
      setState("ready");
    }
  }, [projectId]);

  const retry = useCallback(() => {
    const expectedProjectId = projectIdRef.current;
    setState("loading");
    void reload(undefined, expectedProjectId).catch(() => {
      if (isCurrentProjectHistoryRequest(expectedProjectId, projectIdRef.current)) {
        setState("error");
      }
    });
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    setHistory(null);
    setState("loading");
    void reload(controller.signal).catch(() => {
      if (isCurrentProjectHistoryRequest(
        projectId,
        projectIdRef.current,
        controller.signal.aborted,
      )) {
        setState("error");
      }
    });
    return () => controller.abort();
  }, [projectId, reload]);

  return { history, state, reload, retry };
}
