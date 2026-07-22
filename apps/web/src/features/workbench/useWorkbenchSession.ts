import { useEffect, useRef, useState } from "react";
import type { CandidateProvenance } from "../../shared/candidateProvenance";
import { ApiClientError } from "../../shared/api/client";
import { candidateInferencePrefix, candidateInputIdentity, inferenceRequestCache } from "../../shared/api/inferenceRequestCache";
import {
  workbenchApi,
  type ApiCandidateInput,
  type ApiPreview,
  type ApiProject,
} from "../../shared/api/workbench-api";
import {
  fromApiCandidate,
  setCandidateInputValue,
  toApiCandidate,
  useCandidateEditor,
  validateResolvedTaskDefinition,
  type CandidateViewModel,
  type ResolvedTaskDefinition,
  type TaskDefinitionContract,
} from "../candidates";
import { loadSelectedFirstBounded } from "./boundedPreviewLoader";
import { useWorkbenchPrediction } from "./useWorkbenchPrediction";

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
  const [apiState, setApiState] = useState<"ready" | "loading" | "offline">("loading");
  const [notice, setNotice] = useState("候補を読み込んでいます");
  const [brokenOriginCandidateId, setBrokenOriginCandidateId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ApiProject[]>([]);
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
  const operations = resolvedTaskDefinition?.runtime_capability.operations;
  const prediction = useWorkbenchPrediction({
    projectId: activeProjectId,
    taskId,
    candidate: selected,
    operations,
    onNotice: setNotice,
    setApiState,
  });
  const editor = useCandidateEditor({
    projectId: activeProjectId,
    setCandidates,
    previewAvailable: operations?.preview === true,
    getPreviewInputIdentity: prediction.getPreviewInputIdentity,
    onPreview: prediction.acceptPreview,
    onNotice: setNotice,
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
    setNotice(
      requestedCandidateMissing
        ? "参照元の候補は削除済みか、このプロジェクトから参照できません"
        : imported.length
          ? "プロジェクトを切り替えました"
          : "候補がありません。過去条件または新規入力から追加できます",
    );
    if (!imported.length || !resolved.runtime_capability.operations.preview) return;
    const previewEntries = await loadSelectedFirstBounded<CandidateViewModel, ApiPreview>({
      items: imported.filter((candidate) => !candidate.raw.archived_at),
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
      imported.filter((candidate) => candidate.id !== nextSelectedId),
      candidatesRef.current,
      Object.fromEntries(backgroundEntries),
      definition.id,
    );
  }

  async function openLocation(projectId: string, candidateId?: string) {
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

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        const available = await workbenchApi.listProjects();
        const requested = initialLocation.current.requestedProjectId;
        const remembered = window.localStorage.getItem("material-workbench-project");
        const projectId = available.some((project) => project.id === requested)
          ? requested!
          : available.some((project) => project.id === remembered)
            ? remembered!
            : (available[0]?.id ?? "default");
        if (cancelled) return;
        setProjects(available);
        await loadProject(
          projectId,
          initialLocation.current.requestedProjectId === projectId
            ? initialLocation.current.requestedCandidateId
            : undefined,
        );
      } catch (error) {
        if (cancelled) return;
        setApiState("offline");
        setLoadError(`APIから候補を読み込めませんでした（${error instanceof Error ? error.message : "不明なエラー"}）。`);
        setNotice("API未接続: 予測結果は表示できません");
      }
    }
    void bootstrap();
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
          setNotice("作成元候補を一時的に確認できません。API接続を確認してください。");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.id]);

  function updateCandidateInput(id: string, path: string, value: number | string) {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const next: CandidateViewModel = {
      ...current,
      raw: { ...current.raw, inputs: setCandidateInputValue(current.raw.inputs, path, value) },
    };
    setCandidates((items) => items.map((candidate) => candidate.id === id ? next : candidate));
    editor.schedule(next, current);
  }

  function updateCandidateText(id: string, field: "label", value: string) {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const next: CandidateViewModel = { ...current, label: value };
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
    if (candidates.length >= 10) {
      setNotice("比較候補は最大10件です。不要な候補を削除してから追加してください");
      return;
    }
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
      setNotice("候補を追加しました");
    } catch {
      setApiState("offline");
      setNotice("候補を追加できませんでした。API接続を確認してください。");
    }
  }

  async function addCandidateFromLineage(entityKey: string): Promise<boolean> {
    if (candidatesRef.current.length >= 10) {
      setNotice("比較候補は最大10件です。不要な候補を削除してから追加してください");
      return false;
    }
    try {
      const created = fromApiCandidate(await workbenchApi.createCandidateFromLineage(entityKey, activeProjectId));
      appendCandidate(created);
      selectCandidate(created.id);
      setNotice("近い過去実績を候補に追加しました");
      return true;
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "過去実績を候補に追加できませんでした。");
      return false;
    }
  }

  async function createStarterCandidate() {
    try {
      const catalog = await workbenchApi.listTaskDefinitions();
      const starter = catalog.find((item) => item.definition.task_definition.id === taskId)?.starter_candidate;
      if (!starter) throw new Error("予測タスクの基準候補を取得できませんでした");
      const created = fromApiCandidate(await workbenchApi.createCandidate(activeProjectId, starter));
      setCandidates([created]);
      selectCandidate(created.id);
      setNotice("基準候補を作成しました");
      setApiState("ready");
    } catch {
      setNotice("基準候補を作成できませんでした。API接続を確認してください。");
    }
  }

  async function copyCandidate(candidateId = selectedId) {
    const source = candidatesRef.current.find((candidate) => candidate.id === candidateId);
    if (!source) return;
    const requestProjectId = activeProjectIdRef.current;
    if (candidatesRef.current.length >= 10) {
      setNotice("比較候補は最大10件です。不要な候補を削除してから追加してください");
      return;
    }
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
      setNotice("由来を保持して候補をコピーしました");
    } catch (cause) {
      if (activeProjectIdRef.current === requestProjectId) setNotice(cause instanceof Error ? cause.message : "候補をコピーできませんでした。");
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
      setNotice("候補を一覧から外しました");
    } catch {
      if (activeProjectIdRef.current === requestProjectId) setNotice("候補を削除できませんでした。API接続を確認してください。");
    } finally {
      deletingCandidateIds.current.delete(candidateId);
    }
  }

  async function deleteProject(projectId: string): Promise<boolean> {
    try {
      await workbenchApi.deleteProject(projectId);
      const remaining = projects.filter((project) => project.id !== projectId);
      setProjects(remaining);
      if (projectId === activeProjectIdRef.current) {
        const nextProject = remaining[0];
        if (nextProject) await loadProject(nextProject.id);
      }
      setNotice("プロジェクトを削除しました");
      return true;
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "プロジェクトを削除できませんでした");
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
      setNotice("別プロジェクトの保存結果は現在の候補へ混在できません");
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
        setNotice("コピー元候補は削除済みか、このプロジェクトから参照できません");
      } else {
        setBrokenOriginCandidateId(null);
        setNotice("作成元候補を一時的に確認できません。API接続を確認してください。");
      }
    }
  }

  async function refreshProjectDefinition(project: ApiProject) {
    setProjects((items) => items.some((item) => item.id === project.id)
      ? items.map((item) => item.id === project.id ? project : item)
      : [...items, project]);
    if (project.id !== activeProjectId || activeProject?.task_id === project.task_id) return;
    await loadProject(project.id, selectedId || undefined);
  }

  async function refreshAdminProject(project: ApiProject) {
    setProjects((items) => items.map((item) => item.id === project.id ? project : item));
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
    brokenOriginCandidateId,
    candidates,
    copyCandidate,
    createStarterCandidate,
    deleteCandidate,
    deleteProject,
    deleteHeatPoint,
    editor,
    loadError,
    loadProject,
    notice,
    openLocation,
    openOrigin,
    operations,
    prediction,
    projects,
    refreshAdminProject,
    refreshProjectDefinition,
    resolvedTaskDefinition,
    restoreCandidate,
    selected,
    selectedId,
    selectCandidate,
    setNotice,
    taskDefinition,
    updateCandidateInput,
    updateCandidateHeat,
    updateCandidateText,
    updateHeat,
    acceptCandidate,
  };
}
