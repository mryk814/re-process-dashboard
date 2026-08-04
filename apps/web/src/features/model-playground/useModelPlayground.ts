import { useCallback, useEffect, useState } from "react";
import {
  workbenchApi,
  type ApiModelExplorationRun,
  type ApiModelPlaygroundPreview,
} from "../../shared/api/workbench-api";
import type { ModelPlaygroundPageState } from "./ModelPlaygroundPage";
import type { PlaygroundAttemptView } from "./modelPlaygroundPresentation";
import {
  presentModelExplorationRun,
  presentModelPlaygroundPreview,
} from "./modelPlaygroundAdapter";

export type ModelPlaygroundLocation = Readonly<{
  runId?: string;
  taskId?: string;
  profileRevisionId?: string;
  trainingSnapshotId?: string;
}>;

export function useModelPlayground(
  location: ModelPlaygroundLocation,
  onRunCreated: (runId: string, target?: string) => void,
) {
  const [preview, setPreview] = useState<ApiModelPlaygroundPreview | null>(null);
  const [run, setRun] = useState<ApiModelExplorationRun | null>(null);
  const [state, setState] = useState<ModelPlaygroundPageState>({ kind: "loading" });
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyAttemptId, setBusyAttemptId] = useState<string>();
  const [requestVersion, setRequestVersion] = useState(0);

  const acceptRun = useCallback((next: ApiModelExplorationRun) => {
    setRun(next);
    setState({ kind: "run", run: presentModelExplorationRun(next) });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    setActionError("");
    setPreview(null);
    setRun(null);
    const request = location.runId
      ? workbenchApi.modelExplorationRun(location.runId, controller.signal)
        .then(acceptRun)
      : location.taskId
        && location.profileRevisionId
        && location.trainingSnapshotId
        ? workbenchApi.modelPlaygroundPreview(
          location.taskId,
          location.profileRevisionId,
          location.trainingSnapshotId,
          controller.signal,
        ).then((next) => {
          setPreview(next);
          setState({
            kind: "preview",
            preview: presentModelPlaygroundPreview(next),
          });
        })
        : Promise.reject(new Error(
          "Model LibraryでTraining Snapshotを固定したPackageから開いてください。",
        ));
    void request.catch((cause: unknown) => {
      if (!controller.signal.aborted) {
        setState({
          kind: "error",
          message: cause instanceof Error
            ? cause.message
            : "Model Playgroundを読み込めませんでした。",
        });
      }
    });
    return () => controller.abort();
  }, [
    acceptRun,
    location.profileRevisionId,
    location.runId,
    location.taskId,
    location.trainingSnapshotId,
    requestVersion,
  ]);

  useEffect(() => {
    if (!location.runId || !run?.attempts.some((attempt) => attempt.status === "running")) {
      return undefined;
    }
    const timer = window.setTimeout(() => {
      void workbenchApi.modelExplorationRun(location.runId!)
        .then(acceptRun)
        .catch(() => undefined);
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [acceptRun, location.runId, run]);

  const createRun = useCallback(async (
    recipeIds: readonly string[],
    budget: "quick" | "standard" | "research",
  ) => {
    if (!preview) return;
    setBusy(true);
    setActionError("");
    try {
      let current = await workbenchApi.createModelExplorationRun({
        task_id: preview.context.task_id,
        profile_revision_id: preview.context.profile_revision_id,
        training_snapshot_id: preview.context.training_snapshot_id,
        selected_recipe_ids: [...recipeIds],
        compute_budget: budget,
      });
      acceptRun(current);
      onRunCreated(
        current.run_id,
        current.definition.context.targets[0]?.target_key,
      );
      for (const recipeId of recipeIds) {
        setBusyAttemptId(recipeId);
        current = await workbenchApi.executeModelExplorationRecipe(
          current.run_id,
          recipeId,
          current.execution_revision,
        );
        acceptRun(current);
      }
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "Model Playground Runを実行できませんでした。");
    } finally {
      setBusyAttemptId(undefined);
      setBusy(false);
    }
  }, [acceptRun, onRunCreated, preview]);

  const retry = useCallback(async (attempt: PlaygroundAttemptView) => {
    if (!run) return;
    setBusyAttemptId(attempt.attemptId);
    setActionError("");
    try {
      acceptRun(await workbenchApi.executeModelExplorationRecipe(
        run.run_id,
        attempt.recipeId,
        run.execution_revision,
      ));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "recipeを再試行できませんでした。");
    } finally {
      setBusyAttemptId(undefined);
    }
  }, [acceptRun, run]);

  const register = useCallback(async (attempt: PlaygroundAttemptView) => {
    if (!run) return;
    setBusyAttemptId(attempt.attemptId);
    setActionError("");
    try {
      acceptRun(await workbenchApi.registerModelExplorationAttempt(
        run.run_id,
        attempt.attemptId,
        run.execution_revision,
      ));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "Model Libraryへ登録できませんでした。");
    } finally {
      setBusyAttemptId(undefined);
    }
  }, [acceptRun, run]);

  const saveMemo = useCallback(async (memo: {
    decision: "adopt" | "no_adopt" | "continue_research";
    recipeId?: string;
    rationale: string;
  }) => {
    if (!run) return;
    setActionError("");
    try {
      acceptRun(await workbenchApi.saveModelExplorationAdoptionMemo(
        run.run_id,
        {
          expected_revision: run.execution_revision,
          adopted_recipe_id: memo.recipeId,
          decision: memo.decision,
          rationale: memo.rationale,
        },
      ));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "Adoption memoを保存できませんでした。");
    }
  }, [acceptRun, run]);

  return {
    state,
    actionError,
    busy,
    busyAttemptId,
    retryLoad: () => setRequestVersion((value) => value + 1),
    createRun,
    retry,
    register,
    saveMemo,
    rawRun: run,
  };
}

