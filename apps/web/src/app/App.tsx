import { useEffect, useRef, useState } from "react";
import { provenanceNavigation } from "./candidateProvenance";
import { isLegacyQualityAdminNavigation, navigationUrl, readNavigationIntent, withView, type NavigationIntent, type WorkbenchView } from "./navigation";
import { ChainWorkbenchPage, WorkbenchEmptyState, WorkbenchPage, apiStartupWaitText, useWorkbenchSession, type StartupDiagnostic } from "../features/workbench";
import { chainAvailability, chainStagePath, ProjectHub, resolveFixedChain } from "../features/projects";
import { ScreeningPage } from "../features/screening";
import { LineagePage } from "../features/lineage";
import { DataExploreNavigation, LiveDataQualityPage } from "../features/quality";
import { ProjectScopedSettings, WorkspaceAdminPage } from "../features/admin";
import { DataLibraryPage, ProfileWorkbenchPage } from "../features/data-library";
import { WorkspaceManagerDialog } from "../features/workspace";
import { WorkspaceNoticeBanner } from "../shared/ui/WorkspaceNoticeBanner";
import type { WorkspaceNotice } from "../shared/workspaceNotice";
import {
  workbenchApi,
  type ApiChainTemplate,
  type ApiSubsystemAvailability,
} from "../shared/api/workbench-api";

type Tab = WorkbenchView;
type HomeNavigationIcon = "project" | "data" | "workspace";
const lastNavigationStorageKey = "material-workbench-last-navigation";
const projectNavItems: Array<{ id: Tab; label: string; active: Tab[]; requiresDataExplorer?: boolean }> = [
  { id: "project", label: "概要", active: ["project"] },
  { id: "lineage", label: "データ探索", active: ["lineage", "quality"], requiresDataExplorer: true },
  { id: "explore", label: "範囲探索", active: ["explore"] },
  { id: "candidates", label: "候補比較", active: ["candidates"] },
];

function HomeNavIcon({ icon }: { icon: HomeNavigationIcon }) {
  if (icon === "project") {
    return <svg className="home-nav-icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d="M3 4.5h5l1.4 1.7H17v9.3H3z" />
    </svg>;
  }
  if (icon === "data") {
    return <svg className="home-nav-icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <ellipse cx="10" cy="4.5" rx="6.5" ry="2.5" />
      <path d="M3.5 4.5v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-5M3.5 9.5v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-5" />
    </svg>;
  }
  return <svg className="home-nav-icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
    <path d="M3.5 6.5h13v10h-13zM6 3.5h8v3H6zM7 10h6M7 13h6" />
  </svg>;
}

function DataExploreUnavailable() {
  return <div className="page-panel">
    <div className="page-intro"><div><h2>データ探索</h2><p>この予測タスクではデータ探索に対応していません。</p></div></div>
  </div>;
}

function ChainModeUnavailablePanel({ onOpenCandidates, stagePath }: { onOpenCandidates: () => void; stagePath: string }) {
  return <div className="page-panel task-unavailable-panel" role="status">
    <span className="overline">CHAIN PROJECT</span>
    <h2>この画面はChainプロジェクトでは利用できません</h2>
    <p>Chainは固定したRevisionの段を順に実行するため、単一Task向けの範囲探索・データ探索・開発管理は使いません。</p>
    <p>条件の編集、{stagePath}の実行、段別の実測照合は候補作業面で行います。</p>
    <button type="button" className="primary-button" onClick={onOpenCandidates}>Chain候補を開く</button>
  </div>;
}

function StartupBanner({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(() => Date.now() - startedAt);
  useEffect(() => {
    const timer = window.setInterval(() => setElapsed(Date.now() - startedAt), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  return <div className="connection-banner starting" role="status">
    <div>
      <strong>{apiStartupWaitText(elapsed)}</strong>
      <span>ExcelとModel Packageの読み込みが終わるまで自動で再試行します。操作は不要です。</span>
    </div>
  </div>;
}

function ConnectionBanner({ retrying, onRetry, diagnostic }: { retrying: boolean; onRetry: () => void; diagnostic: StartupDiagnostic | null }) {
  return <div className="connection-banner" role="alert">
    <div>
      <strong>{diagnostic ? "Workspaceの互換性検査で起動を停止しました" : "APIへ接続できません"}</strong>
      {diagnostic ? <>
        <span>保存済みデータを変更しないため、APIは起動していません。次の診断内容を解決してから再試行してください。</span>
        <div className="startup-diagnostic-list">
          {diagnostic.report.findings.map((finding, index) => <dl key={`${finding.stage}-${finding.resource_id}-${index}`} className="startup-diagnostic-finding">
            <div><dt>stage</dt><dd>{finding.stage}</dd></div>
            <div><dt>resource_id</dt><dd>{finding.resource_id}</dd></div>
            <div><dt>cause</dt><dd>{finding.cause}</dd></div>
            <div><dt>impact</dt><dd>{finding.impact}</dd></div>
            <div><dt>recovery_hint</dt><dd>{finding.recovery_hint}</dd></div>
          </dl>)}
        </div>
        <span className="connection-banner-steps">起動ログ: <code>{diagnostic.log_path}</code><br />復旧手順: <code>{diagnostic.recovery_route}</code></span>
      </> : <>
        <span>自動再試行の時間内に接続できませんでした。保存済みのデータは変更されていません。ローカルAPIが起動していない可能性があります。</span>
        <span className="connection-banner-steps">起動ログ（Desktop版は <code>logs/material-workbench-api.log</code>、開発時は <code>npm run dev</code> のapi出力）を確認してください。</span>
      </>}
    </div>
    <button type="button" className="outline-button" disabled={retrying} onClick={onRetry}>{retrying ? "再試行中…" : "再試行"}</button>
  </div>;
}

function TaskUnavailablePanel({
  message,
  onOpenSettings,
}: {
  message: string;
  onOpenSettings: () => void;
}) {
  return <div className="page-panel task-unavailable-panel" role="status">
    <span className="overline">予測タスクの利用状況</span>
    <h2>この予測タスクは一時的に利用できません</h2>
    <p>{message}</p>
    <p>プロジェクト概要では、保存済みの候補・予測・実測・判断履歴を引き続き確認できます。</p>
    <button type="button" className="primary-button" onClick={onOpenSettings}>参照状態を確認する</button>
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
  const [subsystemAvailabilityLoaded, setSubsystemAvailabilityLoaded] = useState(false);
  const [chainTemplates, setChainTemplates] = useState<ApiChainTemplate[]>([]);
  const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false);
  const [desktopWorkspaceNotice, setDesktopWorkspaceNotice] = useState<WorkspaceNotice | null>(null);
  const navigationRef = useRef(navigation);
  const workspaceButtonRef = useRef<HTMLButtonElement>(null);
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
  const chainIdentity = activeProject?.scientific_identity?.identity_kind === "chain"
    ? activeProject.scientific_identity
    : null;
  const activeChainRevision = resolveFixedChain(chainIdentity, chainTemplates).revision;
  const activeChainId = activeChainRevision?.chain_id;
  const activeChainAvailability = chainAvailability(
    subsystemAvailability,
    activeChainId,
    "chain",
  );
  const taskUnavailable = taskAvailability?.status === "unavailable";
  const unavailableScopedTab = taskUnavailable
    && !chainProject
    && tab !== "project"
    && tab !== "workspace"
    && tab !== "data-library"
    && tab !== "profile-workbench";
  // Chain projects have no single-task contract, so these views cannot answer
  // anything. They stay reachable by deep link, and must explain themselves
  // instead of failing inside a single-task surface.
  const chainScopedTab = chainProject
    && (tab === "explore" || tab === "lineage" || tab === "quality");
  const dataExplorer = taskUnavailable ? null : resolvedTaskDefinition?.data_explorer;
  const qualityAvailable = dataExplorer?.quality === true;
  const lineageAvailable = dataExplorer?.lineage === true;
  const visibleProjectNavItems = projectNavItems.filter((item) => (
    (!chainProject && (!taskUnavailable || item.id === "project"))
      || (chainProject && item.id === "candidates")
  ) && (!item.requiresDataExplorer || qualityAvailable || lineageAvailable));
  const workspaceLevelMode = tab === "data-library" || tab === "profile-workbench" || tab === "workspace";
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
      if (isLegacyQualityAdminNavigation()) {
        window.history.replaceState({}, "", navigationUrl(intent));
      }
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
    const params = new URLSearchParams(window.location.search);
    if (!params.has("view") || params.get("view") === "settings") {
      window.history.replaceState({}, "", navigationUrl(current));
    }
  }, []);

  useEffect(() => {
    void window.workbenchDesktop?.takeWorkspaceNotice().then((receipt) => {
      if (receipt) {
        setDesktopWorkspaceNotice({
          id: Date.now(),
          kind: receipt.tone,
          message: receipt.message,
        });
      }
    });
  }, []);

  useEffect(() => {
    if (apiState === "offline") return;
    let active = true;
    setSubsystemAvailabilityLoaded(false);
    void workbenchApi.listSubsystemAvailability().then((items) => {
      if (active) {
        setSubsystemAvailability(items);
        setSubsystemAvailabilityLoaded(true);
      }
    }).catch(() => {
      if (active) {
        setSubsystemAvailability([]);
        setSubsystemAvailabilityLoaded(true);
      }
    });
    void workbenchApi.listChainTemplates().then((items) => {
      if (active) setChainTemplates(items);
    }).catch(() => {
      if (active) setChainTemplates([]);
    });
    return () => { active = false; };
  }, [apiState]);


  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand" aria-label="Material Decision Workbench">
          <span className="brand-full">Material Decision Workbench</span>
          <span className="brand-short" aria-hidden="true">MDW</span>
        </div>
        <nav aria-label="ホーム">
          <button
            type="button"
            className="nav-button"
            aria-label="プロジェクト"
            data-short-label="Project"
            aria-current={!workspaceLevelMode ? "page" : undefined}
            onClick={() => navigate({ view: "project", projectId: activeProjectId })}
          >
            <HomeNavIcon icon="project" />
            <span className="nav-label-full">プロジェクト</span>
          </button>
          <button
            type="button"
            className={dataLibraryMode ? "nav-button active" : "nav-button"}
            aria-label="データライブラリ"
            data-short-label="Data"
            aria-current={dataLibraryMode ? "page" : undefined}
            onClick={() => navigate({ view: "data-library" })}
          >
            <HomeNavIcon icon="data" />
            <span className="nav-label-full">データライブラリ</span>
          </button>
          <button
            ref={workspaceButtonRef}
            type="button"
            className={tab === "workspace" ? "nav-button active" : "nav-button"}
            aria-label="ワークスペース"
            data-short-label="保管"
            aria-current={tab === "workspace" ? "page" : undefined}
            onClick={() => navigate({ view: "workspace", adminSection: "developer" })}
          >
            <HomeNavIcon icon="workspace" />
            <span className="nav-label-full">ワークスペース</span>
          </button>
        </nav>
      </header>
      <main>
        {apiState === "starting" && session.apiStartedWaitingAt !== null
          && <StartupBanner startedAt={session.apiStartedWaitingAt} />}
        {apiState === "offline" && <ConnectionBanner diagnostic={session.startupDiagnostic} retrying={retrying} onRetry={() => {
          setRetrying(true);
          void session.retryOpenWorkspace().finally(() => setRetrying(false));
        }} />}
        {!workspaceLevelMode && <div className="context-bar">
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
                  {apiState === "loading"
                    ? "プレビュー更新中"
                    : apiState === "starting" ? "API 起動待ち" : "API 未接続"}
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
        {desktopWorkspaceNotice
          ? <WorkspaceNoticeBanner
            notice={desktopWorkspaceNotice}
            onDismiss={() => setDesktopWorkspaceNotice(null)}
          />
          : !dataLibraryMode && notice
            ? <WorkspaceNoticeBanner notice={notice} onDismiss={session.dismissNotice} />
            : null}
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
            subsystemAvailabilityLoaded={subsystemAvailabilityLoaded}
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
            onNavigate={(view, candidateId, options) => {
              navigate({
                view,
                projectId: activeProjectId,
                candidateId,
                activityId: options?.activityId,
                candidateSection: options?.candidateSection,
              }, true);
              if (candidateId) session.selectCandidate(candidateId, false);
            }}
            onSnapshotNavigate={(snapshotId) => navigate({ view: "project", projectId: activeProjectId, snapshotId }, true)}
            onRestore={(candidate) => {
              session.restoreCandidate(candidate);
            }}
            requestedSnapshotId={navigation.snapshotId}
            requestedDatasetViewId={requestedDatasetViewId}
            requestedSettingsSection={navigation.projectSettings}
            renderScientificSettings={(project, handleProjectChanged) => <ProjectScopedSettings
              project={project}
              taskDefinition={taskDefinition}
              resolvedTaskDefinition={resolvedTaskDefinition}
              readOnly={taskUnavailable}
              initialSection={navigation.projectSettings === "display" || navigation.projectSettings === "task"
                ? navigation.projectSettings
                : "ranges"}
              onSectionChange={(projectSettings) => navigate({
                view: "project",
                projectId: activeProjectId,
                projectSettings,
              }, true)}
              onProjectChanged={handleProjectChanged}
            />}
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
          <TaskUnavailablePanel
            message={taskAvailability?.message ?? "このタスクは現在利用できません。"}
            onOpenSettings={() => navigate({
              view: "project",
              projectId: activeProjectId,
              projectSettings: "task",
            })}
          />
        )}
        {chainScopedTab && (
          <ChainModeUnavailablePanel onOpenCandidates={() => navigate({
            view: "candidates",
            projectId: activeProjectId,
            candidateId: selectedId || undefined,
          })} stagePath={chainStagePath(activeChainRevision)} />
        )}
        {tab === "workspace" && (
          <WorkspaceAdminPage
            developerTab={navigation.developerTab}
            developerTabError={navigation.developerTabError}
            developerGuideId={navigation.developerGuideId}
            onDeveloperLocationChange={(developerTab, developerGuideId) => navigate({
              ...navigationRef.current,
              view: "workspace",
              adminSection: "developer",
              developerTab,
              developerGuideId,
            })}
            onOpenProfileWorkbench={() => navigate({ view: "profile-workbench" })}
            onOpenStorage={() => setWorkspaceDialogOpen(true)}
          />
        )}
        {tab === "candidates" && chainProject && (
          <ChainWorkbenchPage
            projectId={activeProjectId}
            initialCandidateId={navigation.candidateId}
            unavailable={activeChainAvailability?.status === "unavailable"
              ? activeChainAvailability
              : undefined}
            displayDecimalOverrides={activeProject?.display_decimals}
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
              activityId={navigation.activityId}
              activityRunId={navigation.activityRunId}
              candidateSection={navigation.candidateSection}
              onActivityStateChange={(activityId, activityRunId) => navigate({
                ...navigationRef.current,
                view: "candidates",
                projectId: activeProjectId,
                candidateId: selectedId || undefined,
                activityId,
                activityRunId,
                candidateSection: undefined,
              }, true)}
              onConfigureGoals={() => navigate({
                view: "project",
                projectId: activeProjectId,
                projectSettings: "targets",
              })}
              previewAvailable={operations?.preview === true}
              pendingPreviewCount={candidates.filter((item) => !item.raw.archived_at && !previewsByCandidate[item.id]).length}
              loadingRemainingPreviews={session.loadingRemainingPreviews}
              onLoadRemainingPreviews={() => void session.loadRemainingPreviews()}
              onConfigureSupport={() => navigate({
                view: "project",
                projectId: activeProjectId,
                projectSettings: "ranges",
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
            showReferenceScenarios
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
            onConfigureGoals={() => navigate({
              view: "project",
              projectId: activeProjectId,
              projectSettings: "targets",
            })}
            onCompare={() => navigate({ view: "candidates", projectId: activeProjectId }, true)}
            onCreateStarter={() => void session.createStarterCandidate()}
          />
        )}
        <WorkspaceManagerDialog
          open={workspaceDialogOpen}
          onClose={() => {
            setWorkspaceDialogOpen(false);
            window.requestAnimationFrame(() => workspaceButtonRef.current?.focus());
          }}
        />
      </main>
    </div>
  );
}

export default App;
