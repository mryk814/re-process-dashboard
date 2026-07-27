import { useEffect, useRef, useState } from "react";
import type { CandidateProvenance } from "../../shared/candidateProvenance";
import type { WorkspaceNotice, WorkspaceNoticeKind } from "../../shared/workspaceNotice";
import { ApiClientError } from "../../shared/api/client";
import { apiStartupRetryDelayMs, shouldKeepWaitingForApi } from "./apiStartupWait";
import { candidateInferencePrefix, candidateInputIdentity, inferenceRequestCache } from "../../shared/api/inferenceRequestCache";
import {
  workbenchApi,
  type ApiCandidateInput,
  type ApiPreview,
  type ApiProject,
} from "../../shared/api/workbench-api";
import {
  fromApiCandidate,
  scaleHeatTimesForLineSpeed,
  setCandidateInputValue,
  toApiCandidate,
  useCandidateEditor,
  validateResolvedTaskDefinition,
  type CandidateViewModel,
  type HeatTimeBasis,
  type ResolvedTaskDefinition,
  type TaskDefinitionContract,
} from "../candidates";
import { loadSelectedFirstBounded } from "./boundedPreviewLoader";
import { useWorkbenchPrediction } from "./useWorkbenchPrediction";

const INITIAL_PROJECT_PREVIEW_LIMIT = 5;

type WorkbenchSessionOptions = {
  requestedProjectId?: string;
  requestedCandidateId?: string;
  onLocationReplace: (projectId: string, candidateId?: string) => void;
  onCandidateSelected: (projectId: string, candidateId: string) => void;
  onOpenProvenance: (provenance: CandidateProvenance) => void;
};

export function useWorkbenchSession({
  requestedProjectId,
  requestedCandidateId,
  onLocationReplace,
  onCandidateSelected,
  onOpenProvenance,
}: WorkbenchSessionOptions) {
  const [candidates, setCandidates] = useState<CandidateViewModel[]>([]);
  const candidatesRef = useRef(candidates);
  candidatesRef.current = candidates;
  const [selectedId, setSelectedId] = useState("");
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const deletingCandidateIds = useRef(new Set<string>());
  const [apiState, setApiState] = useState<"ready" | "loading" | "starting" | "offline">("loading");
  const [apiStartedWaitingAt, setApiStartedWaitingAt] = useState<number | null>(null);
  const [notice, setNotice] = useState<WorkspaceNotice | null>(null);
  const noticeSequence = useRef(0);
  function notify(kind: WorkspaceNoticeKind, message: string) {
    noticeSequence.current += 1;
    setNotice({ id: noticeSequence.current, kind, message });
  }
  const notifySuccess = (message: string) => notify("success", message);
  const notifyError = (message: string) => notify("error", message);
  const dismissNotice = () => setNotice(null);
  const [brokenOriginCandidateId, setBrokenOriginCandidateId] = useState<string | null>(null);
  const [loadingRemainingPreviews, setLoadingRemainingPreviews] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ApiProject[]>([]);
  const projectsRef = useRef(projects);
  projectsRef.current = projects;
  const [taskDefinition, setTaskDefinition] = useState<TaskDefinitionContract | null>(null);
  const [resolvedTaskDefinition, setResolvedTaskDefinition] = useState<ResolvedTaskDefinition | null>(null);
  const [activeProjectId, setActiveProjectId] = useState("default");
  const activeProjectIdRef = useRef(activeProjectId);
  activeProjectIdRef.current = activeProjectId;
  const initialLocation = useRef({ requestedProjectId, requestedCandidateId });
  const loadSequence = useRef(0);
  const loadPreviewController = useRef<AbortController | null>(null);
  const selected = candidates.find((candidate) => candidate.id === selectedId);
  const activeProject = projects.find((project) => project.id === activeProjectId);
  const taskId = taskDefinition?.id ?? activeProject?.task_id ?? "";
  const taskAvailability = resolvedTaskDefinition?.availability;
  const taskAvailable = taskAvailability?.status !== "unavailable";
  const operations = taskAvailable ? resolvedTaskDefinition?.runtime_capability.operations : undefined;
  const prediction = useWorkbenchPrediction({
    projectId: activeProjectId,
    taskId,
    candidate: selected,
    operations,
    onNotify: notify,
    setApiState,
  });
  const editor = useCandidateEditor({
    projectId: activeProjectId,
    setCandidates,
    previewAvailable: operations?.preview === true,
    getPreviewInputIdentity: prediction.getPreviewInputIdentity,
    onPreview: prediction.acceptPreview,
    onNotify: notify,
  });

  function selectCandidate(candidateId: string, notifyLocation = true) {
    selectedIdRef.current = candidateId;
    setSelectedId(candidateId);
    if (notifyLocation) onCandidateSelected(activeProjectIdRef.current, candidateId);
  }

  function appendCandidate(candidate: CandidateViewModel) {
    if (candidatesRef.current.some((item) => item.id === candidate.id)) return candidatesRef.current;
    const next = [...candidatesRef.current, candidate];
    candidatesRef.current = next;
    setCandidates(next);
    return next;
  }

  async function loadProject(projectId: string, candidateId?: string) {
    const sequence = ++loadSequence.current;
    loadPreviewController.current?.abort();
    const previewController = new AbortController();
    loadPreviewController.current = previewController;
    // The backend owns versioned Package/Pipeline/support identities. A full
    // project load starts a new renderer cache epoch so completed transport
    // responses cannot bypass those server-side identities after a runtime reload.
    inferenceRequestCache.invalidatePrefix(candidateInferencePrefix(projectId));
    setApiState("loading");
    setLoadError(null);
    const project = projectsRef.current.find((item) => item.id === projectId);
    if (project?.scientific_identity?.identity_kind === "chain") {
      activeProjectIdRef.current = projectId;
      setActiveProjectId(projectId);
      setTaskDefinition(null);
      setResolvedTaskDefinition(null);
      editor.acceptServerCandidates([]);
      candidatesRef.current = [];
      setCandidates([]);
      selectedIdRef.current = "";
      setSelectedId("");
      window.localStorage.setItem("material-workbench-project", projectId);
      onLocationReplace(projectId, candidateId);
      prediction.reset();
      setApiState("ready");
      setNotice(null);
      return;
    }
    const [listedCandidates, resolved] = await Promise.all([
      workbenchApi.listCandidates(projectId),
      workbenchApi.taskDefinition(projectId),
    ]);
    let apiCandidates = listedCandidates;
    let requestedCandidateMissing = false;
    if (candidateId && !listedCandidates.some((candidate) => candidate.id === candidateId)) {
      try {
        const requestedCandidate = await workbenchApi.candidate(projectId, candidateId, true);
        apiCandidates = [...listedCandidates, requestedCandidate];
      } catch (cause) {
        if (!(cause instanceof ApiClientError) || cause.status !== 404) throw cause;
        requestedCandidateMissing = true;
      }
    }
    const imported = apiCandidates.map(fromApiCandidate);
    validateResolvedTaskDefinition(resolved);
    const definition = resolved.task_definition;
    if (sequence !== loadSequence.current) return;
    editor.acceptServerCandidates(apiCandidates);
    activeProjectIdRef.current = projectId;
    setActiveProjectId(projectId);
    setTaskDefinition(definition);
    setResolvedTaskDefinition(resolved);
    window.localStorage.setItem("material-workbench-project", projectId);
    candidatesRef.current = imported;
    setCandidates(imported);
    const nextSelectedId = imported.some((candidate) => candidate.id === candidateId)
      ? candidateId!
      : (imported[0]?.id ?? "");
    selectedIdRef.current = nextSelectedId;
    setSelectedId(nextSelectedId);
    onLocationReplace(projectId, nextSelectedId || undefined);
    prediction.reset();
    setApiState("ready");
    setNotice(null);
    if (requestedCandidateMissing) notifyError("参照元の候補は削除済みか、このプロジェクトから参照できません");
    if (
      resolved.availability.status === "unavailable"
      || !imported.length
      || !resolved.runtime_capability.operations.preview
    ) return;
    // Preview loading is best effort. A failure here belongs to the preview surface,
    // never to the connection state: project, contract and candidates are already loaded.
    try {
      const activeCandidates = imported.filter((candidate) => !candidate.raw.archived_at);
    const selectedCandidate = activeCandidates.find((candidate) => candidate.id === nextSelectedId);
    const initialPreviewCandidates = [
      ...(selectedCandidate ? [selectedCandidate] : []),
      ...activeCandidates.filter((candidate) => candidate.id !== nextSelectedId),
    ].slice(0, INITIAL_PROJECT_PREVIEW_LIMIT);
    const previewEntries = await loadSelectedFirstBounded<CandidateViewModel, ApiPreview>({
      items: initialPreviewCandidates,
      selectedId: nextSelectedId,
      concurrency: 2,
      signal: previewController.signal,
      onSelectedLoaded: (candidate, loaded) => {
        if (sequence !== loadSequence.current || previewController.signal.aborted) return;
        prediction.acceptProjectPreviews(
          [candidate],
          candidatesRef.current,
          { [candidate.id]: loaded },
          definition.id,
        );
      },
      load: async (candidate) => {
        const inputIdentity = candidateInputIdentity(candidate.raw.inputs);
        try {
          const loaded = await workbenchApi.previewCandidate(
            projectId,
            candidate.id,
            candidate.raw.revision,
            inputIdentity,
            previewController.signal,
          );
          if (sequence !== loadSequence.current || previewController.signal.aborted) return null;
          return loaded;
        } catch {
          return null;
        }
      },
    });
    if (sequence !== loadSequence.current || previewController.signal.aborted) return;
    const backgroundEntries = previewEntries.filter(([candidateId]) => candidateId !== nextSelectedId);
      prediction.acceptProjectPreviews(
        initialPreviewCandidates.filter((candidate) => candidate.id !== nextSelectedId),
        candidatesRef.current,
        Object.fromEntries(backgroundEntries),
        definition.id,
      );
    } catch {
      // Keep the loaded project usable; the comparison surface reports the preview error.
    }
  }

  /**
   * The initial load previews a bounded set so a large project opens quickly. The
   * comparison is incomplete until the rest are computed, so completing it is an
   * explicit action instead of a silent dash.
   */
  async function loadRemainingPreviews() {
    const definition = taskDefinition;
    if (!definition || !operations?.preview || loadingRemainingPreviews) return;
    const projectId = activeProjectIdRef.current;
    const pending = candidatesRef.current.filter((candidate) => (
      !candidate.raw.archived_at && !prediction.previewsByCandidate[candidate.id]
    ));
    if (!pending.length) return;
    setLoadingRemainingPreviews(true);
    const controller = new AbortController();
    try {
      await loadSelectedFirstBounded<CandidateViewModel, ApiPreview>({
        items: pending,
        selectedId: selectedIdRef.current,
        concurrency: 2,
        signal: controller.signal,
        load: async (candidate) => {
          try {
            const loaded = await workbenchApi.previewCandidate(
              projectId,
              candidate.id,
              candidate.raw.revision,
              candidateInputIdentity(candidate.raw.inputs),
              controller.signal,
            );
            if (controller.signal.aborted || activeProjectIdRef.current !== projectId) return null;
            // Accept each result as it arrives so rows fill in visibly.
            prediction.acceptProjectPreviews(
              [candidate],
              candidatesRef.current,
              { [candidate.id]: loaded },
              definition.id,
            );
            return loaded;
          } catch {
            return null;
          }
        },
      });
    } finally {
      setLoadingRemainingPreviews(false);
    }
  }

  async function openLocation(projectId: string, candidateId?: string) {
    const currentProject = projectsRef.current.find((item) => item.id === projectId);
    if (
      projectId === activeProjectIdRef.current
      && currentProject?.scientific_identity?.identity_kind === "chain"
    ) {
      return;
    }
    if (
      projectId !== activeProjectIdRef.current
      || (candidateId && !candidatesRef.current.some((candidate) => candidate.id === candidateId))
    ) {
      await loadProject(projectId, candidateId);
    } else if (candidateId) {
      selectedIdRef.current = candidateId;
      setSelectedId(candidateId);
    }
  }

  async function openWorkspace(cancelled = () => false) {
    const startedAt = Date.now();
    for (let attempt = 1; ; attempt += 1) {
      try {
        setLoadError(null);
        const available = await workbenchApi.listProjects();
        const requested = initialLocation.current.requestedProjectId;
        const remembered = window.localStorage.getItem("material-workbench-project");
        const projectId = available.some((project) => project.id === requested)
          ? requested!
          : available.some((project) => project.id === remembered)
            ? remembered!
            : (available[0]?.id ?? "default");
        if (cancelled()) return;
        projectsRef.current = available;
        setProjects(available);
        await loadProject(
          projectId,
          initialLocation.current.requestedProjectId === projectId
            ? initialLocation.current.requestedCandidateId
            : undefined,
        );
        return;
      } catch (error) {
        if (cancelled()) return;
        const elapsed = Date.now() - startedAt;
        // 起動待ちのうちは自動で再試行し、経過時間を出す。手動再試行を求めない。
        if (shouldKeepWaitingForApi(error, elapsed)) {
          setApiState("starting");
          setApiStartedWaitingAt(startedAt);
          await new Promise((resolve) => {
            window.setTimeout(resolve, apiStartupRetryDelayMs(attempt));
          });
          if (cancelled()) return;
          continue;
        }
        setApiState("offline");
        setLoadError(`APIから候補を読み込めませんでした（${error instanceof Error ? error.message : "不明なエラー"}）。`);
        return;
      }
    }
  }

  // The shell offers one retry for a workspace that could not be opened.
  async function retryOpenWorkspace() {
    setApiState("loading");
    await openWorkspace();
  }

  useEffect(() => {
    let cancelled = false;
    void openWorkspace(() => cancelled);
    return () => {
      cancelled = true;
      loadPreviewController.current?.abort();
    };
  }, []);

  useEffect(() => {
    const provenance = selected?.raw.provenance as CandidateProvenance | undefined;
    if (!selected || provenance?.source_kind !== "copy") {
      setBrokenOriginCandidateId(null);
      return;
    }
    let cancelled = false;
    workbenchApi.candidate(provenance.source_ref.project_id, provenance.source_ref.candidate_id, true)
      .then(() => {
        if (!cancelled) setBrokenOriginCandidateId(null);
      })
      .catch((cause) => {
        if (cancelled) return;
        if (cause instanceof ApiClientError && cause.status === 404) {
          setBrokenOriginCandidateId(selected.id);
        } else {
          setBrokenOriginCandidateId(null);
          notifyError("作成元候補を一時的に確認できません。API接続を確認してください。");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.id]);

  function updateCandidateInput(id: string, path: string, value: number | string | undefined) {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const oldLineSpeed = Number(current.raw.inputs.process.ls_mpm);
    const newLineSpeed = Number(value);
    const removesLineSpeed = path === "process.ls_mpm" && value === undefined;
    const next: CandidateViewModel = {
      ...current,
      raw: { ...current.raw, inputs: setCandidateInputValue(current.raw.inputs, path, value) },
      heatTimeBasis: removesLineSpeed ? "elapsed_time" : current.heatTimeBasis,
      heat: path === "process.ls_mpm" && current.heatTimeBasis === "line_speed" && !removesLineSpeed
        ? scaleHeatTimesForLineSpeed(current.heat, oldLineSpeed, newLineSpeed)
        : current.heat,
    };
    setCandidates((items) => items.map((candidate) => candidate.id === id ? next : candidate));
    if (path === "process.ls_mpm") {
      void editor.flush(next, current);
    } else {
      editor.schedule(next, current);
    }
  }

  function updateCandidateHeatTimeBasis(id: string, basis: HeatTimeBasis) {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current || current.heatTimeBasis === basis) return;
    const next = { ...current, heatTimeBasis: basis };
    setCandidates((items) => items.map((candidate) => candidate.id === id ? next : candidate));
    void editor.flush(next, current);
  }

  function updateCandidateText(id: string, field: "label", value: string) {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const next: CandidateViewModel = { ...current, label: value };
    setCandidates((items) => items.map((candidate) => candidate.id === id ? next : candidate));
    editor.schedule(next, current);
  }

  function updateCandidateBlend(
    id: string,
    blend: NonNullable<CandidateViewModel["raw"]["blend"]>,
    lockedMaterialIds?: string[],
  ) {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const next: CandidateViewModel = {
      ...current,
      raw: {
        ...current.raw,
        blend,
        editor_state: lockedMaterialIds === undefined
          ? current.raw.editor_state
          : { locked_material_ids: lockedMaterialIds },
      },
    };
    setCandidates((items) => items.map((candidate) => candidate.id === id ? next : candidate));
    editor.schedule(next, current);
  }

  function updateCandidateBlendLocks(id: string, lockedMaterialIds: string[]) {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current?.raw.blend) return;
    const next: CandidateViewModel = {
      ...current,
      raw: {
        ...current.raw,
        editor_state: { locked_material_ids: lockedMaterialIds },
      },
    };
    setCandidates((items) => items.map((candidate) => candidate.id === id ? next : candidate));
    editor.schedule(next, current);
  }

  function updateHeat(index: number, field: "time" | "temperature" | "stageName", raw: number | string) {
    if (!selected) return;
    updateCandidateHeat(selected.id, index, field, raw);
  }

  function updateCandidateHeat(id: string, index: number, field: "time" | "temperature" | "stageName", raw: number | string) {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const next = {
      ...current,
      heat: current.heat.map((point, pointIndex) => pointIndex === index ? { ...point, [field]: raw } : point),
    };
    setCandidates((items) => items.map((candidate) => candidate.id === id ? next : candidate));
    editor.schedule(next, current);
  }

  function addHeatPoint() {
    if (!selected || selected.heat.length >= 30) return;
    const heat = [...selected.heat];
    const insertAt = Math.max(1, heat.length - 1);
    const before = heat[insertAt - 1];
    const after = heat[insertAt];
    heat.splice(insertAt, 0, {
      time: (before.time + after.time) / 2,
      temperature: (before.temperature + after.temperature) / 2,
    });
    const next = { ...selected, heat };
    setCandidates((items) => items.map((candidate) => candidate.id === selected.id ? next : candidate));
    editor.schedule(next, selected);
  }

  function deleteHeatPoint(index: number) {
    if (!selected || selected.heat.length <= 2) return;
    const next = { ...selected, heat: selected.heat.filter((_, pointIndex) => pointIndex !== index) };
    setCandidates((items) => items.map((candidate) => candidate.id === selected.id ? next : candidate));
    editor.schedule(next, selected);
  }

  async function addCandidate() {
    if (!selected) return;
    try {
      const request = toApiCandidate({
        ...selected,
        label: `候補 ${candidates.length + 1}`,
        heat: selected.heat.map((point) => ({ ...point })),
      });
      request.provenance = { source_kind: "direct", source_ref: null };
      const created = fromApiCandidate(await workbenchApi.createCandidate(activeProjectId, request));
      appendCandidate(created);
      selectCandidate(created.id);
      notifySuccess("候補を追加しました");
    } catch (cause) {
      if (cause instanceof ApiClientError && cause.kind !== "network") {
        notifyError(cause.message);
      } else {
        notifyError("候補を追加できませんでした。API接続を確認してください。");
      }
    }
  }

  async function addCandidateFromLineage(entityKey: string): Promise<boolean> {
    try {
      const created = fromApiCandidate(await workbenchApi.createCandidateFromLineage(entityKey, activeProjectId));
      appendCandidate(created);
      selectCandidate(created.id);
      notifySuccess("近い過去実績を候補に追加しました");
      return true;
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "過去実績を候補に追加できませんでした。");
      return false;
    }
  }

  async function createStarterCandidate() {
    // Failing here has three different causes for the user: this project has no
    // single Task at all, the Task declares no starter, or the API is unreachable.
    if (!taskId) {
      notifyError("このプロジェクトは単一の予測タスクを固定していないため、基準候補を作成できません");
      return;
    }
    try {
      const catalog = await workbenchApi.listTaskDefinitions();
      const starter = catalog.find((item) => item.definition.task_definition.id === taskId)?.starter_candidate;
      if (!starter) {
        notifyError("この予測タスクには基準候補の定義がありません。開発・管理で予測タスク定義を確認してください。");
        return;
      }
      const created = fromApiCandidate(await workbenchApi.createCandidate(activeProjectId, starter));
      setCandidates([created]);
      selectCandidate(created.id);
      notifySuccess("基準候補を作成しました");
      setApiState("ready");
    } catch (cause) {
      notifyError(cause instanceof ApiClientError && cause.kind !== "network"
        ? cause.message
        : "基準候補を作成できませんでした。API接続を確認してください。");
    }
  }

  async function copyCandidate(candidateId = selectedId) {
    const source = candidatesRef.current.find((candidate) => candidate.id === candidateId);
    if (!source) return;
    const requestProjectId = activeProjectIdRef.current;
    try {
      const request: ApiCandidateInput = {
        ...toApiCandidate(source),
        name: `${source.label} のコピー`,
        provenance: {
          source_kind: "copy",
          source_ref: {
            project_id: requestProjectId,
            candidate_id: source.id,
            candidate_revision: source.raw.revision,
          },
        },
      };
      const created = fromApiCandidate(await workbenchApi.createCandidate(requestProjectId, request));
      if (activeProjectIdRef.current !== requestProjectId) return;
      appendCandidate(created);
      selectCandidate(created.id);
      notifySuccess("由来を保持して候補をコピーしました");
    } catch (cause) {
      if (activeProjectIdRef.current === requestProjectId) notifyError(cause instanceof Error ? cause.message : "候補をコピーできませんでした。");
    }
  }

  async function deleteCandidate(candidateId = selectedId) {
    const target = candidatesRef.current.find((candidate) => candidate.id === candidateId);
    if (!target || candidatesRef.current.length === 1 || deletingCandidateIds.current.has(candidateId)) return;
    const requestProjectId = activeProjectIdRef.current;
    deletingCandidateIds.current.add(candidateId);
    try {
      await workbenchApi.deleteCandidate(requestProjectId, target.id, target.raw.revision);
      if (activeProjectIdRef.current !== requestProjectId) return;
      const remaining = candidatesRef.current.filter((candidate) => candidate.id !== target.id);
      candidatesRef.current = remaining;
      setCandidates(remaining);
      if (selectedIdRef.current === target.id && remaining[0]) selectCandidate(remaining[0].id);
      notifySuccess("候補を一覧から外しました");
    } catch {
      if (activeProjectIdRef.current === requestProjectId) notifyError("候補を削除できませんでした。API接続を確認してください。");
    } finally {
      deletingCandidateIds.current.delete(candidateId);
    }
  }

  async function archiveProject(projectId: string): Promise<boolean> {
    try {
      await workbenchApi.archiveProject(projectId);
      const remaining = projects.filter((project) => project.id !== projectId);
      projectsRef.current = remaining;
      setProjects(remaining);
      if (projectId === activeProjectIdRef.current) {
        const nextProject = remaining[0];
        if (nextProject) await loadProject(nextProject.id);
      }
      notifySuccess("プロジェクトをアーカイブしました");
      return true;
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "プロジェクトをアーカイブできませんでした");
      return false;
    }
  }

  async function restoreProject(projectId: string): Promise<boolean> {
    try {
      const restored = await workbenchApi.restoreProject(projectId);
      const nextProjects = [...projectsRef.current, restored]
        .filter((project, index, values) => values.findIndex((item) => item.id === project.id) === index);
      projectsRef.current = nextProjects;
      setProjects(nextProjects);
      await loadProject(restored.id);
      notifySuccess("プロジェクトを復元しました");
      return true;
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "プロジェクトを復元できませんでした");
      return false;
    }
  }

  function acceptCandidate(candidate: CandidateViewModel) {
    appendCandidate(candidate);
    selectedIdRef.current = candidate.id;
    setSelectedId(candidate.id);
    return candidatesRef.current.some((item) => item.id === candidate.id)
      ? candidatesRef.current.length
      : candidatesRef.current.length + 1;
  }

  function restoreCandidate(candidate: CandidateViewModel) {
    if (candidate.raw.project_id !== activeProjectId) {
      notifyError("別プロジェクトの保存結果は現在の候補へ混在できません");
      return false;
    }
    acceptCandidate(candidate);
    onCandidateSelected(activeProjectId, candidate.id);
    return true;
  }

  async function openOrigin() {
    if (!selected) return;
    const provenance = selected.raw.provenance as CandidateProvenance;
    if (provenance.source_kind !== "copy") {
      onOpenProvenance(provenance);
      return;
    }
    try {
      const source = await workbenchApi.candidate(
        provenance.source_ref.project_id,
        provenance.source_ref.candidate_id,
        true,
      );
      if (source.project_id !== activeProjectId) await loadProject(source.project_id, source.id);
      const sourceCandidate = fromApiCandidate(source);
      setCandidates((items) => items.some((item) => item.id === source.id)
        ? items.map((item) => item.id === source.id ? sourceCandidate : item)
        : [...items, sourceCandidate]);
      selectedIdRef.current = source.id;
      setSelectedId(source.id);
      onOpenProvenance(provenance);
    } catch (cause) {
      if (cause instanceof ApiClientError && cause.status === 404) {
        setBrokenOriginCandidateId(selected.id);
        notifyError("コピー元候補は削除済みか、このプロジェクトから参照できません");
      } else {
        setBrokenOriginCandidateId(null);
        notifyError("作成元候補を一時的に確認できません。API接続を確認してください。");
      }
    }
  }

  async function refreshProjectDefinition(project: ApiProject) {
    const nextProjects = projectsRef.current.some((item) => item.id === project.id)
      ? projectsRef.current.map((item) => item.id === project.id ? project : item)
      : [...projectsRef.current, project];
    projectsRef.current = nextProjects;
    setProjects(nextProjects);
    if (project.id !== activeProjectId || (
      activeProject?.task_id === project.task_id
      && activeProject?.scientific_identity?.identity_kind === project.scientific_identity?.identity_kind
    )) return;
    await loadProject(project.id, selectedId || undefined);
  }

  async function refreshAdminProject(project: ApiProject) {
    const nextProjects = projectsRef.current.map((item) => item.id === project.id ? project : item);
    projectsRef.current = nextProjects;
    setProjects(nextProjects);
    if (project.id !== activeProjectId) return;
    try {
      const resolved = await workbenchApi.taskDefinition(project.id);
      validateResolvedTaskDefinition(resolved);
      setResolvedTaskDefinition(resolved);
      setTaskDefinition(resolved.task_definition);
    } catch {
      // The admin surface owns its save error; keep the last valid task contract here.
    }
  }

  return {
    activeProject,
    activeProjectId,
    addCandidate,
    addCandidateFromLineage,
    addHeatPoint,
    apiState,
    apiStartedWaitingAt,
    brokenOriginCandidateId,
    candidates,
    copyCandidate,
    createStarterCandidate,
    deleteCandidate,
    archiveProject,
    restoreProject,
    deleteHeatPoint,
    editor,
    loadError,
    loadingRemainingPreviews,
    loadProject,
    loadRemainingPreviews,
    dismissNotice,
    notice,
    openLocation,
    openOrigin,
    operations,
    prediction,
    projects,
    refreshAdminProject,
    refreshProjectDefinition,
    retryOpenWorkspace,
    resolvedTaskDefinition,
    restoreCandidate,
    selected,
    selectedId,
    selectCandidate,
    notifyError,
    notifySuccess,
    taskDefinition,
    taskAvailability,
    updateCandidateInput,
    updateCandidateHeatTimeBasis,
    updateCandidateHeat,
    updateCandidateText,
    updateCandidateBlend,
    updateCandidateBlendLocks,
    updateHeat,
    acceptCandidate,
  };
}
