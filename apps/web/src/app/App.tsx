import { useEffect, useRef, useState } from "react";
import { provenanceNavigation } from "./candidateProvenance";
import { navigationUrl, readNavigationIntent, withView, type NavigationIntent, type WorkbenchView } from "./navigation";
import { ChainWorkbenchPage, WorkbenchEmptyState, WorkbenchPage, useWorkbenchSession } from "../features/workbench";
import { ProjectHub } from "../features/projects";
import { ScreeningPage } from "../features/screening";
import { LineagePage } from "../features/lineage";
import { DataExploreNavigation, LiveDataQualityPage } from "../features/quality";
import { DeveloperAdminPage } from "../features/admin";
import { DataLibraryPage, ProfileWorkbenchPage } from "../features/data-library";
import { WorkspaceNoticeBanner } from "../shared/ui/WorkspaceNoticeBanner";
import {
  workbenchApi,
  type ApiSubsystemAvailability,
} from "../shared/api/workbench-api";

type Tab = WorkbenchView;
const lastNavigationStorageKey = "material-workbench-last-navigation";
const projectNavItems: Array<{ id: Tab; label: string; active: Tab[]; requiresDataExplorer?: boolean }> = [
  { id: "project", label: "概要", active: ["project"] },
  { id: "lineage", label: "データ探索", active: ["lineage", "quality"], requiresDataExplorer: true },
  { id: "explore", label: "範囲探索", active: ["explore"] },
  { id: "candidates", label: "候補比較", active: ["candidates"] },
  { id: "settings", label: "開発・管理", active: ["settings"] },
];

function DataExploreUnavailable() {
  return <div className="page-panel">
    <div className="page-intro"><div><h2>データ探索</h2><p>この予測タスクではデータ探索に対応していません。</p></div></div>
  </div>;
}

function ChainModeUnavailablePanel({ onOpenCandidates }: { onOpenCandidates: () => void }) {
  return <div className="page-panel task-unavailable-panel" role="status">
    <span className="overline">CHAIN PROJECT</span>
    <h2>この画面はChainプロジェクトでは利用できません</h2>
    <p>Chainは固定したRevisionの段を順に実行するため、単一Task向けの範囲探索・データ探索・開発管理は使いません。</p>
    <p>配合と工程条件の編集、A → B → Cの実行、段別の実測照合は候補作業面で行います。</p>
    <button type="button" className="primary-button" onClick={onOpenCandidates}>Chain候補を開く</button>
  </div>;
}

function ConnectionBanner({ retrying, onRetry }: { retrying: boolean; onRetry: () => void }) {
  return <div className="connection-banner" role="alert">
    <div>
      <strong>APIへ接続できません</strong>
      <span>ローカルAPIが起動していない、または保存データと構成が不整合の可能性があります。保存済みのデータは変更されていません。</span>
    </div>
    <button type="button" className="outline-button" disabled={retrying} onClick={onRetry}>{retrying ? "再試行中…" : "再試行"}</button>
  </div>;
}

function TaskUnavailablePanel({ message }: { message: string }) {
  return <div className="page-panel task-unavailable-panel" role="status">
    <span className="overline">TASK UNAVAILABLE</span>
    <h2>この予測タスクは一時的に利用できません</h2>
    <p>{message}</p>
    <p>プロジェクト概要では、保存済みの候補・予測・実測・判断履歴を引き続き確認できます。</p>
  </div>;
}

function readStartupNavigation(): NavigationIntent {
  if (new URLSearchParams(window.location.search).has("view")) return readNavigationIntent();
  try {
    const savedSearch = window.localStorage.getItem(lastNavigationStorageKey);
    return savedSearch ? readNavigationIntent(savedSearch) : readNavigationIntent();
  } catch {
    return readNavigationIntent();
  }
}

function rememberNavigation(intent: NavigationIntent) {
  try {
    window.localStorage.setItem(lastNavigationStorageKey, new URL(navigationUrl(intent), window.location.href).search);
  } catch {
    // Navigation remains usable when browser storage is unavailable.
  }
}

function App() {
  const [navigation, setNavigation] = useState<NavigationIntent>(() => readStartupNavigation());
  const [requestedDatasetViewId, setRequestedDatasetViewId] = useState<string>();
  const [retrying, setRetrying] = useState(false);
  const [subsystemAvailability, setSubsystemAvailability] = useState<ApiSubsystemAvailability[]>([]);
  const navigationRef = useRef(navigation);
  const tab = navigation.view;

  function navigate(intent: NavigationIntent, replace = false) {
    const next = Object.freeze(intent);
    navigationRef.current = next;
    setNavigation(next);
    rememberNavigation(next);
    window.history[replace ? "replaceState" : "pushState"]({}, "", navigationUrl(next));
  }

  const session = useWorkbenchSession({
    requestedProjectId: navigation.projectId,
    requestedCandidateId: navigation.candidateId,
    onLocationReplace: (projectId, candidateId) => {
      const current = navigationRef.current;
      navigate(
        current.view === "data-library" || current.view === "profile-workbench"
          ? current
          : current.projectId && current.projectId !== projectId
            ? withView({ view: current.view, projectId, candidateId, adminSection: current.adminSection }, current.view)
            : { ...current, projectId, candidateId },
        true,
      );
    },
    onCandidateSelected: (projectId, candidateId) => {
      navigate({ view: "candidates", projectId, candidateId }, true);
    },
    onOpenProvenance: (provenance) => {
      const intent = provenanceNavigation(provenance, session.activeProjectId);
      if (intent) navigate(intent);
    },
  });
  const {
    activeProject,
    activeProjectId,
    apiState,
    brokenOriginCandidateId,
    candidates,
    editor,
    loadError,
    notice,
    operations,
    prediction,
    projects,
    resolvedTaskDefinition,
    selected,
    selectedId,
    taskDefinition,
    taskAvailability,
  } = session;
  const { error: previewError, preview, previewsByCandidate } = prediction;
  const chainProject = activeProject?.scientific_identity?.identity_kind === "chain";
  const chainSubsystem = subsystemAvailability.find(
    (item) => item.kind === "chain"
      && item.resource_id === "welding-consumable-a-b-c-v1",
  );
  const taskUnavailable = taskAvailability?.status === "unavailable";
  const unavailableScopedTab = taskUnavailable
    && !chainProject
    && tab !== "project"
    && tab !== "data-library"
    && tab !== "profile-workbench";
  // Chain projects have no single-task contract, so these views cannot answer
  // anything. They stay reachable by deep link, and must explain themselves
  // instead of failing inside a single-task surface.
  const chainScopedTab = chainProject
    && (tab === "explore" || tab === "lineage" || tab === "quality" || tab === "settings");
  const dataExplorer = taskUnavailable ? null : resolvedTaskDefinition?.data_explorer;
  const qualityAvailable = dataExplorer?.quality === true;
  const lineageAvailable = dataExplorer?.lineage === true;
  const visibleProjectNavItems = projectNavItems.filter((item) => (
    (!taskUnavailable && !chainProject)
      || item.id === "project"
      || (chainProject && item.id === "candidates")
  ) && (!item.requiresDataExplorer || qualityAvailable || lineageAvailable));
  const dataLibraryMode = tab === "data-library" || tab === "profile-workbench";

  function selectCandidate(candidateId: string, replace = true) {
    session.selectCandidate(candidateId, false);
    navigate({ view: "candidates", projectId: activeProjectId, candidateId }, replace);
  }

  function rememberCandidate(candidateId: string) {
    session.selectCandidate(candidateId, false);
    navigate({ ...navigationRef.current, projectId: activeProjectId, candidateId }, true);
  }

  function navigateProjectView(item: { id: Tab; active: Tab[] }) {
    const destination = item.active.includes(tab)
      ? tab
      : item.id === "lineage" && !lineageAvailable && qualityAvailable
        ? "quality"
        : item.id;
    const intent = withView(navigationRef.current, destination);
    navigate({
      ...intent,
      projectId: activeProjectId,
      candidateId: item.id === "candidates" ? selectedId || undefined : intent.candidateId,
    });
  }

  function startProjectForDataset(datasetViewRevisionId: string) {
    setRequestedDatasetViewId(datasetViewRevisionId);
    navigate({ view: "project", projectId: activeProjectId });
  }

  useEffect(() => {
    const onPopState = () => {
      const intent = readNavigationIntent();
      navigationRef.current = intent;
      setNavigation(intent);
      rememberNavigation(intent);
      const targetProjectId = intent.projectId ?? activeProjectId;
      void session.openLocation(targetProjectId, intent.candidateId);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [activeProjectId, candidates]);

  useEffect(() => {
    const current = navigationRef.current;
    rememberNavigation(current);
    if (!new URLSearchParams(window.location.search).has("view")) {
      window.history.replaceState({}, "", navigationUrl(current));
    }
  }, []);

  useEffect(() => {
    if (apiState === "offline") return;
    let active = true;
    void workbenchApi.listSubsystemAvailability().then((items) => {
      if (active) setSubsystemAvailability(items);
    }).catch(() => {
      if (active) setSubsystemAvailability([]);
    });
    return () => { active = false; };
  }, [apiState]);


  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">Material Decision Workbench</div>
        <nav aria-label="ホーム">
          <button
            className="nav-button"
            aria-current={!dataLibraryMode ? "page" : undefined}
            onClick={() => navigate({ view: "project", projectId: activeProjectId })}
          >
            プロジェクト
          </button>
          <button
            className={dataLibraryMode ? "nav-button active" : "nav-button"}
            aria-current={dataLibraryMode ? "page" : undefined}
            onClick={() => navigate({ view: "data-library" })}
          >
            データライブラリ
          </button>
        </nav>
      </header>
      <main>
        {apiState === "offline" && <ConnectionBanner retrying={retrying} onRetry={() => {
          setRetrying(true);
          void session.retryOpenWorkspace().finally(() => setRetrying(false));
        }} />}
        {!dataLibraryMode && <div className="context-bar">
          <div className="context-primary-row">
            <h1 title={activeProject?.name ?? undefined}>{activeProject?.name ?? "プロジェクトを読み込んでいます"}</h1>
            <div className="run-actions">
              {tab !== "candidates" && !taskUnavailable && !chainProject && (
                <button
                  type="button"
                  className="stock-button"
                  onClick={() => navigate({
                    view: "candidates",
                    projectId: activeProjectId,
                    candidateId: selectedId || undefined,
                  })}
                >
                  <span>候補</span><b>{candidates.length}</b><span>件を比較</span>
                </button>
              )}
              {apiState !== "ready" && (
                <span className={`api-state ${apiState}`}>
                  {apiState === "loading" ? "プレビュー更新中" : "API 未接続"}
                </span>
              )}
              {tab === "candidates" && selected && (
                <button className="outline-button" onClick={() => navigate({ view: "project", projectId: activeProjectId, candidateId: selected.id })}>保存結果・履歴</button>
              )}
            </div>
          </div>
          <nav className="project-nav" aria-label="プロジェクト内メニュー">
            {visibleProjectNavItems.map((item) => (
              <button
                type="button"
                className={item.active.includes(tab) ? "project-nav-button active" : "project-nav-button"}
                onClick={() => navigateProjectView(item)}
                key={item.id}
              >
                {item.label}
              </button>
            ))}
          </nav>
        </div>}
        {!dataLibraryMode && notice && <WorkspaceNoticeBanner notice={notice} onDismiss={session.dismissNotice} />}
        {tab === "project" && (
          <ProjectHub
            projects={projects}
            activeProjectId={activeProjectId}
            candidate={selected}
            taskDefinition={taskDefinition}
            supportsLineageCandidate={dataExplorer?.candidate_creation === true}
            operations={resolvedTaskDefinition?.runtime_capability.operations}
            currentPreviews={prediction.previewsByCandidate}
            taskAvailability={taskAvailability}
            subsystemAvailability={subsystemAvailability}
            offline={apiState === "offline"}
            onProjectChanged={(project) => {
              void session.refreshProjectDefinition(project);
            }}
            onProjectArchived={(projectId) => session.archiveProject(projectId)}
            onProjectRestored={(projectId) => session.restoreProject(projectId)}
            onSwitch={(projectId) => {
              navigate({ view: "project", projectId }, true);
              void session.loadProject(projectId);
            }}
            onNavigate={(view, candidateId) => {
              navigate({ view, projectId: activeProjectId, candidateId }, true);
              if (candidateId) selectCandidate(candidateId, true);
            }}
            onSnapshotNavigate={(snapshotId) => navigate({ view: "project", projectId: activeProjectId, snapshotId }, true)}
            onRestore={(candidate) => {
              session.restoreCandidate(candidate);
            }}
            requestedSnapshotId={navigation.snapshotId}
            requestedDatasetViewId={requestedDatasetViewId}
            requestedSettingsSection={navigation.projectSettings}
            onCreationIntentConsumed={() => setRequestedDatasetViewId(undefined)}
          />
        )}
        {tab === "data-library" && <DataLibraryPage
          projects={projects}
          onAddDataset={() => navigate({ view: "profile-workbench" })}
          onStartProject={startProjectForDataset}
        />}
        {tab === "profile-workbench" && <ProfileWorkbenchPage
          onOpenDataLibrary={() => navigate({ view: "data-library" })}
          onStartProject={startProjectForDataset}
        />}
        {unavailableScopedTab && (
          <TaskUnavailablePanel message={taskAvailability?.message ?? "このタスクは現在利用できません。"} />
        )}
        {chainScopedTab && (
          <ChainModeUnavailablePanel onOpenCandidates={() => navigate({
            view: "candidates",
            projectId: activeProjectId,
            candidateId: selectedId || undefined,
          })} />
        )}
        {tab === "settings" && !taskUnavailable && !chainProject && (
          <DeveloperAdminPage
            project={activeProject}
            taskDefinition={taskDefinition}
            resolvedTaskDefinition={resolvedTaskDefinition}
            initialSection={navigation.adminSection}
            onSectionChange={(adminSection) => navigate({ ...navigationRef.current, view: "settings", projectId: activeProjectId, adminSection }, true)}
            qualityFilters={{
              issueId: navigation.qualityIssueId,
              type: navigation.qualityType,
              sheet: navigation.qualitySheet,
              key: navigation.qualityKey,
            }}
            onQualityFiltersChange={(filters) => navigate({
              ...navigationRef.current,
              view: "settings",
              projectId: activeProjectId,
              qualityIssueId: filters.issueId,
              qualityType: filters.type,
              qualitySheet: filters.sheet,
              qualityKey: filters.key,
            }, true)}
            onOpenLineage={(issue, filters) => navigate({
              view: "lineage",
              projectId: activeProjectId,
              entityKey: issue.focus_entity_key ?? undefined,
              qualityIssueId: issue.issue_id,
              qualityType: filters.type,
              qualitySheet: filters.sheet,
              qualityKey: filters.key,
            })}
            onProjectChanged={(project) => {
              void session.refreshAdminProject(project);
            }}
            onOpenProfileWorkbench={() => navigate({ view: "profile-workbench" })}
            onOpenQualityIssues={(filters) => navigate({
              view: "quality",
              projectId: activeProjectId,
              qualityType: filters.type,
              qualitySheet: filters.sheet,
              qualityKey: filters.key,
            })}
          />
        )}
        {tab === "candidates" && chainProject && (
          <ChainWorkbenchPage
            projectId={activeProjectId}
            initialCandidateId={navigation.candidateId}
            unavailable={chainSubsystem?.status === "unavailable" ? chainSubsystem : undefined}
            onCandidateSelected={(candidateId) => navigate({
              view: "candidates",
              projectId: activeProjectId,
              candidateId,
            }, true)}
          />
        )}
        {tab === "candidates" && !chainProject && !taskUnavailable &&
          (selected ? (
            <WorkbenchPage
              candidates={candidates}
              projectId={activeProjectId}
              project={activeProject ?? null}
              targetValues={activeProject?.target_values ?? {}}
              inputRanges={activeProject?.input_ranges ?? {}}
              responseCurveRanges={activeProject?.response_curve_ranges ?? {}}
              decisionCandidateId={activeProject?.decision_candidate_id ?? ""}
              selected={selected}
              selectedId={selectedId}
              taskDefinition={taskDefinition}
              operations={operations}
              application={resolvedTaskDefinition?.application}
              saveState={editor.saveStates[selected.id] ?? "idle"}
              saveStates={editor.saveStates}
              fieldErrors={editor.fieldErrors[selected.id] ?? []}
              onReload={() => editor.reload(selected.id)}
              onCopyDraft={() => void editor.copyDraft(selected)}
              preview={preview}
              previewError={previewError}
              onRetryPreview={prediction.retry}
              previewsByCandidate={previewsByCandidate}
              onSelect={(candidateId) => selectCandidate(candidateId)}
              originBroken={brokenOriginCandidateId === selected.id}
              onOpenOrigin={() => {
                void session.openOrigin();
              }}
              onInput={session.updateCandidateInput}
              onText={session.updateCandidateText}
              onBlend={session.updateCandidateBlend}
              onBlendLocks={session.updateCandidateBlendLocks}
              onHeat={session.updateHeat}
              onHeatTimeBasis={session.updateCandidateHeatTimeBasis}
              onAddHeat={session.addHeatPoint}
              onDeleteHeat={session.deleteHeatPoint}
              onCopy={(candidateId) => void session.copyCandidate(candidateId)}
              onDelete={(candidateId) => void session.deleteCandidate(candidateId)}
              onSave={(candidate) => void prediction.runDetailedPrediction(candidate)}
              savedRevisionsByCandidate={prediction.savedRevisionsByCandidate}
              savingCandidateIds={prediction.savingCandidateIds}
              snapshotHistoryState={prediction.snapshotHistoryState}
              onAdd={() => {
                void session.addCandidate();
              }}
              onAddCandidateFromLineage={session.addCandidateFromLineage}
              onImported={(imported) => {
                if (imported.length) void session.loadProject(activeProjectId, selectedId || undefined);
              }}
              onOptimizedCandidate={(candidate) => {
                void session.loadProject(activeProjectId, candidate.id);
              }}
              onProjectChanged={(project) => {
                void session.refreshAdminProject(project);
              }}
              onConfigureGoals={() => navigate({
                view: "project",
                projectId: activeProjectId,
                projectSettings: "targets",
              })}
              onConfigureSupport={() => navigate({
                view: "settings",
                projectId: activeProjectId,
                adminSection: "ranges",
              })}
            />
          ) : (
            <WorkbenchEmptyState
              loading={apiState === "loading"}
              error={loadError}
              onCreate={() => void session.createStarterCandidate()}
            />
          ))}
        {tab === "quality" && !taskUnavailable && !chainProject && (qualityAvailable ? (
          <div className="data-explore-page">
            <DataExploreNavigation active="quality" qualityAvailable={qualityAvailable} lineageAvailable={lineageAvailable} onNavigate={(view) => navigate(withView(navigationRef.current, view))} />
            <LiveDataQualityPage
            projectId={activeProjectId}
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
          </div>
        ) : <DataExploreUnavailable />)}
        {tab === "lineage" && !taskUnavailable && !chainProject && (lineageAvailable ? (
          <div className="data-explore-page">
            <DataExploreNavigation active="lineage" qualityAvailable={qualityAvailable} lineageAvailable={lineageAvailable} onNavigate={(view) => navigate(withView(navigationRef.current, view))} />
            <LineagePage
            projectId={activeProjectId}
            supportsCandidateCreation={dataExplorer?.candidate_creation ?? false}
            outputs={taskDefinition?.outputs ?? []}
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
              const count = session.acceptCandidate(candidate);
              rememberCandidate(candidate.id);
              session.notifySuccess(`${candidate.label} を候補ストックへ追加しました（${count}件）`);
            }}
            />
          </div>
        ) : <DataExploreUnavailable />)}
        {tab === "explore" && !taskUnavailable && !chainProject && (
          <ScreeningPage
            projectId={activeProjectId}
            project={activeProject}
            candidates={candidates}
            selectedId={selectedId}
            taskDefinition={taskDefinition}
            resolvedTaskDefinition={resolvedTaskDefinition}
            initialRunId={navigation.screeningRunId}
            onRunChange={(screeningRunId) => navigate({ view: "explore", projectId: activeProjectId, screeningRunId }, true)}
            onCandidate={(candidate) => {
              const count = session.acceptCandidate(candidate);
              rememberCandidate(candidate.id);
              session.notifySuccess(`${candidate.label} を候補ストックへ追加しました（${count}件）`);
            }}
            onCompare={() => navigate({ view: "candidates", projectId: activeProjectId }, true)}
            onCreateStarter={() => void session.createStarterCandidate()}
          />
        )}
      </main>
    </div>
  );
}

export default App;
