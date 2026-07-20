import { PointerEvent, useEffect, useRef, useState } from "react";
import { provenanceLabel, provenanceNavigation, type CandidateProvenance } from "./app/candidateProvenance";
import { navigationUrl, readNavigationIntent, withView, type NavigationIntent, type WorkbenchView } from "./app/navigation";
import { CandidateInspector as TaskDrivenCandidateInspector, ComparisonTable as TaskDrivenComparisonTable, fromApiCandidate, numericTaskInputs, setCandidateInputValue, taskFieldName, toApiCandidate, useCandidateEditor, validateResolvedTaskDefinition, type CandidateSaveState, type CandidateViewModel as Candidate, type NumericTaskInput, type ResolvedTaskDefinition, type RuntimeOperations, type TaskDefinitionContract, type TaskOutputDefinition } from "./features/candidates";
import { useWorkbenchPrediction, type PredictionMetric as Metric } from "./features/workbench/useWorkbenchPrediction";
import { workbenchRequestKey } from "./features/workbench/workbenchIdentity";
import { ProjectHub } from "./features/projects";
import { LineageGraph } from "./features/lineage/LineageGraph";
import { ApiClientError, apiBaseUrl } from "./shared/api/client";
import { candidateInputIdentity } from "./shared/api/inferenceRequestCache";
import {
  workbenchApi,
  type ApiActual,
  type ApiCandidateInput,
  type ApiLineage,
  type ApiLineageIndex,
  type ApiPredictionVsActual,
  type ApiPreview,
  type ApiProject,
  type ApiQuality,
  type ApiResponseCurves,
  type ApiScreeningRun,
} from "./shared/api/workbench-api";

type Tab = WorkbenchView;
function allowedRange(input: NumericTaskInput) {
  if (!input.allowed_range) throw new Error(`数値fieldにallowed_rangeがありません: ${input.path}`);
  return input.allowed_range;
}

type ResponseCurvesPayload = ApiResponseCurves;
type CurvePoint = ApiResponseCurves["curves"][string][number];
type CurveVariable = ApiResponseCurves["variable"];
const navItems: Array<{ id: Tab; label: string }> = [
  { id: "project", label: "プロジェクト" },
  { id: "candidates", label: "候補比較" },
  { id: "settings", label: "設定" },
  { id: "quality", label: "データ品質" },
  { id: "lineage", label: "工程系譜" },
  { id: "explore", label: "範囲探索" },
];

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function rangeNumber(value: number) {
  return value.toLocaleString("ja-JP", {
    maximumFractionDigits: 4,
  });
}

const CANDIDATE_COLORS = ["#d97706", "#0f766e", "#9333a8", "#dc2626", "#0891b2", "#4f46e5", "#65a30d", "#c2410c"];

function candidateColor(candidateId: string, selectedId: string) {
  if (candidateId === selectedId) return "#1f5fc4";
  let hash = 0;
  for (const character of candidateId) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return CANDIDATE_COLORS[hash % CANDIDATE_COLORS.length];
}

function Icon({
  name,
}: {
  name:
    | "copy"
    | "trash"
    | "plus"
    | "eye"
    | "play"
    | "chevron"
    | "save"
    | "settings";
}) {
  const common = {
    width: 18,
    height: 18,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  const paths = {
    copy: (
      <>
        <rect x="8" y="8" width="12" height="12" rx="1" />
        <path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" />
      </>
    ),
    trash: (
      <>
        <path d="M4 7h16M10 11v6m4-6v6M9 7l1-3h4l1 3m-9 0 1 13h10l1-13" />
      </>
    ),
    plus: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v8m-4-4h8" />
      </>
    ),
    eye: (
      <>
        <path d="M2.5 12s3.3-5.5 9.5-5.5 9.5 5.5 9.5 5.5-3.3 5.5-9.5 5.5S2.5 12 2.5 12Z" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
    play: <path d="m9 5 10 7-10 7V5Z" />,
    chevron: <path d="m9 18 6-6-6-6" />,
    save: (
      <>
        <path d="M5 4h12l3 3v13H4V5a1 1 0 0 1 1-1Z" />
        <path d="M8 4v6h8V4M8 20v-6h8v6" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.2 2.2-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.1h-3.2v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L6.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H5v-3.2h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1 2.2-2.2.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6V4h3.2v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1 2.2 2.2-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.1V14h-.1a1.7 1.7 0 0 0-1.6 1Z" />
      </>
    ),
  };
  return <svg {...common}>{paths[name]}</svg>;
}

function App() {
  const [navigation, setNavigation] = useState<NavigationIntent>(() => readNavigationIntent());
  const navigationRef = useRef(navigation);
  const tab = navigation.view;
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const candidatesRef = useRef(candidates);
  candidatesRef.current = candidates;
  const [selectedId, setSelectedId] = useState("");
  const [apiState, setApiState] = useState<"ready" | "loading" | "offline">(
    "loading",
  );
  const [notice, setNotice] = useState("候補を読み込んでいます");
  const [brokenOriginCandidateId, setBrokenOriginCandidateId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ApiProject[]>([]);
  const [taskDefinition, setTaskDefinition] = useState<TaskDefinitionContract | null>(null);
  const [resolvedTaskDefinition, setResolvedTaskDefinition] = useState<ResolvedTaskDefinition | null>(null);
  const [activeProjectId, setActiveProjectId] = useState("default");
  const loadSequence = useRef(0);
  const loadPreviewController = useRef<AbortController | null>(null);
  const selected = candidates.find((candidate) => candidate.id === selectedId);
  const activeProject = projects.find(
    (project) => project.id === activeProjectId,
  );
  const taskId = taskDefinition?.id ?? activeProject?.task_id ?? "";
  const operations = resolvedTaskDefinition?.runtime_capability.operations;
  const prediction = useWorkbenchPrediction({ projectId: activeProjectId, taskId, candidate: selected, operations, onNotice: setNotice, setApiState });
  const { error: previewError, metrics, preview, previewsByCandidate } = prediction;
  const editor = useCandidateEditor({
    projectId: activeProjectId,
    setCandidates,
    previewAvailable: operations?.preview === true,
    getPreviewInputIdentity: prediction.getPreviewInputIdentity,
    onPreview: prediction.acceptPreview,
    onNotice: setNotice,
  });

  function navigate(intent: NavigationIntent, replace = false) {
    const next = Object.freeze(intent);
    navigationRef.current = next;
    setNavigation(next);
    window.history[replace ? "replaceState" : "pushState"]({}, "", navigationUrl(next));
  }

  function selectCandidate(candidateId: string, replace = true) {
    setSelectedId(candidateId);
    navigate({ view: "candidates", projectId: activeProjectId, candidateId }, replace);
  }

  function rememberCandidate(candidateId: string) {
    setSelectedId(candidateId);
    navigate({ ...navigationRef.current, projectId: activeProjectId, candidateId }, true);
  }

  async function loadProject(projectId: string) {
    const sequence = ++loadSequence.current;
    loadPreviewController.current?.abort();
    const previewController = new AbortController();
    loadPreviewController.current = previewController;
    setApiState("loading");
    const requestedCandidateId = navigationRef.current.projectId === projectId
      ? navigationRef.current.candidateId
      : undefined;
    const [listedCandidates, resolved] = await Promise.all([
      workbenchApi.listCandidates(projectId),
      workbenchApi.taskDefinition(projectId),
    ]);
    let apiCandidates = listedCandidates;
    let requestedCandidateMissing = false;
    if (requestedCandidateId && !listedCandidates.some((candidate) => candidate.id === requestedCandidateId)) {
      try {
        const requestedCandidate = await workbenchApi.candidate(projectId, requestedCandidateId, true);
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
    setActiveProjectId(projectId);
    setTaskDefinition(definition);
    setResolvedTaskDefinition(resolved);
    window.localStorage.setItem("material-workbench-project", projectId);
    candidatesRef.current = imported;
    setCandidates(imported);
    const nextSelectedId = imported.some((candidate) => candidate.id === requestedCandidateId)
      ? requestedCandidateId!
      : (imported[0]?.id ?? "");
    setSelectedId(nextSelectedId);
    navigate({ ...navigationRef.current, projectId, candidateId: nextSelectedId || undefined }, true);
    prediction.reset();
    setApiState("ready");
    setNotice(
      requestedCandidateMissing
        ? "参照元の候補は削除済みか、このプロジェクトから参照できません"
        : imported.length
        ? "プロジェクトを切り替えました"
        : "候補がありません。過去条件または新規入力から追加できます",
    );
    if (!imported.length) return;
    if (!resolved.runtime_capability.operations.preview) return;
    const previewEntries = await Promise.all(
      imported.filter((candidate) => !candidate.raw.archived_at).map(async (candidate) => {
        const inputIdentity = candidateInputIdentity(candidate.raw.inputs);
        try {
          const loaded = await workbenchApi.previewCandidate(projectId, candidate.id, candidate.raw.revision, inputIdentity, previewController.signal);
          if (sequence !== loadSequence.current || previewController.signal.aborted) return null;
          return [candidate.id, loaded] as const;
        } catch {
          return null;
        }
      }),
    );
    if (sequence !== loadSequence.current || previewController.signal.aborted) return;
    const loaded = Object.fromEntries(
      previewEntries.filter(
        (entry): entry is readonly [string, ApiPreview] => entry !== null,
      ),
    );
    prediction.acceptProjectPreviews(imported, candidatesRef.current, loaded, definition.id);
  }

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        const available = await workbenchApi.listProjects();
        const requested = navigationRef.current.projectId;
        const remembered = window.localStorage.getItem(
          "material-workbench-project",
        );
        const projectId = available.some((project) => project.id === requested)
          ? requested!
          : available.some((project) => project.id === remembered)
            ? remembered!
            : (available[0]?.id ?? "default");
        if (cancelled) return;
        setProjects(available);
        await loadProject(projectId);
      } catch (error) {
        if (cancelled) return;
        setApiState("offline");
        setLoadError(
          `APIから候補を読み込めませんでした（${error instanceof Error ? error.message : "不明なエラー"}）。`,
        );
        setNotice("API未接続: 予測結果は表示できません");
      }
    }
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onPopState = () => {
      const intent = readNavigationIntent();
      navigationRef.current = intent;
      setNavigation(intent);
      const targetProjectId = intent.projectId ?? activeProjectId;
      if (targetProjectId !== activeProjectId || (intent.candidateId && !candidates.some((candidate) => candidate.id === intent.candidateId))) {
        void loadProject(targetProjectId);
      } else if (intent.candidateId) {
        setSelectedId(intent.candidateId);
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [activeProjectId, candidates]);

  useEffect(() => {
    const provenance = selected?.raw.provenance as CandidateProvenance | undefined;
    if (!selected || provenance?.source_kind !== "copy") {
      setBrokenOriginCandidateId(null);
      return;
    }
    let cancelled = false;
    workbenchApi.candidate(
      provenance.source_ref.project_id,
      provenance.source_ref.candidate_id,
      true,
    ).then(() => {
      if (!cancelled) setBrokenOriginCandidateId(null);
    }).catch((cause) => {
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

  const updateCandidateInput = (id: string, path: string, value: number | string) => {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const next = {
      ...current,
      raw: {
        ...current.raw,
        inputs: setCandidateInputValue(current.raw.inputs, path, value),
      },
    } as Candidate;
    setCandidates((items) =>
      items.map((candidate) => (candidate.id === id ? next : candidate)),
    );
    void persistCandidate(next, current);
  };

  const updateCandidateText = (
    id: string,
    field: "label" | "coating",
    value: string,
  ) => {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const next = field === "label"
      ? { ...current, label: value }
      : {
          ...current,
          raw: {
            ...current.raw,
            inputs: {
              ...current.raw.inputs,
              categorical: { ...current.raw.inputs.categorical, coating: value },
            },
          },
        };
    setCandidates((items) =>
      items.map((candidate) => (candidate.id === id ? next : candidate)),
    );
    void persistCandidate(next, current);
  };

  const updateHeat = (
    index: number,
    field: "time" | "temperature",
    raw: number,
  ) => {
    if (!selected) return;
    const next = {
      ...selected,
      heat: selected.heat.map((point, pointIndex) =>
        pointIndex === index ? { ...point, [field]: raw } : point,
      ),
    };
    setCandidates((items) =>
      items.map((candidate) =>
        candidate.id === selectedId ? next : candidate,
      ),
    );
    void persistCandidate(next, selected);
  };

  const addHeatPoint = () => {
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
    setCandidates((items) =>
      items.map((candidate) =>
        candidate.id === selected.id ? next : candidate,
      ),
    );
    void persistCandidate(next, selected);
  };

  const deleteHeatPoint = (index: number) => {
    if (!selected || selected.heat.length <= 2) return;
    const next = {
      ...selected,
      heat: selected.heat.filter((_, pointIndex) => pointIndex !== index),
    };
    setCandidates((items) =>
      items.map((candidate) =>
        candidate.id === selected.id ? next : candidate,
      ),
    );
    void persistCandidate(next, selected);
  };

  async function persistCandidate(candidate: Candidate, previous: Candidate) {
    editor.schedule(candidate, previous);
  }

  const addCandidate = async () => {
    if (!selected) return;
    if (candidates.length >= 10) {
      setNotice(
        "比較候補は最大10件です。不要な候補を削除してから追加してください",
      );
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
      setCandidates((items) => [...items, created]);
      selectCandidate(created.id);
      setNotice("候補を追加しました");
    } catch {
      setApiState("offline");
      setNotice("候補を追加できませんでした。API接続を確認してください。");
    }
  };

  const createStarterCandidate = async () => {
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
  };

  const copyCandidate = async () => {
    if (!selected) return;
    if (candidates.length >= 10) {
      setNotice("比較候補は最大10件です。不要な候補を削除してから追加してください");
      return;
    }
    try {
      const request: ApiCandidateInput = {
        ...toApiCandidate(selected),
        name: `${selected.label} のコピー`,
        provenance: {
          source_kind: "copy",
          source_ref: {
            project_id: activeProjectId,
            candidate_id: selected.id,
            candidate_revision: selected.raw.revision,
          },
        },
      };
      const created = fromApiCandidate(await workbenchApi.createCandidate(activeProjectId, request));
      setCandidates((items) => [...items, created]);
      selectCandidate(created.id);
      setNotice("由来を保持して候補をコピーしました");
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "候補をコピーできませんでした。");
    }
  };

  const deleteCandidate = async () => {
    if (!selected || candidates.length === 1) return;
    try {
      await workbenchApi.deleteCandidate(activeProjectId, selectedId, selected.raw.revision);
      const remaining = candidates.filter(
        (candidate) => candidate.id !== selectedId,
      );
      setCandidates(remaining);
      selectCandidate(remaining[0].id);
      setNotice("候補を一覧から外しました");
    } catch {
      setNotice("候補を削除できませんでした。API接続を確認してください。");
    }
  };

  const runDetailedPrediction = prediction.runDetailedPrediction;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">Material Decision Workbench</div>
        <nav aria-label="画面">
          {navItems.map((item) => (
            <button
              className={tab === item.id ? "nav-button active" : "nav-button"}
              onClick={() => {
                const intent = withView(navigationRef.current, item.id);
                navigate({
                  ...intent,
                  projectId: activeProjectId,
                  candidateId:
                    item.id === "candidates" || item.id === "project"
                      ? selectedId || undefined
                      : intent.candidateId,
                });
              }}
              key={item.id}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        <div className="context-bar">
          <div>
            <span className="overline">プロジェクト</span>
            <h1>{activeProject?.name ?? "プロジェクトを読み込んでいます"}</h1>
          </div>
          <div className="run-actions">
            {tab !== "candidates" && (
              <button
                type="button"
                className="stock-button"
                onClick={() => navigate({
                  view: "candidates",
                  projectId: activeProjectId,
                  candidateId: selectedId || undefined,
                })}
              >
                候補ストック <b>{candidates.length}</b>
                <span>比較へ</span>
              </button>
            )}
            <span className="notice" role="status">{notice}</span>
            <span className={`api-state ${apiState}`}>
              {apiState === "loading"
                ? "プレビュー更新中"
                : apiState === "offline"
                  ? "API 未接続"
                  : "同期済み"}
            </span>
            {tab === "candidates" && selected && (
              <button
                className="primary-button"
                disabled={!operations?.detailed_prediction || !["idle", "saved"].includes(editor.saveStates[selected.id] ?? "idle")}
                title={!operations?.detailed_prediction ? "このタスクでは詳細予測を利用できません" : undefined}
                onClick={() => {
                  void runDetailedPrediction();
                }}
              >
                <Icon name="play" />
                {selected.label}の詳細予測を保存
              </button>
            )}
          </div>
        </div>
        {tab === "project" && (
          <ProjectHub
            projects={projects}
            activeProjectId={activeProjectId}
            candidate={selected}
            taskDefinition={taskDefinition}
            operations={operations}
            currentPreviews={prediction.previewsByCandidate}
            onProjectChanged={(project) => {
              setProjects((items) =>
                items.some((item) => item.id === project.id)
                  ? items.map((item) =>
                      item.id === project.id ? project : item,
                    )
                  : [...items, project],
              );
              if (project.id === activeProjectId && activeProject?.task_id !== project.task_id) void loadProject(project.id);
            }}
            onSwitch={(projectId) => {
              navigate({ view: "project", projectId }, true);
              void loadProject(projectId);
            }}
            onNavigate={(view, candidateId) => {
              navigate({ view, projectId: activeProjectId, candidateId }, true);
              if (candidateId) selectCandidate(candidateId, true);
            }}
            onSnapshotNavigate={(snapshotId) => navigate({ view: "project", projectId: activeProjectId, snapshotId }, true)}
            onRestore={(candidate) => {
              if (candidate.raw.project_id !== activeProjectId) {
                setNotice(
                  "別プロジェクトの保存結果は現在の候補へ混在できません",
                );
                return;
              }
              setCandidates((items) => [...items, candidate]);
              selectCandidate(candidate.id);
            }}
            requestedSnapshotId={navigation.snapshotId}
          />
        )}
        {tab === "settings" && (
          <InputRangeSettingsPage
            project={activeProject}
            taskDefinition={taskDefinition}
            onProjectChanged={(project) => {
              setProjects((items) => items.map((item) => item.id === project.id ? project : item));
              if (project.id === activeProjectId) {
                workbenchApi.taskDefinition(project.id)
                  .then((resolved) => {
                    setResolvedTaskDefinition(resolved);
                    validateResolvedTaskDefinition(resolved);
                    setTaskDefinition(resolved.task_definition);
                  })
                  .catch(() => undefined);
              }
            }}
          />
        )}
        {tab === "candidates" &&
          (selected ? (
            <CandidateWorkbench
              candidates={candidates}
              projectId={activeProjectId}
              targetValues={activeProject?.target_values ?? {}}
              decisionCandidateId={activeProject?.decision_candidate_id ?? ""}
              selected={selected}
              selectedId={selectedId}
              taskDefinition={taskDefinition}
              operations={operations}
              saveState={editor.saveStates[selected.id] ?? "idle"}
              fieldErrors={editor.fieldErrors[selected.id] ?? []}
              onReload={() => editor.reload(selected.id)}
              onCopyDraft={() => void editor.copyDraft(selected)}
              metrics={metrics}
              preview={preview}
              previewError={previewError}
              onRetryPreview={prediction.retry}
              previewsByCandidate={previewsByCandidate}
              onSelect={(candidateId) => selectCandidate(candidateId)}
              originBroken={brokenOriginCandidateId === selected.id}
              onOpenOrigin={() => {
                const provenance = selected.raw.provenance as CandidateProvenance;
                const intent = provenanceNavigation(provenance, activeProjectId);
                if (!intent) return;
                setBrokenOriginCandidateId(null);
                if (provenance.source_kind === "copy") {
                  void workbenchApi.candidate(
                    provenance.source_ref.project_id,
                    provenance.source_ref.candidate_id,
                    true,
                  ).then(async (source) => {
                    if (source.project_id !== activeProjectId) await loadProject(source.project_id);
                    const sourceCandidate = fromApiCandidate(source);
                    setCandidates((items) => items.some((item) => item.id === source.id)
                      ? items.map((item) => item.id === source.id ? sourceCandidate : item)
                      : [...items, sourceCandidate]);
                    setSelectedId(source.id);
                    navigate(intent);
                  }).catch((cause) => {
                    if (cause instanceof ApiClientError && cause.status === 404) {
                      setBrokenOriginCandidateId(selected.id);
                      setNotice("コピー元候補は削除済みか、このプロジェクトから参照できません");
                    } else {
                      setBrokenOriginCandidateId(null);
                      setNotice("作成元候補を一時的に確認できません。API接続を確認してください。");
                    }
                  });
                  return;
                }
                navigate(intent);
              }}
              onInput={updateCandidateInput}
              onText={updateCandidateText}
              onHeat={updateHeat}
              onAddHeat={addHeatPoint}
              onDeleteHeat={deleteHeatPoint}
              onCopy={copyCandidate}
              onDelete={() => {
                void deleteCandidate();
              }}
              onAdd={() => {
                void addCandidate();
              }}
              onImported={(imported) => {
                if (imported.length) void loadProject(activeProjectId);
              }}
            />
          ) : (
            <ApiEmptyState
              loading={apiState === "loading"}
              error={loadError}
              onCreate={() => void createStarterCandidate()}
            />
          ))}
        {tab === "quality" && (
          <LiveDataQualityPage
            filters={{
              issueId: navigation.qualityIssueId,
              type: navigation.qualityType,
              sheet: navigation.qualitySheet,
              key: navigation.qualityKey,
            }}
            onFiltersChange={(filters) => navigate({
              ...navigationRef.current,
              view: "quality",
              projectId: activeProjectId,
              qualityIssueId: filters.issueId,
              qualityType: filters.type,
              qualitySheet: filters.sheet,
              qualityKey: filters.key,
              entityKey: undefined,
            }, true)}
            onOpenLineage={(issue, filters) => navigate({
              ...navigationRef.current,
              view: "lineage",
              projectId: activeProjectId,
              entityKey: issue.focus_entity_key ?? undefined,
              qualityIssueId: issue.issue_id,
              qualityType: filters.type,
              qualitySheet: filters.sheet,
              qualityKey: filters.key,
            })}
          />
        )}
        {tab === "lineage" && (
          <LiveLineagePage
            projectId={activeProjectId}
            supportsCandidateCreation={taskDefinition?.input_groups.some((group) => group.key === "heat_pattern") ?? false}
            initialEntityKey={navigation.entityKey}
            qualityIssueId={navigation.qualityIssueId}
            onEntityChange={(entityKey) => navigate({ ...navigationRef.current, view: "lineage", projectId: activeProjectId, entityKey }, true)}
            onReturnToQuality={() => navigate({
              ...navigationRef.current,
              view: "quality",
              projectId: activeProjectId,
              entityKey: undefined,
            })}
            onCandidate={(candidate) => {
              setCandidates((items) => [...items, candidate]);
              rememberCandidate(candidate.id);
              setNotice(`${candidate.label} を候補ストックへ追加しました（${candidates.length + 1}件）`);
            }}
          />
        )}
        {tab === "explore" && (
          <LiveScreeningPage
            projectId={activeProjectId}
            candidates={candidates}
            selectedId={selectedId}
            taskDefinition={taskDefinition}
            resolvedTaskDefinition={resolvedTaskDefinition}
            initialRunId={navigation.screeningRunId}
            onRunChange={(screeningRunId) => navigate({ view: "explore", projectId: activeProjectId, screeningRunId }, true)}
            onCandidate={(candidate) => {
              setCandidates((items) => [...items, candidate]);
              rememberCandidate(candidate.id);
              setNotice(`${candidate.label} を候補ストックへ追加しました（${candidates.length + 1}件）`);
            }}
            onCompare={() => navigate({ view: "candidates", projectId: activeProjectId }, true)}
          />
        )}
      </main>
    </div>
  );
}

function ApiEmptyState({
  loading,
  error,
  onCreate,
}: {
  loading: boolean;
  error: string | null;
  onCreate: () => void;
}) {
  return (
    <div className="api-empty-state" role={error ? "alert" : "status"}>
      <h2>{loading ? "候補を読み込んでいます" : "候補を表示できません"}</h2>
      <p>{error ?? "データと予測モデルを準備しています。"}</p>
      {error && (
        <p className="api-hint">
          FastAPI を <code>{apiBaseUrl}</code> で起動後、再読み込みしてください。
        </p>
      )}
      {!loading && !error && (
        <button className="primary-button" onClick={onCreate}>
          <Icon name="plus" />
          最初の候補を作る
        </button>
      )}
    </div>
  );
}

type WorkbenchProps = {
  candidates: Candidate[];
  projectId: string;
  targetValues: Record<string, number>;
  decisionCandidateId: string;
  selected: Candidate;
  selectedId: string;
  taskDefinition: TaskDefinitionContract | null;
  operations?: RuntimeOperations;
  saveState: CandidateSaveState;
  fieldErrors: Array<{ path: string; message: string }>;
  onReload: () => void;
  onCopyDraft: () => void;
  metrics: Metric[];
  preview: ApiPreview | null;
  previewError: string;
  onRetryPreview: () => void;
  previewsByCandidate: Record<string, ApiPreview>;
  onSelect: (id: string) => void;
  onHeat: (index: number, field: "time" | "temperature", raw: number) => void;
  onInput: (id: string, path: string, value: number | string) => void;
  onText: (id: string, field: "label" | "coating", value: string) => void;
  onAddHeat: () => void;
  onDeleteHeat: (index: number) => void;
  onCopy: () => void;
  onOpenOrigin: () => void;
  originBroken: boolean;
  onDelete: () => void;
  onAdd: () => void;
  onImported: (items: Candidate[]) => void;
};

function CandidateWorkbench(props: WorkbenchProps) {
  const [curvesVisible, setCurvesVisible] = useState(false);
  const {
    candidates,
    projectId,
    targetValues,
    decisionCandidateId,
    selected,
    selectedId,
    taskDefinition,
    operations,
    saveState,
    fieldErrors,
    onReload,
    onCopyDraft,
    metrics,
    preview,
    previewError,
    onRetryPreview,
    previewsByCandidate,
    onSelect,
    onInput,
    onText,
    onHeat,
    onAddHeat,
    onDeleteHeat,
    onCopy,
    onOpenOrigin,
    originBroken,
    onDelete,
    onAdd,
    onImported,
  } = props;
  return (
    <div className="workbench-grid candidate-workbench-grid">
      {taskDefinition && <TaskDrivenCandidateInspector
        candidate={selected}
        taskDefinition={taskDefinition}
        saveState={saveState}
        fieldErrors={fieldErrors}
        onInput={(path, value) => onInput(selected.id, path, value)}
        onReload={onReload}
        onCopyDraft={onCopyDraft}
        heatPattern={taskDefinition.input_groups.some((group) => group.key === "heat_pattern") ? <HeatPattern candidates={candidates} candidate={selected} onUpdate={onHeat} onAdd={onAddHeat} onDelete={onDeleteHeat} /> : undefined}
      />}
      <section className="central-workspace">
        <div className="table-heading">
          <div>
            <h2>
              候補比較表 <span>（セルを直接編集）</span>
            </h2>
          </div>
          <div className="comparison-actions" aria-label="候補操作">
            <button className="outline-button" onClick={onCopy}>
              <Icon name="copy" />選択候補を複製
            </button>
            <button
              className="outline-button"
              onClick={onDelete}
              disabled={
                candidates.length <= 1 || decisionCandidateId === selectedId
              }
              title={
                decisionCandidateId === selectedId
                  ? "採用判断を解除してから削除してください"
                  : undefined
              }
            >
              <Icon name="trash" />削除
            </button>
            <CandidateFileControls projectId={projectId} onImported={onImported} />
            <button className="primary-button" onClick={onAdd}>
              <Icon name="plus" />候補を追加
            </button>
          </div>
        </div>
        <CandidateOrigin candidate={selected} broken={originBroken} onOpen={onOpenOrigin} />
        {taskDefinition && <TaskDrivenComparisonTable
          candidates={candidates}
          selectedId={selectedId}
          taskDefinition={taskDefinition}
          previewsByCandidate={previewsByCandidate}
          targetValues={targetValues}
          onSelect={onSelect}
          onInput={onInput}
          onName={(id, value) => onText(id, "label", value)}
        />}
        {operations?.response_curve ? (
          <section className="response-curves-disclosure">
            <button
              type="button"
              className="outline-button"
              aria-expanded={curvesVisible}
              onClick={() => setCurvesVisible((visible) => !visible)}
            >
              {curvesVisible ? "応答曲線を閉じる" : "選択候補の応答曲線を表示"}
            </button>
            {curvesVisible && <LiveResponseCurves
              projectId={projectId}
              candidate={selected}
              preview={preview}
              targetValues={targetValues}
              taskDefinition={taskDefinition}
              available
            />}
          </section>
        ) : <UnavailablePanel title="応答曲線" />}
        {operations?.actual_measurement ? <ActualsPanel projectId={projectId} candidate={selected} outputs={taskDefinition?.outputs ?? []} enabled={["idle", "saved"].includes(saveState)} /> : <UnavailablePanel title="予測と実測" />}
      </section>
      <EvidencePanel metrics={metrics} preview={preview} candidateLabel={selected.label} similarityAvailable={operations?.similarity === true} error={previewError} onRetry={onRetryPreview} />
    </div>
  );
}

function CandidateOrigin({
  candidate,
  broken,
  onOpen,
}: {
  candidate: Candidate;
  broken: boolean;
  onOpen: () => void;
}) {
  const provenance = candidate.raw.provenance as CandidateProvenance;
  const originNavigation = provenanceNavigation(provenance, candidate.raw.project_id);
  return (
    <div className={`candidate-origin ${broken ? "missing" : ""}`}>
      <span><b>作成元</b>{provenanceLabel(provenance)}</span>
      {broken ? (
        <em>コピー元は削除済みか参照できません</em>
      ) : candidate.raw.archived_at ? (
        <em>archive済み候補を参照中</em>
      ) : originNavigation ? (
        <button type="button" className="outline-button" onClick={onOpen}>作成元へ戻る</button>
      ) : (
        <small>この候補は比較画面で直接作成されました</small>
      )}
    </div>
  );
}

function CandidateFileControls({
  projectId,
  onImported,
}: {
  projectId: string;
  onImported: (items: Candidate[]) => void;
}) {
  const [message, setMessage] = useState("");
  const upload = async (file?: File) => {
    if (!file) return;
    try {
      const body = await workbenchApi.importCandidates(projectId, file);
      const imported = body.candidates.map(fromApiCandidate);
      onImported(imported);
      setMessage(
        `${body.created}件を取り込みました${body.errors.length ? `（${body.errors.length}件は確認が必要）` : ""}`,
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "XLSXを取り込めませんでした。",
      );
    }
  };
  const download = () => {
    window.location.assign(workbenchApi.candidateExportUrl(projectId));
  };
  return (
    <div className="file-controls">
      <label className="outline-button">
        XLSXを読込
        <input
          type="file"
          accept=".xlsx"
          onChange={(e) => {
            void upload(e.target.files?.[0]);
          }}
          hidden
        />
      </label>
      <button className="outline-button" onClick={download}>
        結果をXLSX出力
      </button>
      {message && <small>{message}</small>}
    </div>
  );
}

function UnavailablePanel({ title }: { title: string }) {
  return <section className="actuals-panel unavailable-panel" aria-label={`${title}は利用できません`}><div className="panel-title"><h2>{title}</h2></div><p className="empty-evidence">このタスクでは利用できません。</p></section>;
}

function ActualsPanel({ projectId, candidate, outputs, enabled }: { projectId: string; candidate: Candidate; outputs: TaskOutputDefinition[]; enabled: boolean }) {
  const [property, setProperty] = useState<ApiActual["property"]>((outputs[0]?.key ?? "TS") as ApiActual["property"]);
  const [mean, setMean] = useState("");
  const [std, setStd] = useState("0");
  const [replicates, setReplicates] = useState("1");
  const [experimentNo, setExperimentNo] = useState("");
  const [measuredAt, setMeasuredAt] = useState("");
  const [note, setNote] = useState("");
  const [comparison, setComparison] = useState<ApiPredictionVsActual | null>(null);
  const [error, setError] = useState("");
  const identity = `${projectId}\u001f${candidate.id}\u001f${candidate.raw.revision}`;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const refresh = async (signal?: AbortSignal, expectedIdentity = identity) => {
    try {
      const result = await workbenchApi.predictionVsActual(projectId, candidate.id, signal);
      if (!signal?.aborted && identityRef.current === expectedIdentity) setComparison(result);
    } catch {
      if (signal?.aborted || identityRef.current !== expectedIdentity) return;
      setError("実測値を取得できませんでした。");
    }
  };
  useEffect(() => {
    const controller = new AbortController();
    setComparison(null);
    setError("");
    if (!outputs.some((output) => output.key === property)) setProperty((outputs[0]?.key ?? "TS") as ApiActual["property"]);
    void refresh(controller.signal);
    return () => controller.abort();
  }, [candidate.id, candidate.raw.revision, projectId]);
  useEffect(() => {
    setMean("");
    setStd("0");
    setReplicates("1");
    setExperimentNo("");
    setMeasuredAt("");
    setNote("");
  }, [candidate.id, projectId]);
  const add = async () => {
    if (!enabled) return setError("候補の保存完了後に実測を登録してください。");
    if (mean.trim() === "") return setError("実測平均を入力してください。");
    const expectedIdentity = identity;
    try {
      setError("");
      await workbenchApi.createActual(projectId, candidate.id, candidate.raw.revision, {
        property,
        mean: Number(mean),
        std: Number(std),
        replicates: Number(replicates),
        unit: (outputs.find((output) => output.key === property)?.unit ?? "%") as "MPa" | "%",
        experiment_no: experimentNo.trim(),
        measured_at: measuredAt || null,
        note: note.trim(),
      });
      if (identityRef.current !== expectedIdentity) return;
      setMean("");
      setExperimentNo("");
      setMeasuredAt("");
      setNote("");
      await refresh(undefined, expectedIdentity);
    } catch {
      if (identityRef.current !== expectedIdentity) return;
      setError("実測値を保存できませんでした。");
    }
  };
  const remove = async (id: string) => {
    const expectedIdentity = identity;
    try {
      await workbenchApi.deleteActual(projectId, candidate.id, id);
      await refresh(undefined, expectedIdentity);
    } catch {
      if (identityRef.current !== expectedIdentity) return;
      setError("実測値を削除できませんでした。");
    }
  };
  const rows = comparison?.comparisons ?? [];
  return (
    <section className="actuals-panel">
      <div className="panel-title">
        <h2>予測と実測</h2>
        <span>
          {rows.length
            ? "登録時点の予測スナップショットと比較"
            : "実測を登録すると予測を固定保存します"}
        </span>
      </div>
      <div className="actual-form">
        <select
          aria-label="実測特性"
          disabled={!enabled}
          value={property}
          onChange={(e) => setProperty(e.target.value as ApiActual["property"])}
        >
          {outputs.map((output) => <option key={output.key} value={output.key}>{output.label}</option>)}
        </select>
        <input
          aria-label="実測平均"
          disabled={!enabled}
          type="number"
          placeholder="実測平均"
          value={mean}
          onChange={(e) => setMean(e.target.value)}
        />
        <input
          aria-label="標準偏差"
          disabled={!enabled}
          type="number"
          min="0"
          placeholder="標準偏差"
          value={std}
          onChange={(e) => setStd(e.target.value)}
        />
        <input
          aria-label="反復数"
          disabled={!enabled}
          type="number"
          min="1"
          placeholder="反復数"
          value={replicates}
          onChange={(e) => setReplicates(e.target.value)}
        />
        <button
          className="outline-button"
          disabled={!enabled}
          onClick={() => {
            void add();
          }}
        >
          実測を追加
        </button>
      </div>
      <details className="actual-meta-fields">
        <summary>実験情報を追加</summary>
        <div>
          <label>
            実験番号
            <input
              value={experimentNo}
              onChange={(e) => setExperimentNo(e.target.value)}
              placeholder="例: EXP-2026-014"
            />
          </label>
          <label>
            測定日
            <input
              type="date"
              value={measuredAt}
              onChange={(e) => setMeasuredAt(e.target.value)}
            />
          </label>
          <label>
            メモ
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="試験片・測定条件など"
            />
          </label>
        </div>
      </details>
      {error && <p className="empty-evidence">{error}</p>}
      <table className="quality-table actual-table">
        <thead>
          <tr>
            <th>特性 / 実験</th>
            <th>固定予測</th>
            <th>実測平均 ± SD</th>
            <th>差（実測−予測）</th>
            <th>予測区間</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const prediction = row.prediction.predictions[row.actual.property];
            const delta = row.actual.mean - prediction.value;
            const inside =
              row.actual.mean >= prediction.lower &&
              row.actual.mean <= prediction.upper;
            return (
              <tr key={row.actual.id}>
                <td>
                  <b>{row.actual.property}</b>
                  {(row.actual.experiment_no ||
                    row.actual.measured_at ||
                    row.actual.note) && (
                    <small className="actual-meta">
                      {[
                        row.actual.experiment_no,
                        row.actual.measured_at,
                        row.actual.note,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </small>
                  )}
                </td>
                <td>
                  {number(prediction.value, 1)} {prediction.unit}
                </td>
                <td>
                  {number(row.actual.mean, 1)} ± {number(row.actual.std, 1)}{" "}
                  {row.actual.unit}
                  <small> n={row.actual.replicates}</small>
                </td>
                <td className={Math.abs(delta) > 0 ? "metric-value" : ""}>
                  {delta >= 0 ? "+" : ""}
                  {number(delta, 1)}
                </td>
                <td>
                  <span
                    className={`status-dot ${inside ? "success" : "caution"}`}
                  />
                  {inside ? "区間内" : "区間外"}
                  <small>
                    {" "}
                    [{number(prediction.lower, 1)}–{number(prediction.upper, 1)}
                    ]
                  </small>
                </td>
                <td>
                  <button
                    className="icon-delete"
                    aria-label={`${row.actual.property}実測を削除`}
                    onClick={() => {
                      void remove(row.actual.id);
                    }}
                  >
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
          {!rows.length && (
            <tr>
              <td colSpan={6} className="empty-evidence">
                実測はまだありません。
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function HeatPattern({
  candidates,
  candidate,
  onUpdate,
  onAdd,
  onDelete,
}: {
  candidates: Candidate[];
  candidate: Candidate;
  onUpdate: (index: number, field: "time" | "temperature", raw: number) => void;
  onAdd: () => void;
  onDelete: (index: number) => void;
}) {
  const width = 440;
  const height = 210;
  const pad = { x: 42, y: 18 };
  const times = candidates.flatMap((item) => item.heat.map((point) => point.time));
  const rawMinTime = Math.min(...times);
  const rawMaxTime = Math.max(...times);
  const timePadding = Math.max((rawMaxTime - rawMinTime) * 0.08, 0.05);
  const minTime = Math.max(0, rawMinTime - timePadding);
  const maxTime = rawMaxTime + timePadding;
  const maxTemp = Math.max(
    1000,
    ...candidates.flatMap((item) =>
      item.heat.map((point) => point.temperature),
    ),
  );
  const x = (time: number) =>
    pad.x + ((time - minTime) / Math.max(0.001, maxTime - minTime)) * (width - pad.x - 18);
  const y = (temp: number) =>
    height - 31 - (temp / maxTemp) * (height - pad.y - 31);
  const points = candidate.heat
    .map((point) => `${x(point.time)},${y(point.temperature)}`)
    .join(" ");
  const timeTicks = [minTime, (minTime + maxTime) / 2, maxTime];
  const dragPoint = (event: PointerEvent<SVGCircleElement>, index: number) => {
    const svg = event.currentTarget.ownerSVGElement;
    if (!svg) return;
    const bounds = svg.getBoundingClientRect();
    const temperature = Math.round(
      Math.max(
        0,
        Math.min(
          maxTemp,
          ((height -
            31 -
            ((event.clientY - bounds.top) / bounds.height) * height) /
            (height - pad.y - 31)) *
            maxTemp,
        ),
      ),
    );
    onUpdate(index, "temperature", temperature);
  };
  return (
    <section className="chart-panel heat-panel">
      <div className="panel-title">
        <h2>
          ヒートパターン <span>（焼鈍温度・時間）</span>
        </h2>
        <div className="candidate-color-legend" aria-label="候補の色">
          {candidates.map((item) => <span className={item.id === candidate.id ? "selected" : ""} key={item.id}><i style={{ background: candidateColor(item.id, candidate.id) }} />{item.label}</span>)}
        </div>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="heat-chart"
        role="img"
        aria-label="候補を重ねたヒートパターン。選択候補の温度点をドラッグして編集できます。"
      >
        <g className="grid-lines">
          {[0, 200, 400, 600, 800, 1000].map((value) => (
            <g key={value}>
              <line x1={pad.x} x2={width - 18} y1={y(value)} y2={y(value)} />
              <text x="3" y={y(value) + 4}>
                {value}
              </text>
            </g>
          ))}
          {timeTicks.map((value) => (
            <g key={`time-${value}`}>
              <line x1={x(value)} x2={x(value)} y1={pad.y} y2={height - 31} />
              <text x={x(value)} y={height - 18} textAnchor="middle">
                {number(value, 2)}
              </text>
            </g>
          ))}
        </g>
        {candidates
          .filter((item) => item.id !== candidate.id)
          .map((item) => (
            <polyline
              key={item.id}
              points={item.heat
                .map((point) => `${x(point.time)},${y(point.temperature)}`)
                .join(" ")}
              fill="none"
              stroke={candidateColor(item.id, candidate.id)}
              strokeWidth="1.5"
              opacity=".62"
            />
          ))}
        <polyline
          points={points}
          fill="none"
          stroke={candidateColor(candidate.id, candidate.id)}
          strokeWidth="3"
        />
        {candidate.heat.map((point, index) => (
          <circle
            tabIndex={0}
            aria-label={`${number(point.time, 2)}分, ${point.temperature}度`}
            key={`${point.time}-${index}`}
            cx={x(point.time)}
            cy={y(point.temperature)}
            r="5"
            fill="#1F5FC4"
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              dragPoint(event, index);
            }}
            onPointerMove={(event) =>
              event.currentTarget.hasPointerCapture(event.pointerId) &&
              dragPoint(event, index)
            }
          />
        ))}
        <text className="axis-title" x="3" y="13">
          温度 (°C)
        </text>
        <text
          className="axis-title"
          x={(pad.x + width - 18) / 2}
          y={height - 1}
          textAnchor="middle"
        >
          時間 (min)
        </text>
      </svg>
      <div className="heat-edit">
        <div>
          <b>ヒートパターン編集</b>
          <span>点をドラッグ、または数値を編集</span>
          <button className="text-button" onClick={onAdd}>
            点を追加
          </button>
        </div>
        <div className="heat-point-table-wrap">
          <table className="heat-point-table">
            <thead>
              <tr><th>#</th><th>時間 <small>min</small></th><th>温度 <small>°C</small></th><th aria-label="操作" /></tr>
            </thead>
            <tbody>
              {candidate.heat.map((point, index) => (
                <tr key={`${point.time}-${index}`}>
                  <th scope="row">{index + 1}</th>
                  <td><input type="number" step="0.01" value={Number(point.time.toFixed(3))} aria-label={`点${index + 1}の時間（分）`} onChange={(event) => onUpdate(index, "time", Number(event.target.value))} /></td>
                  <td><input type="number" value={point.temperature} aria-label={`点${index + 1}の温度（℃）`} onChange={(event) => onUpdate(index, "temperature", Number(event.target.value))} /></td>
                  <td><button className="icon-delete" aria-label={`点${index + 1}を削除`} disabled={candidate.heat.length <= 2} onClick={() => onDelete(index)}>×</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <small>RT = 室温（25°C）</small>
      </div>
    </section>
  );
}

function LiveResponseCurves({
  projectId,
  candidate,
  preview,
  targetValues,
  taskDefinition,
  available,
}: {
  projectId: string;
  candidate: Candidate;
  preview: ApiPreview | null;
  targetValues: Record<string, number>;
  taskDefinition: TaskDefinitionContract | null;
  available: boolean;
}) {
  const outputs = taskDefinition?.outputs ?? [];
  const variables: CurveVariable[] = [
    ...numericTaskInputs(taskDefinition)
      .filter((input) => input.editable && input.field !== "coating")
      .map((input) => {
        const range = allowedRange(input);
        return {
          id: input.group === "composition" ? `${input.group}.${input.field}` : input.field,
          label: input.label,
          unit: input.unit,
          min: range.min,
          max: range.max,
          current: input.group === "composition" ? candidate.raw.inputs.composition[input.field] ?? 0 : candidate.raw.inputs.process[input.field] ?? 0,
        };
      }),
    ...candidate.heat.flatMap((point, index) => [
      { id: `heat.${index}.temperature_c`, label: `ヒート ${index + 1}点目 温度`, unit: "°C", min: 0, max: 1000, current: point.temperature },
      { id: `heat.${index}.time_min`, label: `ヒート ${index + 1}点目 時間`, unit: "min", min: 0, max: Math.max(1, candidate.heat.at(-1)?.time ?? 1), current: point.time },
    ]),
  ];
  const [variableId, setVariableId] = useState(variables[0]?.id ?? "heat.peak_temperature_c");
  const [loadedPayload, setLoadedPayload] = useState<{ identity: string; payload: ResponseCurvesPayload } | null>(null);
  const [errorIdentity, setErrorIdentity] = useState<string | null>(null);
  const requestIdentity = useRef("");
  const inputIdentity = candidateInputIdentity(candidate.raw.inputs);
  const currentRequestIdentity = taskDefinition
    ? `${workbenchRequestKey({ projectId, taskId: taskDefinition.id, candidateId: candidate.id, candidateRevision: candidate.raw.revision }, `response_curve:${variableId}`)}\u001f${inputIdentity}`
    : "";
  useEffect(() => {
    if (variables.length && !variables.some((variable) => variable.id === variableId)) setVariableId(variables[0].id);
  }, [candidate.id, variableId, variables.length]);
  useEffect(() => {
    const controller = new AbortController();
    if (!available || !preview || !taskDefinition || !inputIdentity) return;
    const identity = currentRequestIdentity;
    requestIdentity.current = identity;
    setLoadedPayload(null);
    setErrorIdentity(null);
    const timer = window.setTimeout(async () => {
      try {
        const loaded = await workbenchApi.responseCurves(projectId, candidate.id, candidate.raw.revision, inputIdentity, variableId, controller.signal);
        if (controller.signal.aborted || requestIdentity.current !== identity) return;
        setLoadedPayload({ identity, payload: loaded });
      } catch (cause) {
        if (controller.signal.aborted || requestIdentity.current !== identity) return;
        setErrorIdentity(identity);
      }
    }, 320);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [available, candidate.id, candidate.raw.revision, currentRequestIdentity, inputIdentity, preview, projectId, taskDefinition?.id, variableId]);
  const activePayload = loadedPayload?.identity === currentRequestIdentity ? loadedPayload.payload : null;
  const error = errorIdentity === currentRequestIdentity;
  if (!available) return <UnavailablePanel title="応答曲線" />;
  if (!preview) return <section className="response-curves-panel"><div className="panel-title"><h2>応答曲線</h2></div><p className="empty-evidence">候補の保存とプレビュー完了後に表示します。</p></section>;
  return (
    <section className="response-curves-panel" aria-label="設計変数ごとの応答曲線" data-candidate-id={candidate.id}>
      <div className="panel-title">
        <div className="response-curves-title-group">
          <h2>応答曲線 <span>（選択した設計変数を動かしたときの特性）</span></h2>
          <span className="curve-scope">{candidate.label}のみ</span>
        </div>
        <label>変数 <select aria-label="応答曲線の設計変数" value={variableId} onChange={(event) => setVariableId(event.target.value)}>{variables.map((variable) => <option key={variable.id} value={variable.id}>{variable.label} ({variable.unit})</option>)}</select></label>
      </div>
      {error ? <p className="empty-evidence">応答曲線を取得できません。</p> : (
        <div className="response-curves-grid">
          {outputs.map((output) => {
            const points = activePayload?.curves[output.key] ?? [];
            const series = points.length && activePayload
              ? [{ candidate, points, prediction: preview?.predictions?.[output.key], currentX: activePayload.variable.current }]
              : [];
            return <ResponseCurveMiniChart key={output.key} output={output} series={series} selectedId={candidate.id} prediction={preview?.predictions?.[output.key]} goalValue={targetValues[output.key]} xRange={activePayload ? { min: activePayload.variable.min, max: activePayload.variable.max } : undefined} yRange={activePayload?.output_ranges[output.key]} xLabel={activePayload?.variable.label ?? "設計変数"} xUnit={activePayload?.variable.unit ?? ""} />;
          })}
        </div>
      )}
    </section>
  );
}

function ResponseCurveMiniChart({
  output,
  series,
  selectedId,
  prediction,
  goalValue,
  xRange,
  yRange,
  xLabel,
  xUnit,
}: {
  output: TaskOutputDefinition;
  series: Array<{ candidate: Candidate; points: CurvePoint[]; prediction?: NonNullable<ApiPreview["predictions"]>[string]; currentX: number }>;
  selectedId: string;
  prediction?: NonNullable<ApiPreview["predictions"]>[string];
  goalValue?: number;
  xRange?: { min: number; max: number };
  yRange?: { min: number; max: number };
  xLabel: string;
  xUnit: string;
}) {
  const width = 300;
  const height = 156;
  const points = series.flatMap((item) => item.points);
  const minX = xRange?.min ?? Math.min(...points.map((point) => point.x), 0);
  const maxX = xRange?.max ?? Math.max(...points.map((point) => point.x), 1);
  const outputAxisValues = yRange ? [yRange.min, yRange.max] : points.flatMap((point) => [point.lower, point.upper]);
  const rawMin = Math.min(...outputAxisValues, goalValue ?? Infinity);
  const rawMax = Math.max(...outputAxisValues, goalValue ?? -Infinity);
  const valuePadding = Math.max(1, (rawMax - rawMin) * 0.08);
  const minValue = rawMin - valuePadding;
  const maxValue = rawMax + valuePadding;
  const x = (value: number) => 30 + ((value - minX) / Math.max(1e-6, maxX - minX)) * 252;
  const y = (value: number) => 124 - ((value - minValue) / Math.max(1, maxValue - minValue)) * 92;
  const xTicks = [minX, (minX + maxX) / 2, maxX];
  return (
    <article className="response-curve-card">
      <header><b>{output.label}</b><span>{prediction ? `${number(prediction.value, output.key === "EL" || output.key === "lambda" ? 1 : 0)} ${prediction.unit}` : "読み込み中"}</span></header>
      {series.length ? <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${output.label}の応答曲線`}>
        {[minValue, (minValue + maxValue) / 2, maxValue].map((tick) => <g key={tick}><line x1="28" y1={y(tick)} x2="284" y2={y(tick)} stroke="#e3e9f0" /><text x="25" y={y(tick) + 3} textAnchor="end" fontSize="9" fill="#617087">{number(tick, output.key === "EL" || output.key === "lambda" ? 1 : 0)}</text></g>)}
        {series.map((item) => {
          const color = candidateColor(item.candidate.id, selectedId);
          const line = item.points.map((point, index) => `${index ? "L" : "M"}${x(point.x)} ${y(point.value)}`).join(" ");
          const band = `${item.points.map((point, index) => `${index ? "L" : "M"}${x(point.x)} ${y(point.upper)}`).join(" ")} ${[...item.points].reverse().map((point) => `L${x(point.x)} ${y(point.lower)}`).join(" ")} Z`;
          return <g key={item.candidate.id}><path d={band} fill={color} opacity={item.candidate.id === selectedId ? ".12" : ".05"} /><path d={line} fill="none" stroke={color} strokeWidth={item.candidate.id === selectedId ? "2.5" : "1.5"} opacity={item.candidate.id === selectedId ? "1" : ".78"} />{item.prediction && Number.isFinite(item.currentX) && <circle cx={x(item.currentX)} cy={y(item.prediction.value)} r={item.candidate.id === selectedId ? "4" : "2.5"} fill="#fff" stroke={color} strokeWidth={item.candidate.id === selectedId ? "2.5" : "1.5"} />}</g>;
        })}
        {Number.isFinite(goalValue) && <line x1="28" y1={y(goalValue!)} x2="284" y2={y(goalValue!)} stroke="#c17816" strokeDasharray="4 3" />}
        {xTicks.map((tick) => <text key={tick} x={x(tick)} y="137" textAnchor="middle" fontSize="8" fill="#617087">{number(tick, xUnit === "min" ? 2 : 1)}</text>)}
        <text x="156" y="153" textAnchor="middle" fontSize="8" fill="#617087">{xLabel} ({xUnit})</text>
      </svg> : <p className="empty-evidence">読み込み中…</p>}
    </article>
  );
}

function EvidencePanel({
  metrics,
  preview,
  candidateLabel,
  similarityAvailable,
  error,
  onRetry,
}: {
  metrics: Metric[];
  preview: ApiPreview | null;
  candidateLabel: string;
  similarityAvailable: boolean;
  error: string;
  onRetry: () => void;
}) {
  const similar = preview?.similar ?? [];
  const nearest = similar.slice(0, 3);
  const status = preview?.support?.status;
  const training = preview?.model_meta?.training_data?.records;
  const warnings = (preview?.warnings ?? []).filter(
    (warning) => warning !== preview?.support?.message,
  );
  return (
    <aside className="evidence-panel">
      <section>
        <div className="evidence-title">
          <h2>予測特性 <span>— {candidateLabel}</span></h2>
        </div>
        {metrics.length ? (
          <table className="metric-table">
            <thead>
              <tr>
                <th>特性</th>
                <th>予測値</th>
                <th>90%予測区間</th>
                <th>目標達成</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((metric) => (
                <tr key={metric.key}>
                  <th>
                    {metric.key} <small>({metric.unit})</small>
                  </th>
                  <td>
                    {number(
                      metric.value,
                      metric.key === "EL" || metric.key === "λ" ? 1 : 0,
                    )}
                  </td>
                  <td>
                    {number(metric.low, 1)}{" "}
                      <span className="whisker">
                       <i style={{ left: `${Math.max(0, Math.min(100, ((metric.value - metric.low) / Math.max(1e-9, metric.high - metric.low)) * 100))}%` }} />
                    </span>{" "}
                    {number(metric.high, 1)}
                    {(metric.modelStd !== null || metric.observationStd !== null) && (
                      <small className="uncertainty-detail">
                        モデル ±{number(metric.modelStd ?? 0, 1)} / 測定 ±{number(metric.observationStd ?? 0, 1)}
                      </small>
                    )}
                  </td>
                  <td>
                    {metric.goalProbability === null ||
                    metric.goalProbability === undefined ? (
                      "—"
                    ) : (
                      <>
                        <b>{number(metric.goalProbability * 100, 0)}%</b>
                        <small> {preview?.predictions?.[metric.key === "λ" ? "lambda" : metric.key]?.goal_direction === "at_most" ? "≤" : "≥"} {number(metric.goalValue ?? 0, 1)}</small>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : error ? (
          <p className="empty-evidence panel-error">{error} <button className="text-button" onClick={onRetry}>再試行</button></p>
        ) : (
          <p className="empty-evidence">プレビュー結果を待っています。</p>
        )}
        <p className="interval-note">
          {preview?.model_meta?.prediction_interval?.method === "gaussian_process_predictive_distribution"
            ? "予測区間はモデルの不確かさと過去測定のばらつきを含みます。入力条件の支持度は別に判定しています。"
            : "区間と目標達成率は、親工程単位の交差検証残差から求めた経験的な範囲です。"}
        </p>
        {preview?.support && (
          <div className={`support-summary ${status ?? "caution"}`}>
            <b>入力条件の支持度：{status === "supported" ? "範囲内" : status === "extrapolated" ? "外挿" : "要確認"}</b>
            <span>条件全体に対する判定です。目的変数ごとの学習範囲判定ではありません。</span>
          </div>
        )}
      </section>
      {warnings.map((warning) => (
        <div className="warning" key={warning}>
          <span>⚠</span>
          <p>{warning}</p>
        </div>
      ))}
      {preview?.support?.message && (
        <div className={status === "supported" ? "support-note" : "warning"}>
          <span>{status === "supported" ? "✓" : "⚠"}</span>
          <p>{preview.support.message}</p>
        </div>
      )}
      <section>
        <div className="evidence-title">
          <h2>近い過去実験</h2>
          <span>成分・工程・熱履歴を分けて確認</span>
        </div>
        {!similarityAvailable ? (
          <p className="empty-evidence">このタスクでは類似実験を利用できません。</p>
        ) : nearest.length ? (
          <>
            <table className="similar-table similar-summary-table">
              <thead>
                <tr>
                  <th>実験ID</th>
                  <th>層</th>
                  <th>総合</th>
                  <th>代表実測値</th>
                </tr>
              </thead>
              <tbody>
                {nearest.map((item) => (
                  <tr
                    key={`${item.layer ?? "training"}-${item.observation_id}`}
                  >
                    <td>{item.observation_id}</td>
                    <td>
                      <span
                        className={`layer-chip ${item.layer ?? "training"}`}
                      >
                        {item.layer === "historical" ? "学習外" : "学習内"}
                      </span>
                    </td>
                    <td>{item.distance.toFixed(2)}</td>
                    <td>
                      {Object.entries(item.repeat_summary ?? {})
                        .map(
                          ([key, value]) =>
                            `${key === "lambda" ? "λ" : key} ${number(value.mean, 1)} ± ${number(value.std, 1)} (n=${value.n})`,
                        )
                        .join(" / ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {similar.length > 3 && (
              <details className="similar-more">
                <summary>ほかの近傍を表示</summary>
                {similar.slice(3).map((item) => (
                  <p key={`${item.layer ?? "training"}-${item.observation_id}`}>
                    {item.parent_key} ·{" "}
                    {item.layer === "historical" ? "学習外" : "学習内"} · 距離{" "}
                    {item.distance.toFixed(2)} · {Object.entries(item.repeat_summary ?? {}).map(([key, value]) => `${key} ${number(value.mean, 1)} ± ${number(value.std, 1)} (n=${value.n})`).join(" / ")}
                  </p>
                ))}
              </details>
            )}
          </>
        ) : (
          <p className="empty-evidence">類似実験を取得しています。</p>
        )}
      </section>
      <details className="evidence-card">
        <summary>予測の根拠（再現性の詳細）</summary>
        <h2>予測の根拠</h2>
        <dl>
          <dt>計算方法</dt>
          <dd>
            {preview?.model_meta?.model?.method ?? "—"} ·{" "}
            {preview?.model_meta?.model?.id ?? "—"} v
            {preview?.model_meta?.model?.version ?? "—"}
          </dd>
          <dt>Package</dt>
          <dd>
            {preview?.model_meta?.package
              ? `${preview.model_meta.package.id} v${preview.model_meta.package.version} / ${preview.model_meta.package.manifest_sha256?.slice(0, 12)}`
              : "—"}
          </dd>
          <dt>特徴量</dt>
          <dd>
            {preview?.model_meta?.feature_pipeline?.id ?? "—"} v
            {preview?.model_meta?.feature_pipeline?.version ?? "—"}
          </dd>
          <dt>学習観測</dt>
          <dd>
            {training
              ? Object.entries(training)
                  .map(([key, value]) => `${key}: ${value}`)
                  .join(" / ")
              : "—"}
          </dd>
          <dt>検証方法</dt>
          <dd>{preview?.model_meta?.prediction_interval?.method ?? "—"}</dd>
          <dt>学習データ</dt>
          <dd>
            {preview?.model_meta?.training_data?.source_sha256?.slice(0, 12) ??
              "—"}
          </dd>
          <dt>支持度</dt>
          <dd>
            {status ?? "—"}
            {preview?.support?.percentile !== undefined
              ? `（距離百分位 ${preview.support.percentile.toFixed(0)}%）`
              : ""}
          </dd>
        </dl>
      </details>
    </aside>
  );
}

type QualityFilters = Readonly<{
  issueId?: string;
  type?: string;
  sheet?: string;
  key?: string;
}>;

function LiveDataQualityPage({
  filters,
  onFiltersChange,
  onOpenLineage,
}: {
  filters: QualityFilters;
  onFiltersChange: (filters: QualityFilters) => void;
  onOpenLineage: (issue: ApiQuality["detected_issues"][number], filters: QualityFilters) => void;
}) {
  type DetectedIssue = ApiQuality["detected_issues"][number];
  const [data, setData] = useState<ApiQuality | null>(null);
  const [error, setError] = useState(false);
  const [exportError, setExportError] = useState("");
  const [copiedKey, setCopiedKey] = useState("");
  const [copyError, setCopyError] = useState("");
  useEffect(() => {
    workbenchApi.quality()
      .then(setData)
      .catch(() => setError(true));
  }, []);
  const labels: Record<DetectedIssue["issue_type"], string> = {
    missing_key: "キー欠損",
    orphan_entity: "孤立",
    duplicate_key: "重複",
    invalid_reference: "不正参照",
  };
  const updateFilters = (patch: Partial<QualityFilters>) => onFiltersChange({ ...filters, ...patch, issueId: undefined });
  const sheets = Array.from(new Set(data?.detected_issues.map((issue) => issue.source_sheet) ?? [])).sort();
  const normalizedKey = filters.key?.trim().toLocaleLowerCase("ja-JP") ?? "";
  const visibleIssues = data?.detected_issues.filter((issue) =>
    (!filters.type || issue.issue_type === filters.type)
    && (!filters.sheet || issue.source_sheet === filters.sheet)
    && (!normalizedKey || `${issue.entity_key} ${issue.missing_reference_key ?? ""}`.toLocaleLowerCase("ja-JP").includes(normalizedKey))
  ) ?? [];
  const exportCsv = async () => {
    setExportError("");
    try {
      const csv = await workbenchApi.qualityCsv();
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "detected-data-quality.csv";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch {
      setExportError("CSVを出力できませんでした。");
    }
  };
  const copyKey = async (key: string) => {
    setCopyError("");
    try {
      await navigator.clipboard.writeText(key);
      setCopiedKey(key);
    } catch {
      setCopyError("キーをコピーできませんでした。ブラウザのクリップボード権限を確認してください。");
    }
  };
  return (
    <div className="page-panel quality-page">
      <div className="page-intro">
        <div>
          <h2>データ品質</h2>
          <p>
            元Excelを変更せず、関係と各工程シートを照合して実際の問題を検出します。
          </p>
        </div>
        <button
          className="outline-button"
          onClick={() => void exportCsv()}
        >
          検出結果をCSV出力
        </button>
      </div>
      {exportError && <p className="empty-evidence" role="alert">{exportError}</p>}
      {copyError && <p className="empty-evidence" role="alert">{copyError}</p>}
      {error ? (
        <p className="empty-evidence">
          データ品質を取得できません。API接続を確認してください。
        </p>
      ) : data ? (
        <>
          <div className="quality-summary">
            <button type="button" className={!filters.type ? "active" : ""} onClick={() => updateFilters({ type: undefined })}>
              <b>{data.detected_total}</b>件を実検出
            </button>
            {Object.entries(data.detected_by_type).map(([type, count]) => (
              <button type="button" className={filters.type === type ? "active" : ""} key={type} onClick={() => updateFilters({ type })}>
                <b>{count}</b>
                {labels[type as DetectedIssue["issue_type"]] ?? type}
              </button>
            ))}
          </div>
          <div className="quality-filters" aria-label="検出結果フィルタ">
            <label>
              種別
              <select value={filters.type ?? ""} onChange={(event) => updateFilters({ type: event.target.value || undefined })}>
                <option value="">すべて</option>
                {Object.entries(labels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
            </label>
            <label>
              元シート
              <select value={filters.sheet ?? ""} onChange={(event) => updateFilters({ sheet: event.target.value || undefined })}>
                <option value="">すべて</option>
                {sheets.map((sheet) => <option value={sheet} key={sheet}>{sheet}</option>)}
              </select>
            </label>
            <label>
              キー
              <input value={filters.key ?? ""} onChange={(event) => updateFilters({ key: event.target.value || undefined })} placeholder="キーを絞り込み" />
            </label>
            <span>{visibleIssues.length}件</span>
          </div>
          <div className="table-scroll">
            <table className="quality-table">
              <thead>
                <tr>
                  <th>検出種別</th>
                  <th>対象キー</th>
                  <th>元シート</th>
                  <th>検出内容</th>
                  <th>調査</th>
                </tr>
              </thead>
              <tbody>
                {visibleIssues.map((issue) => (
                  <tr key={issue.issue_id} className={filters.issueId === issue.issue_id ? "quality-focus-row" : ""}>
                    <td>
                      <span
                        className={`status-tag ${issue.issue_type === "invalid_reference" || issue.issue_type === "duplicate_key" ? "warn" : ""}`}
                      >
                        {labels[issue.issue_type]}
                      </span>
                    </td>
                    <td>{issue.entity_key || "（空）"}</td>
                    <td>{issue.source_sheet}</td>
                    <td>{issue.detail}</td>
                    <td className="quality-actions">
                      {issue.focus_entity_key ? (
                        <button type="button" className="text-button" onClick={() => onOpenLineage(issue, filters)}>系譜で確認</button>
                      ) : (
                        <span className="quality-unavailable">系譜を開けません。{issue.source_sheet}の該当行を確認</span>
                      )}
                      {issue.entity_key && (
                        <button type="button" className="text-button" onClick={() => void copyKey(issue.entity_key)}>
                          {copiedKey === issue.entity_key ? "コピー済み" : "キーをコピー"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {!visibleIssues.length && <tr><td colSpan={5}>条件に一致する検出結果はありません。</td></tr>}
              </tbody>
            </table>
          </div>
          {import.meta.env.DEV && <details className="reference-scenarios">
            <summary>
              Excelに用意された確認用シナリオ（{data.reference_scenarios.length}
              件）
            </summary>
            <p>
              ここは検出結果ではなく、アプリの気づきを検証するために元データへ用意された参照ケースです。
            </p>
            <table className="quality-table">
              <tbody>
                {data.reference_scenarios.map((scenario) => (
                  <tr key={scenario.scenario_id}>
                    <td>{scenario.分類}</td>
                    <td>{scenario.対象キー}</td>
                    <td>{scenario.期待する気づき}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>}
        </>
      ) : (
        <p className="empty-evidence">データ品質を読み込んでいます。</p>
      )}
    </div>
  );
}

function LiveLineagePage({
  projectId,
  supportsCandidateCreation,
  initialEntityKey,
  qualityIssueId,
  onEntityChange,
  onReturnToQuality,
  onCandidate,
}: {
  projectId: string;
  supportsCandidateCreation: boolean;
  initialEntityKey?: string;
  qualityIssueId?: string;
  onEntityChange: (entityKey: string) => void;
  onReturnToQuality: () => void;
  onCandidate: (candidate: Candidate) => void;
}) {
  const [entityKey, setEntityKey] = useState(initialEntityKey ?? "");
  const [query, setQuery] = useState("");
  const [directKey, setDirectKey] = useState("");
  const [entityType, setEntityType] = useState("焼鈍");
  const [issueOnly, setIssueOnly] = useState(false);
  const [graphLimit, setGraphLimit] = useState(40);
  const [index, setIndex] = useState<ApiLineageIndex | null>(null);
  const [data, setData] = useState<ApiLineage | null>(null);
  const [error, setError] = useState("");
  const [candidateError, setCandidateError] = useState("");
  const activeProjectRef = useRef(projectId);
  activeProjectRef.current = projectId;
  useEffect(() => {
    setEntityKey(initialEntityKey ?? "");
    setGraphLimit(40);
  }, [projectId, initialEntityKey]);
  useEffect(() => {
    setQuery("");
    setDirectKey("");
    setError("");
    setCandidateError("");
  }, [projectId]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      workbenchApi.lineageIndex(query.trim(), entityType, issueOnly, controller.signal)
        .then(setIndex)
        .catch(() => undefined);
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, entityType, issueOnly]);
  useEffect(() => {
    if (!entityKey) {
      setData(null);
      setError("");
      return;
    }
    let cancelled = false;
    setError("");
    setCandidateError("");
    workbenchApi.lineage(entityKey, graphLimit)
      .then((lineage) => {
        if (!cancelled) {
          setData(lineage);
        }
      })
      .catch((cause) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "系譜を取得できませんでした。",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [entityKey, graphLimit]);
  const createCandidate = async () => {
    const requestProjectId = projectId;
    const requestEntityKey = entityKey;
    try {
      const created = fromApiCandidate(await workbenchApi.createCandidateFromLineage(requestEntityKey, requestProjectId));
      if (activeProjectRef.current !== requestProjectId) return;
      onCandidate(created);
    } catch (cause) {
      if (activeProjectRef.current !== requestProjectId) return;
      setCandidateError(
        cause instanceof Error ? cause.message : "候補を作成できませんでした。",
      );
    }
  };
  const issueLabels: Record<string, string> = {
    missing_key: "キー欠損",
    orphan_entity: "孤立",
    duplicate_key: "重複",
    invalid_reference: "参照切れ",
  };
  const openNode = (key: string) => {
    setGraphLimit(40);
    setEntityKey(key);
    onEntityChange(key);
  };
  const heat = data?.node.heat_pattern ?? [];
  const maxTime = Math.max(1, ...heat.map((point) => point.time_s));
  const maxTemp = Math.max(
    1,
    ...heat.flatMap((point) => [point.temperature_c, point.set_temperature_c ?? point.temperature_c]),
  );
  const heatPoints = heat
    .map(
      (point) =>
        `${20 + (point.time_s / maxTime) * 380},${120 - (point.temperature_c / maxTemp) * 100}`,
    )
    .join(" ");
  const setHeatPoints = heat
    .filter((point) => typeof point.set_temperature_c === "number")
    .map(
      (point) =>
        `${20 + (point.time_s / maxTime) * 380},${120 - ((point.set_temperature_c ?? 0) / maxTemp) * 100}`,
    )
    .join(" ");
  const heatStages = Array.from(
    new Map(
      heat
        .filter((point) => point.stage_category || point.stage_name)
        .map((point) => [
          `${point.stage_category ?? "工程"}-${point.stage_name ?? ""}`,
          { category: point.stage_category ?? "工程", name: point.stage_name ?? "", status: point.mapping_status ?? "" },
        ]),
    ).values(),
  );
  return (
    <div className="page-panel lineage-page">
      {qualityIssueId && (
        <div className="investigation-context" role="status">
          <span>データ品質の検出結果から調査中</span>
          <button type="button" className="text-button" onClick={onReturnToQuality}>品質一覧へ戻る</button>
        </div>
      )}
      <div className="page-intro lineage-intro">
        <div>
          <span className="overline">DATA UNDERSTANDING</span>
          <h2>工程系譜</h2>
          <p>
            この材料・条件は、どの工程と試験結果につながっているか。
          </p>
        </div>
        <form
          className="lineage-direct-open"
          onSubmit={(event) => {
            event.preventDefault();
            if (directKey.trim()) {
              openNode(directKey.trim());
            }
          }}
        >
          <label htmlFor="lineage-direct-key">キーを直接指定</label>
          <input
            id="lineage-direct-key"
            value={directKey}
            onChange={(event) => setDirectKey(event.target.value)}
            placeholder="例: AN-00001"
          />
          <button type="submit" className="secondary-button">開く</button>
        </form>
      </div>
      <div className="lineage-workspace">
        <aside className="lineage-browser" aria-label="系譜ノード検索">
          {index && (
            <div className="lineage-source-facts">
              <span><b>{number(index.total_entities)}</b> エンティティ</span>
              <span><b>{number(index.relation_rows)}</b> relation行</span>
              <span className={index.detected_issues ? "has-issue" : ""}><b>{index.detected_issues}</b> 検出問題</span>
            </div>
          )}
          <label htmlFor="lineage-query">ノードを検索</label>
          <input
            id="lineage-query"
            className="lineage-filter-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="キー・鋼種・PJ・route"
          />
          <label>
            種別
            <select value={entityType} onChange={(event) => setEntityType(event.target.value)}>
              <option value="">すべて</option>
              {Object.keys(index?.counts_by_type ?? {}).map((type) => (
                <option key={type} value={type}>{type} ({index?.counts_by_type[type]})</option>
              ))}
            </select>
          </label>
          <label className="lineage-issue-filter">
            <input type="checkbox" checked={issueOnly} onChange={(event) => setIssueOnly(event.target.checked)} />
            問題があるノードだけ
          </label>
          <div className="lineage-result-list">
            {(index?.items ?? []).map((item) => (
              <button
                key={`${item.entity_type}-${item.key}`}
                type="button"
                className={item.key === entityKey ? "active" : ""}
                onClick={() => openNode(item.key)}
              >
                <span className="lineage-result-title"><b>{item.key}</b><small>{item.entity_type}{item.has_issue ? " · 要確認" : ""}</small></span>
                {item.entity_type === "焼鈍" && (
                  <>
                    <span className="lineage-result-meta">{item.family || "family不明"} · {item.project || "PJ不明"} · {item.route || "route不明"}</span>
                    <span className="lineage-result-meta">peak {item.peak_temperature_c == null ? "—" : `${number(item.peak_temperature_c)}°C`} · {item.coating || "—"} · {item.learning_status || "区分なし"}</span>
                    <span className="lineage-result-observations">
                      {Object.entries(item.observation_summary ?? {}).slice(0, 4).map(([property, summary]) => `${property.replace("[MPa]", "").replace("[%]", "")} ${number(summary.mean, 1)}±${number(summary.std, 1)} (n=${summary.n})`).join(" / ") || "焼鈍後観測なし"}
                    </span>
                  </>
                )}
              </button>
            ))}
            {index && !index.items.length && <p className="empty-evidence">一致するキーはありません。</p>}
          </div>
          <small className="lineage-result-limit">検索結果は最大40件。選択するとグラフを開きます。</small>
        </aside>
      {error ? (
        <main className="lineage-main">
          <div className="lineage-load-error">
          <b>{entityKey}</b>
          <p>{error}</p>
          <span>左の検索結果から存在するキーを選んでください。</span>
          </div>
        </main>
      ) : data ? (
        <>
        <main className="lineage-main">
          <LineageGraph
            graph={data.graph}
            selectedKey={data.key}
            onSelect={openNode}
            onLoadMore={() => setGraphLimit((current) => Math.min(200, current + 40))}
          />
            {data.graph.edges.length > 0 && (
              <details className="route-evidence">
                <summary>経路の接続根拠 {data.graph.edges.length}本</summary>
                <div>
                  {data.graph.edges.map((edge) => (
                    <p key={`${edge.source}-${edge.target}`}>
                      <button type="button" onClick={() => openNode(edge.source)}>{edge.source}</button>
                      <span>→</span>
                      <button type="button" onClick={() => openNode(edge.target)}>{edge.target}</button>
                      <small>relation {edge.route_rows.slice(0, 5).join(", ")}{edge.route_rows.length > 5 ? ` +${edge.route_rows.length - 5}` : ""}</small>
                    </p>
                  ))}
                </div>
              </details>
            )}
        </main>
        <aside className="lineage-detail-panel" aria-label="選択ノード詳細">
          <div className="lineage-detail-header">
            <div>
              <span className="overline">
                {data.node.source_sheet} / {data.node.entity_type}
              </span>
              <h3>{data.key}</h3>
              <p>
                {Object.values(data.relations).reduce(
                  (sum, values) => sum + values.length,
                  0,
                )}
                件の関係、{data.node.connected_observation_count}件の接続観測
              </p>
            </div>
            <button
              className="primary-button"
              disabled={!supportsCandidateCreation || !data.candidate_eligible}
              title={supportsCandidateCreation ? data.candidate_reason : "この予測タスクは系譜からの候補化に対応していません"}
              onClick={() => {
                void createCandidate();
              }}
            >
              候補ストックへ追加
            </button>
          </div>
          <p className={`lineage-candidate-note ${supportsCandidateCreation && data.candidate_eligible ? "" : "muted"}`}>{supportsCandidateCreation ? data.candidate_reason : "この予測タスクは系譜からの候補化に対応していません。工程の確認には引き続き利用できます。"}</p>
          {candidateError && <p className="warning">{candidateError}</p>}
          <div className="lineage-detail-grid">
            <section>
              <h3>主要条件</h3>
              <dl>
                {Object.entries(data.node.primary_conditions).map(
                  ([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{value === null ? "—" : String(value)}</dd>
                    </div>
                  ),
                )}
              </dl>
            </section>
            <section>
              <h3>
                上流組成 <small>mass%</small>
              </h3>
              <div className="composition-chips">
                {Object.entries(data.node.composition).map(([key, value]) => (
                  <span key={key}>
                    <b>{key}</b>
                    {number(value, value < 0.01 ? 5 : 3)}
                  </span>
                ))}
              </div>
            </section>
            <section>
              <h3>
                実績ヒートパターン <small>{heat.length}点</small>
              </h3>
              {heat.length ? (
                <>
                <svg
                  viewBox="0 0 420 135"
                  className="lineage-heat"
                  role="img"
                  aria-label="実績ヒートパターン"
                >
                  <line x1="20" x2="400" y1="120" y2="120" />
                  <polyline
                    points={heatPoints}
                    fill="none"
                    stroke="#1f5fc4"
                    strokeWidth="3"
                  />
                  {setHeatPoints && (
                    <polyline
                      points={setHeatPoints}
                      fill="none"
                      stroke="#c17816"
                      strokeWidth="1.5"
                      strokeDasharray="5 4"
                    />
                  )}
                  {heat.map((point) => (
                    <circle
                      key={point.time_s}
                      cx={20 + (point.time_s / maxTime) * 380}
                      cy={120 - (point.temperature_c / maxTemp) * 100}
                      r="3"
                      fill="#1f5fc4"
                    >
                      <title>{`${point.time_s}s / ${point.temperature_c}°C`}</title>
                    </circle>
                  ))}
                </svg>
                <div className="lineage-heat-legend">
                  <span><i className="actual" />実績温度</span>
                  <span><i className="setting" />設定温度</span>
                  {heatStages.map((stage) => (
                    <span className={stage.status && stage.status !== "確定" ? "unmapped" : ""} key={`${stage.category}-${stage.name}`}>
                      {stage.category}{stage.name ? ` / ${stage.name}` : ""}{stage.status ? ` · ${stage.status}` : ""}
                    </span>
                  ))}
                </div>
                </>
              ) : (
                <p className="empty-evidence">
                  このノードに焼鈍履歴は接続されていません。
                </p>
              )}
            </section>
            <section>
              <h3>工程段階別の特性分布</h3>
              {(data.node.observation_groups ?? []).length ? (
                <>
                  <div className="lineage-observation-scroll">
                    <table className="quality-table compact-table">
                    <thead>
                      <tr>
                        <th>段階 / 試験</th>
                        <th>特性</th>
                        <th>n</th>
                        <th>min</th>
                        <th>mean ± SD</th>
                        <th>median</th>
                        <th>max</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(data.node.observation_groups ?? []).map(
                        (group) => {
                          const warnings = group.observations.flatMap((observation) => (observation.output_warnings ?? {})[group.property] ?? []);
                          return <tr key={`${group.test_type}-${group.property}`} className={warnings.length ? "plausibility-warning-row" : undefined}>
                            <td><b>{group.stage}</b><br /><small>{group.test_type}</small></td>
                            <td>{group.property}{warnings.length ? <span className="plausibility-warning">⚠ 物理範囲外</span> : null}</td>
                            <td>{group.count}</td>
                            <td>{number(group.min, 1)}</td>
                            <td>
                              {number(group.mean, 1)} ±{" "}
                              {number(group.std, 1)}
                            </td>
                            <td>{number(group.median, 1)}</td>
                            <td>{number(group.max, 1)}</td>
                          </tr>;
                        },
                      )}
                    </tbody>
                    </table>
                  </div>
                  <details className="similar-more">
                    <summary>観測値を表示</summary>
                    {(data.node.connected_observations ?? []).map((observation) => (
                      <p key={observation.id}>
                        {observation.id} · {observation.source} ·{" "}
                        {Object.entries(observation.outputs)
                          .map(([key, value]) => <span key={key} className={(observation.output_warnings ?? {})[key]?.length ? "plausibility-value" : undefined}>{key} {number(value, 1)}{(observation.output_warnings ?? {})[key]?.length ? <small>⚠ 物理範囲外</small> : null}</span>) }
                        {Object.values(observation.output_warnings ?? {}).flat().map((warning) => <em className="plausibility-reason" key={warning}>{warning}</em>)}
                      </p>
                    ))}
                  </details>
                </>
              ) : (
                <p className="empty-evidence">接続観測はありません。</p>
              )}
            </section>
          </div>
          {data.quality_issues.map((issue) => (
            <p
              className="warning"
              key={issue.issue_id}
            >
              <b>{issueLabels[issue.issue_type] ?? issue.issue_type}</b> · {issue.source_sheet} · {issue.entity_key || "キーなし"}: {issue.detail}
            </p>
          ))}
        </aside>
        </>
      ) : (
        <main className="lineage-main">
          <section className="lineage-empty-overview">
            <span className="overline">NO NODE SELECTED</span>
            <h3>調べるノードを選択してください</h3>
            <p>左の検索結果を選ぶか、キーを直接指定すると、実在する関係線と前後工程を表示します。</p>
            {index && <p>{number(index.total_entities)}ノード / {number(index.relation_rows)} relation行 / {index.detected_issues}件の品質問題</p>}
          </section>
        </main>
      )}
      </div>
    </div>
  );
}

function InputRangeSettingsPage({
  project,
  taskDefinition,
  onProjectChanged,
}: {
  project: ApiProject | undefined;
  taskDefinition: TaskDefinitionContract | null;
  onProjectChanged: (project: ApiProject) => void;
}) {
  const [draft, setDraft] = useState<Record<string, { min: string; max: string }>>({});
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!project || !taskDefinition) return;
    setDraft(Object.fromEntries(numericTaskInputs(taskDefinition).filter((input) => input.editable).map((input) => {
      const configured = project.input_ranges?.[input.id] ?? allowedRange(input);
      return [input.id, { min: String(configured.min), max: String(configured.max) }];
    })));
    setError("");
  }, [project?.id, project?.input_ranges, taskDefinition]);
  if (!project || !taskDefinition) return <div className="page-panel"><p className="empty-evidence">設定を読み込んでいます。</p></div>;
  const inputs = numericTaskInputs(taskDefinition).filter((input) => input.editable);
  const update = (id: string, side: "min" | "max", value: string) => setDraft((current) => ({ ...current, [id]: { ...current[id], [side]: value } }));
  const resetDefaults = () => setDraft(Object.fromEntries(inputs.map((input) => {
    const range = input.default_range ?? allowedRange(input);
    return [input.id, { min: String(range.min), max: String(range.max) }];
  })));
  const save = async () => {
    const inputRanges: Record<string, { min: number; max: number }> = {};
    for (const input of inputs) {
      const range = draft[input.id];
      const min = Number(range?.min);
      const max = Number(range?.max);
      if (!Number.isFinite(min) || !Number.isFinite(max) || min >= max) {
        setError(`${input.label}の許容範囲を確認してください。`);
        return;
      }
      inputRanges[input.id] = { min, max };
    }
    setSaving(true);
    setError("");
    try {
      onProjectChanged(await workbenchApi.updateProject(project.id, { ...project, input_ranges: inputRanges }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存できませんでした。");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="page-panel input-range-settings">
      <div className="page-intro">
        <div><h2>入力範囲設定</h2><p>スライダーと数値入力で使う許容範囲を、プロジェクトごとに設定します。</p></div>
        <div className="project-actions"><button className="outline-button" onClick={resetDefaults}>デフォルトに戻す</button><button className="primary-button" disabled={saving} onClick={() => void save()}>{saving ? "保存中…" : "保存"}</button></div>
      </div>
      <p className="settings-explanation"><b>スライダー全体</b>が許容範囲、<b>色付き帯</b>が学習データ範囲です。許容範囲を広げても、学習範囲外は外挿として表示されます。</p>
      {error && <p className="empty-evidence">{error}</p>}
      <table className="input-range-table">
        <thead><tr><th>入力項目</th><th>許容最小</th><th>許容最大</th><th>デフォルト</th><th>学習範囲</th></tr></thead>
        <tbody>{inputs.map((input) => {
          const range = input.default_range ?? allowedRange(input);
          const training = input.training_range;
          return <tr key={input.id}><th>{input.label}<small>{input.unit}</small></th><td><input type="number" step="any" value={draft[input.id]?.min ?? ""} onChange={(event) => update(input.id, "min", event.target.value)} /></td><td><input type="number" step="any" value={draft[input.id]?.max ?? ""} onChange={(event) => update(input.id, "max", event.target.value)} /></td><td>{rangeNumber(range.min)}–{rangeNumber(range.max)}</td><td>{training ? `${rangeNumber(training.min)}–${rangeNumber(training.max)}` : "—"}</td></tr>;
        })}</tbody>
      </table>
    </div>
  );
}

function LiveScreeningPage({
  projectId,
  candidates,
  selectedId,
  taskDefinition,
  resolvedTaskDefinition,
  initialRunId,
  onRunChange,
  onCandidate,
  onCompare,
}: {
  projectId: string;
  candidates: Candidate[];
  selectedId: string;
  taskDefinition: TaskDefinitionContract | null;
  resolvedTaskDefinition: ResolvedTaskDefinition | null;
  initialRunId?: string;
  onRunChange: (runId: string) => void;
  onCandidate: (candidate: Candidate) => void;
  onCompare: () => void;
}) {
  type VariableRow = {
    field: string;
    mode: "fixed" | "range" | "list";
    first: string;
    second: string;
  };
  type ScreenPoint = ApiScreeningRun["points"][number];
  type ScreenResult = ApiScreeningRun;
  const [variables, setVariables] = useState<VariableRow[]>([]);
  const [samples, setSamples] = useState(64);
  const [target, setTarget] = useState("TS");
  const [targetValue, setTargetValue] = useState("500");
  const [secondaryTargets, setSecondaryTargets] = useState<Record<string, string>>({});
  const [baseCandidateId, setBaseCandidateId] = useState(selectedId);
  const baseCandidate = candidates.find((candidate) => candidate.id === baseCandidateId);
  const optionGroups = resolvedTaskDefinition
    ? resolvedTaskDefinition.task_definition.input_groups.map((group) => ({
        key: group.key,
        label: group.label,
        options: group.fields.flatMap((field) => {
          if (!field.editable) return [];
          if (field.kind !== "heat_pattern") return [{
            value: field.path,
            label: `${field.label}${field.unit ? ` (${field.unit})` : ""}`,
            kind: field.kind,
            choices: field.choices,
            defaultRange: field.default_range,
          }];
          return (baseCandidate?.raw.inputs.heat_pattern ?? []).flatMap((point, index) => [
            {
              value: `heat_pattern.${index}.temperature_c`,
              label: `点${index + 1} 温度 (°C)`,
              kind: "number" as const,
              choices: [] as string[],
              defaultRange: { min: Math.max(0, point.temperature_c - 50), max: point.temperature_c + 50 },
            },
            {
              value: `heat_pattern.${index}.time_s`,
              label: `点${index + 1} 時刻 (s)`,
              kind: "number" as const,
              choices: [] as string[],
              defaultRange: { min: Math.max(0, point.time_s - 10), max: point.time_s + 10 },
            },
          ]);
        }),
      })).filter((group) => group.options.length)
    : [];
  const options = optionGroups.flatMap((group) => group.options);
  const [result, setResult] = useState<ScreenResult | null>(null);
  const [savedRuns, setSavedRuns] = useState<ScreenResult[]>([]);
  const [error, setError] = useState("");
  const [draftDirty, setDraftDirty] = useState(false);
  const [xAxis, setXAxis] = useState("");
  const [yAxis, setYAxis] = useState("");
  const [colorMetric, setColorMetric] = useState("score");
  const [selectedPointIndices, setSelectedPointIndices] = useState<number[]>([]);
  const [focusedPointIndex, setFocusedPointIndex] = useState<number | null>(null);
  const runRequestSequence = useRef(0);
  const activeProjectRef = useRef(projectId);
  activeProjectRef.current = projectId;
  const outputs = taskDefinition?.outputs ?? [];
  const targetDefinition = outputs.find((output) => output.key === target);
  useEffect(() => {
    const defaults = options.filter((option) => option.kind === "number").slice(0, 2).map((option) => ({
      field: option.value,
      mode: "range" as const,
      first: String(option.defaultRange?.min ?? ""),
      second: String(option.defaultRange?.max ?? ""),
    }));
    setVariables(defaults);
    setSecondaryTargets({});
    setResult(null);
    setSelectedPointIndices([]);
    setFocusedPointIndex(null);
    setDraftDirty(false);
  }, [resolvedTaskDefinition?.task_definition.id]);
  useEffect(() => {
    if (outputs.length && !outputs.some((output) => output.key === target)) {
      setTarget(outputs[0].key);
    }
  }, [outputs, target]);
  useEffect(() => {
    if (candidates.some((candidate) => candidate.id === selectedId)) {
      setBaseCandidateId(selectedId);
    } else if (!candidates.some((candidate) => candidate.id === baseCandidateId)) {
      setBaseCandidateId(candidates[0]?.id ?? "");
    }
  }, [candidates, selectedId, baseCandidateId]);
  useEffect(() => {
    const requestProjectId = projectId;
    runRequestSequence.current += 1;
    setResult(null);
    setSavedRuns([]);
    setSelectedPointIndices([]);
    setFocusedPointIndex(null);
    workbenchApi.listScreeningRuns(requestProjectId)
      .then((runs) => { if (activeProjectRef.current === requestProjectId) setSavedRuns(runs); })
      .catch(() => undefined);
  }, [projectId]);
  const updateVariable = (index: number, patch: Partial<VariableRow>) =>
    (setDraftDirty(true), setVariables((rows) =>
      rows.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...patch } : row,
      ),
    ));
  const applyResult = (run: ScreenResult) => {
    setResult(run);
    const varying = Object.entries(run.variables).filter(([, spec]) => spec.mode !== "fixed").map(([field]) => field);
    setXAxis(varying[0] ?? "");
    setYAxis(varying[1] ?? "");
    setColorMetric("score");
    setSelectedPointIndices([]);
    setFocusedPointIndex(run.representative_points[0]?.index ?? null);
    setDraftDirty(false);
  };
  const run = async () => {
    const sequence = ++runRequestSequence.current;
    const requestProjectId = projectId;
    try {
      setError("");
      const specs = Object.fromEntries(
        variables.map((row) => {
          const categorical = options.find((option) => option.value === row.field)?.kind === "categorical";
          if (row.mode === "range")
            return [
              row.field,
              {
                mode: row.mode,
                min: Number(row.first),
                max: Number(row.second),
              },
            ];
          if (row.mode === "list")
            return [
              row.field,
              {
                mode: row.mode,
                values: row.first
                  .split(",")
                  .map((value) =>
                    categorical ? value.trim() : Number(value.trim()),
                  ),
              },
            ];
          return [
            row.field,
            {
              mode: row.mode,
              value: categorical ? row.first.trim() : Number(row.first),
            },
          ];
        }),
      );
      const created = await workbenchApi.createScreeningRun(requestProjectId, {
        base_candidate_id: baseCandidateId,
        variables: specs,
        samples,
        target,
        target_value: targetValue.trim() === "" ? null : Number(targetValue),
        secondary_targets: Object.fromEntries(Object.entries(secondaryTargets).filter(([, value]) => value.trim() !== "").map(([key, value]) => [key, Number(value)])),
      });
      if (sequence !== runRequestSequence.current || activeProjectRef.current !== requestProjectId) return;
      applyResult(created);
      setSavedRuns((runs) => [created, ...runs]);
      onRunChange(created.id);
    } catch (cause) {
      if (sequence !== runRequestSequence.current || activeProjectRef.current !== requestProjectId) return;
      setError(
        `範囲探索を実行できませんでした。${cause instanceof Error && cause.message ? ` ${cause.message}` : ""}`,
      );
    }
  };
  const loadRun = async (runId: string) => {
    const sequence = ++runRequestSequence.current;
    const requestProjectId = projectId;
    setError("");
    let run: ScreenResult;
    try {
      run = await workbenchApi.screeningRun(requestProjectId, runId);
    } catch {
      if (sequence !== runRequestSequence.current || activeProjectRef.current !== requestProjectId) return;
      return setError("作成元の探索は削除済みか、このプロジェクトから参照できません。");
    }
    if (sequence !== runRequestSequence.current || activeProjectRef.current !== requestProjectId) return;
    applyResult(run);
    if (run.base_candidate_id) setBaseCandidateId(run.base_candidate_id);
    setTarget(run.target);
    setTargetValue(run.target_value == null ? "" : String(run.target_value));
    setSecondaryTargets(Object.fromEntries(Object.entries(run.secondary_targets ?? {}).map(([key, value]) => [key, String(value)])));
    setSamples(run.samples);
    if (run.variables)
      setVariables(
        Object.entries(run.variables).map(([field, spec]) => ({
          field,
          mode: spec.mode,
          first:
            spec.mode === "fixed"
              ? String(spec.value ?? "")
              : spec.mode === "list"
                ? (spec.values ?? []).join(",")
                : String(spec.min ?? ""),
          second: spec.mode === "range" ? String(spec.max ?? "") : "",
        })),
      );
    onRunChange(run.id);
  };
  useEffect(() => {
    if (initialRunId && result?.id !== initialRunId) void loadRun(initialRunId);
    return () => {
      runRequestSequence.current += 1;
    };
  }, [initialRunId, projectId]);
  const stockedPointIndices = new Set(candidates.flatMap((candidate) => {
    const provenance = candidate.raw.provenance;
    if (!provenance || provenance.source_kind !== "screening" || !provenance.source_ref || provenance.source_ref.run_id !== result?.id) return [];
    return typeof provenance.source_ref.point_index === "number" ? [provenance.source_ref.point_index] : [];
  }));
  const selectedNewPointIndices = selectedPointIndices.filter((index) => !stockedPointIndices.has(index));
  const remainingCandidateCapacity = Math.max(0, 10 - candidates.length);
  const persistSelected = async () => {
    if (!result || !selectedNewPointIndices.length) return;
    const requestProjectId = projectId;
    const requestRunId = result.id;
    if (selectedNewPointIndices.length > remainingCandidateCapacity) {
      setError(`追加できるのは残り${remainingCandidateCapacity}件です。選択を減らしてください。`);
      return;
    }
    try {
      const response = await workbenchApi.candidatesFromScreening(requestProjectId, requestRunId, selectedNewPointIndices);
      if (activeProjectRef.current !== requestProjectId) return;
      response.candidates.forEach((candidate) => onCandidate(fromApiCandidate(candidate)));
      setSelectedPointIndices([]);
      setError("");
    } catch (cause) {
      if (activeProjectRef.current !== requestProjectId) return;
      setError(cause instanceof Error ? cause.message : "候補を作成できませんでした。");
    }
  };
  const confirmedVaryingFields = result ? Object.entries(result.variables)
    .filter(([field, spec]) => spec.mode !== "fixed" && result.points.some((point) => typeof point.inputs[field] === "number"))
    .map(([field]) => field) : [];
  const axes = [xAxis, yAxis].filter(Boolean);
  const numeric = (axis: string) =>
    result?.points
      .map((point) => Number(point.inputs[axis]))
      .filter(Number.isFinite) ?? [];
  const xValues = numeric(axes[0]);
  const yValues = numeric(axes[1] ?? axes[0]);
  const scale = (
    value: number,
    values: number[],
    start: number,
    span: number,
  ) =>
    start +
    ((value - Math.min(...values)) /
      Math.max(1e-9, Math.max(...values) - Math.min(...values))) *
      span;
  const scores = result?.points.map((point) => point.score).filter((score): score is number => score != null) ?? [];
  const colorValues = colorMetric === "score" ? scores : result?.points.map((point) => (point.predictions?.[colorMetric] ?? (colorMetric === result.target ? point.prediction : undefined))?.value).filter((value): value is number => typeof value === "number") ?? [];
  const opportunity = (point: ScreenPoint) => {
    const value = colorMetric === "score" ? point.score : (point.predictions?.[colorMetric] ?? (colorMetric === result?.target ? point.prediction : undefined))?.value;
    if (value == null || colorValues.length === 0) return "hsl(215 18% 72%)";
    const normalized = (value - Math.min(...colorValues)) / Math.max(1e-9, Math.max(...colorValues) - Math.min(...colorValues));
    const strength = colorMetric === "score" ? 1 - normalized : normalized;
    return `hsl(215 78% ${82 - strength * 42}%)`;
  };
  const axisLabel = (axis: string | undefined) => options.find((option) => option.value === axis)?.label ?? axis ?? "";
  const supportStroke = (status: string) =>
    status === "supported"
      ? "#15936a"
      : status === "caution"
        ? "#ee9200"
        : "#c43d3d";
  const focusedPoint = result?.points.find((point) => point.index === focusedPointIndex) ?? null;
  const hiddenVaryingFields = result ? Object.entries(result.variables).filter(([field, spec]) => spec.mode !== "fixed" && field !== xAxis && field !== yAxis).map(([field]) => field) : [];
  const togglePoint = (index: number) => {
    setFocusedPointIndex(index);
    setSelectedPointIndices((current) => current.includes(index) ? current.filter((item) => item !== index) : [...current, index]);
  };
  return (
    <div className="page-panel explore-page">
      <div className="page-intro">
        <div>
          <h2>範囲探索</h2>
          <p>
            指定範囲を偏りなく確認し、有望領域から複数候補を集めます。
          </p>
        </div>
        <button
          className="primary-button"
          disabled={!baseCandidateId}
          title={baseCandidateId ? "選択した候補を基準に探索します" : "基準候補が必要です"}
          onClick={() => {
            void run();
          }}
        >
          探索を実行
        </button>
      </div>
      {draftDirty && result && <p className="screening-draft-notice">未実行の条件変更があります。図と点詳細は最後に実行した条件のままです。</p>}
      {savedRuns.length > 0 && (
        <section className="saved-runs">
          <h3>保存済み探索</h3>
          <div>
            {savedRuns.slice(0, 8).map((run) => (
              <button
                className={result?.id === run.id ? "active" : ""}
                key={run.id}
                onClick={() => {
                  void loadRun(run.id);
                }}
              >
                <b>{outputs.find((output) => output.key === run.target)?.label ?? run.target}</b> → {run.target_value == null ? "目標なし" : number(run.target_value, 1)} /{" "}
                 {run.samples}点{" "}
                <small>
                  基準: {candidates.find((candidate) => candidate.id === run.base_candidate_id)?.label ?? run.base_candidate_id?.slice(0, 8) ?? "旧保存データ"} ·{" "}
                  {Object.entries(run.variables).map(([field, spec]) => `${axisLabel(field)}=${spec.mode === "range" ? `${number(spec.min ?? 0, 3)}–${number(spec.max ?? 0, 3)}` : spec.mode === "list" ? (spec.values ?? []).join("/") : String(spec.value ?? "")}`).join(" / ")} ·{" "}
                  {run.created_at
                    ? new Date(run.created_at).toLocaleString("ja-JP")
                    : "保存済み"}
                </small>
              </button>
            ))}
          </div>
        </section>
      )}
      <div className="screening-settings">
        <div className="screening-target">
          <label>
            基準候補
            <select value={baseCandidateId} onChange={(event) => { setBaseCandidateId(event.target.value); setDraftDirty(true); }}>
              {candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>{candidate.label}</option>
              ))}
            </select>
          </label>
          <label>
            評価点数
            <input
              type="number"
              min="48"
              max="128"
              value={samples}
              onChange={(event) => { setSamples(Number(event.target.value)); setDraftDirty(true); }}
            />
          </label>
          <label>
            目標特性
            <select
              value={target}
              onChange={(event) => { const next = event.target.value; setTarget(next); setSecondaryTargets((current) => { const updated = { ...current }; delete updated[next]; return updated; }); setDraftDirty(true); }}
            >
              {outputs.map((output) => <option key={output.key} value={output.key}>{output.label} ({output.unit})</option>)}
            </select>
          </label>
          <label>
            目標値 {targetDefinition?.goal_direction === "at_most" ? "（以下）" : targetDefinition?.goal_direction === "at_least" ? "（以上）" : ""}
            <input
              type="number"
              value={targetValue}
              onChange={(event) => { setTargetValue(event.target.value); setDraftDirty(true); }}
            />
          </label>
          {outputs.filter((output) => output.key !== target).map((output) => <label key={output.key}>副条件: {output.label}（{output.goal_direction === "at_most" ? "以下" : "以上"}）<input type="number" value={secondaryTargets[output.key] ?? ""} placeholder="指定なし" onChange={(event) => { setSecondaryTargets((current) => ({ ...current, [output.key]: event.target.value })); setDraftDirty(true); }} /></label>)}
        </div>
        <table className="quality-table variable-table">
          <thead>
            <tr>
              <th>変数</th>
              <th>指定</th>
              <th>値 / 最小</th>
              <th>最大</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {variables.map((row, index) => (
              <tr key={`${row.field}-${index}`}>
                <td>
                  <select
                    value={row.field}
                    onChange={(event) => {
                      const option = options.find((item) => item.value === event.target.value);
                      updateVariable(index, option?.kind === "categorical"
                        ? { field: event.target.value, mode: "list", first: option.choices.join(","), second: "" }
                        : { field: event.target.value, mode: "fixed", first: String(option?.defaultRange?.min ?? ""), second: "" });
                    }}
                  >
                    {optionGroups.map((group) => <optgroup key={group.key} label={group.label}>{group.options.map((option) => <option key={option.value} value={option.value} disabled={variables.some((item, rowIndex) => rowIndex !== index && item.field === option.value)}>{option.label}</option>)}</optgroup>)}
                  </select>
                </td>
                <td>
                  <select
                    value={row.mode}
                    onChange={(event) =>
                      updateVariable(index, {
                        mode: event.target.value as VariableRow["mode"],
                      })
                    }
                  >
                    <option value="fixed">固定</option>
                    <option value="range" disabled={options.find((option) => option.value === row.field)?.kind === "categorical"}>範囲</option>
                    <option value="list">列挙</option>
                  </select>
                </td>
                <td>
                  <input
                    value={row.first}
                    placeholder={row.mode === "list" ? "例: GI,GA" : "値"}
                    onChange={(event) =>
                      updateVariable(index, { first: event.target.value })
                    }
                  />
                </td>
                <td>
                  {row.mode === "range" ? (
                    <input
                      value={row.second}
                      onChange={(event) =>
                        updateVariable(index, { second: event.target.value })
                      }
                    />
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  <button
                    className="icon-delete"
                    disabled={variables.length === 1}
                    onClick={() => {
                      setDraftDirty(true);
                      setVariables((rows) =>
                        rows.filter((_, rowIndex) => rowIndex !== index),
                      );
                    }}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button
          className="outline-button"
          disabled={!options.some((option) => !variables.some((row) => row.field === option.value))}
          onClick={() => {
            const option = options.find((item) => !variables.some((row) => row.field === item.value));
            if (!option) return;
            setDraftDirty(true);
            setVariables((rows) => [...rows, { field: option.value, mode: option.kind === "categorical" ? "list" : "fixed", first: option.kind === "categorical" ? option.choices.join(",") : String(option.defaultRange?.min ?? ""), second: "" }]);
          }}
        >
          変数を追加
        </button>
      </div>
      {error && <p className="warning">{error}</p>}
      {result && (
        <>
          <div className="screening-display-controls">
            <label>X軸<select value={xAxis} onChange={(event) => setXAxis(event.target.value)}>{confirmedVaryingFields.map((field) => <option key={field} value={field}>{axisLabel(field)}</option>)}</select></label>
            <label>Y軸<select value={yAxis} onChange={(event) => setYAxis(event.target.value)}><option value="">点番号</option>{confirmedVaryingFields.filter((field) => field !== xAxis).map((field) => <option key={field} value={field}>{axisLabel(field)}</option>)}</select></label>
            <label>色<select value={colorMetric} onChange={(event) => setColorMetric(event.target.value)}><option value="score">目標に対する有望度</option>{outputs.map((output) => <option key={output.key} value={output.key}>{output.label}</option>)}</select></label>
          </div>
          {hiddenVaryingFields.length > 0 && <p className="screening-hidden-variables"><b>図に出ていない変動条件:</b> {hiddenVaryingFields.map(axisLabel).join(" / ")}。各点の詳細で実値を確認できます。</p>}
          <div className="screening-action-bar" role="status">
            <span><b>{selectedPointIndices.length}</b>件選択 / 新規{selectedNewPointIndices.length}件 / 追加可能{remainingCandidateCapacity}件</span>
            {selectedPointIndices.some((index) => stockedPointIndices.has(index)) && <small>stock済みの点は再追加しません。</small>}
            <button className="primary-button" disabled={!selectedNewPointIndices.length || selectedNewPointIndices.length > remainingCandidateCapacity} onClick={() => void persistSelected()}>{selectedNewPointIndices.length}件を候補へ追加</button>
            <button className="outline-button" disabled={!candidates.length} onClick={onCompare}>候補比較へ</button>
          </div>
          <div className="screen-legend">
            <span className="opportunity-scale" />
            {colorMetric === "score" ? result.score_contract?.display_label ?? "目標に対して有望" : outputs.find((output) => output.key === colorMetric)?.label ?? colorMetric} <span className="support-key supported" />
            範囲内 <span className="support-key caution" />
            要確認 <span className="support-key extrapolated" />
            外挿
          </div>
          <svg
            className="screen-map"
            viewBox="0 0 600 300"
            role="img"
            aria-label={`${axes.map(axisLabel).join(" × ")} の探索結果。色が濃いほど目標方向に有望で、枠線が学習範囲を示します。`}
          >
            {result.points.map((point, index) => {
              const cx = axes.length
                ? scale(Number(point.inputs[axes[0]]), xValues, 35, 530)
                : 35 + (index % 12) * 46;
              const cy =
                axes.length > 1
                  ? 270 - scale(Number(point.inputs[axes[1]]), yValues, 0, 235)
                  : 35 + Math.floor(index / 12) * 50;
              return (
                <circle
                  key={point.index}
                  className={selectedPointIndices.includes(point.index) ? "selected" : ""}
                  cx={cx}
                  cy={cy}
                  r={selectedPointIndices.includes(point.index) ? "9" : "7"}
                  fill={opportunity(point)}
                  stroke={supportStroke(point.support.status)}
                  strokeWidth="3"
                  opacity={
                    point.support.status === "extrapolated" ? ".55" : ".9"
                  }
                  onClick={() => {
                    togglePoint(point.index);
                  }}
                >
                  <title>{`${axes.map((axis) => `${axis}: ${point.inputs[axis]}`).join(" / ")} / ${point.prediction.value.toFixed(1)} ${point.prediction.unit} / ${point.support.status}`}</title>
                </circle>
              );
            })}
            <text x="300" y="296" textAnchor="middle">
              {axisLabel(axes[0])}
            </text>
            <text x="8" y="16">
              {axisLabel(axes[1])}
            </text>
          </svg>
          {focusedPoint && <section className="screening-point-detail" aria-label="選択した探索点の詳細">
            <div className="panel-title"><h3>点 {focusedPoint.index + 1}</h3><span className={`support-badge ${focusedPoint.support.status}`}>{focusedPoint.support.message}</span></div>
            <div className="screening-point-predictions">{Object.entries({ [result.target]: focusedPoint.prediction, ...(focusedPoint.predictions ?? {}) }).map(([key, prediction]) => <div key={key}><b>{outputs.find((output) => output.key === key)?.label ?? key}</b><strong>{number(prediction.value, 1)} {prediction.unit}</strong><small>{number(prediction.lower, 1)}–{number(prediction.upper, 1)}{prediction.goal_probability != null ? ` / 達成確率 ${Math.round(prediction.goal_probability * 100)}%` : ""}</small>{focusedPoint.secondary_goal_evaluations?.[key]?.achieved != null && <em>{focusedPoint.secondary_goal_evaluations[key].achieved ? "副条件を満たす" : "副条件を満たさない"}</em>}</div>)}</div>
            <p><b>全変動条件:</b> {Object.entries(focusedPoint.inputs).map(([key, value]) => `${axisLabel(key)} ${typeof value === "number" ? number(value, 3) : value}`).join(" / ")}</p>
            <p><b>支持度:</b> {focusedPoint.support.status} / percentile {number(focusedPoint.support.percentile, 1)} / 参照{focusedPoint.support.reference_count}件</p>
            {focusedPoint.warnings?.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
            {(focusedPoint.similar ?? []).length > 0 && <p><b>近い実績:</b> {(focusedPoint.similar ?? []).slice(0, 3).map((item) => `${item.observation_id || item.parent_key} (距離 ${number(item.distance, 2)})`).join(" / ")}</p>}
          </section>}
          <table className="quality-table">
            <thead>
              <tr>
                <th>選択</th>
                <th>代表点</th>
                <th>条件</th>
                <th>全予測 / 支持度</th>
              </tr>
            </thead>
            <tbody>
              {result.representative_points.map((point) => (
                <tr key={point.index}>
                  <td><input type="checkbox" aria-label={`点 ${point.index + 1}を選択`} checked={selectedPointIndices.includes(point.index)} disabled={stockedPointIndices.has(point.index)} onChange={() => togglePoint(point.index)} /></td>
                  <td>{point.index + 1}</td>
                  <td>
                    {Object.entries(point.inputs)
                      .map(
                        ([key, value]) =>
                          `${key}: ${typeof value === "number" ? number(value, 3) : value}`,
                      )
                      .join(" / ")}
                  </td>
                  <td>
                    {Object.entries({ [result.target]: point.prediction, ...(point.predictions ?? {}) }).map(([key, prediction]) => `${outputs.find((output) => output.key === key)?.label ?? key} ${number(prediction.value, 1)} ${prediction.unit}`).join(" / ")}<br /><small>{point.support.message}{stockedPointIndices.has(point.index) ? " / stock済み" : ""}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

export default App;
