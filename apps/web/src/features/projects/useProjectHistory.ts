import { useCallback, useEffect, useRef, useState } from "react";
import { workbenchApi, type ApiProjectHistory } from "../../shared/api/workbench-api";

export function useProjectHistory(projectId: string) {
  const [history, setHistory] = useState<ApiProjectHistory | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;

  const reload = useCallback(async (signal?: AbortSignal, expectedProjectId = projectId) => {
    const loaded = await workbenchApi.projectHistory(expectedProjectId, signal);
    if (!signal?.aborted && projectIdRef.current === expectedProjectId) {
      setHistory(loaded);
      setState("ready");
    }
  }, [projectId]);

  const retry = useCallback(() => {
    setState("loading");
    void reload().catch(() => setState("error"));
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    setHistory(null);
    setState("loading");
    void reload(controller.signal).catch(() => {
      if (!controller.signal.aborted) setState("error");
    });
    return () => controller.abort();
  }, [projectId, reload]);

  return { history, state, reload, retry };
}
