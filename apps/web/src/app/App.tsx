import { useCallback, useEffect, useRef, useState } from "react";
import { provenanceNavigation } from "./candidateProvenance";
import { navigationLocationNeedsNormalization, navigationUrl, readNavigationIntent, withView, type NavigationIntent, type WorkbenchView } from "./navigation";
import { ChainWorkbenchPage, WorkbenchEmptyState, WorkbenchPage, apiStartupWaitText, useWorkbenchSession, type StartupDiagnostic } from "../features/workbench";
import {
  ChainGraphViewer,
  ChainStudioPage,
  chainStagePath,
  projectScientificSettingsReadOnly,
  ProjectHub,
  resolveActiveChainContext,
  resolveFixedChain,
} from "../features/projects";
import { ScreeningPage } from "../features/screening";
import { LineagePage } from "../features/lineage";
import { DataExploreNavigation, LiveDataQualityPage } from "../features/quality";
import { ProjectScopedSettings, WorkspaceAdminPage } from "../features/admin";
import { DataLibraryPage, ProfileWorkbenchPage, type PreparedCsvProjectBinding } from "../features/data-library";
import { WorkspaceManagerDialog } from "../features/workspace";
import { WorkspaceNoticeBanner } from "../shared/ui/WorkspaceNoticeBanner";
import type { WorkspaceNotice } from "../shared/workspaceNotice";
import {
  workbenchApi,
  type ApiChainTemplate,
  type ApiSubsystemAvailability,
} from "../shared/api/workbench-api";

type Tab = WorkbenchView;
type NavigationGuard = () => Promise<boolean>;
type HomeNavigationIcon = "project" | "data" | "workspace" | "chain";
const lastNavigationStorageKey = "material-workbench-last-navigation";
const navigationHistoryIndexKey = "workbenchNavigationIndex";
const projectNavItems: Array<{ id: Tab; label: string; active: Tab[]; requiresDataExplorer?: boolean }> = [
  { id: "project", label: "概要", active: ["project"] },
  { id: "lineage", label: "データ探索", active: ["lineage", "quality"], requiresDataExplorer: true },
  { id: "candidates", label: "候補比較", active: ["candidates"] },
  { id: "chain-graph", label: "Chain構成", active: ["chain-graph"] },
  { id: "explore", label: "範囲探索", active: ["explore"] },
  { id: "candidate-review", label: "候補確認", active: ["candidate-review"] },
  { id: "project-settings", label: "設定", active: ["project-settings"] },
];

function HomeNavIcon({ icon }: { icon: HomeNavigationIcon }) {
  if (icon === "project") {
    return <svg className="home-nav-icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d="M3 4.5h5l1.4 1.7H17v9.3H3z" />
    </svg>;
  }
  if (icon === "chain") {
    return <svg className="home-nav-icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <circle cx="4" cy="10" r="2.2" /><circle cx="16" cy="5" r="2.2" /><circle cx="16" cy="15" r="2.2" /><path d="M6.1 9.1 13.8 5.9M6.1 10.9l7.7 3.2" />
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

function PredictionGraphModeUnavailablePanel({ onOpenOverview }: { onOpenOverview: () => void }) {
  return <div className="page-panel task-unavailable-panel" role="status">
    <span className="overline">PREDICTION GRAPH PROJECT</span>
    <h2>この画面はPrediction Graph Projectでは利用できません</h2>
    <p>このProjectは複数のTask／TransformとDecision Outputを一つの公開Revisionへ固定しています。単一Task向けの候補比較・範囲探索・データ探索は呼び出しません。</p>
    <button type="button" className="primary-button" onClick={onOpenOverview}>Project概要へ戻る</button>
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
        <span className="connection-banner-steps">起動ログ（Desktop版は診断ログを開く、開発時は <code>npm run dev</code> のapi出力）を確認してください。</span>
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
  const [requestedDatasetViewId, setRequestedDatasetViewId] = useState<string | undefined>(
    () => navigation.preparedProjectBinding?.datasetViewId,
  );
  const [requestedProjectBinding, setRequestedProjectBinding] = useState<Omit<PreparedCsvProjectBinding, "datasetViewId"> | undefined>(
    () => {
      const binding = navigation.preparedProjectBinding;
      if (!binding) return undefined;
      const { datasetViewId: _datasetViewId, ...rest } = binding;
      return rest;
    },
  );
  const [retrying, setRetrying] = useState(false);
  const [subsystemAvailability, setSubsystemAvailability] = useState<ApiSubsystemAvailability[]>([]);
  const [subsystemAvailabilityLoaded, setSubsystemAvailabilityLoaded] = useState(false);
  const [subsystemAvailabilityError, setSubsystemAvailabilityError] = useState(false);
  const [chainTemplates, setChainTemplates] = useState<ApiChainTemplate[]>([]);
  const [chainTemplatesLoaded, setChainTemplatesLoaded] = useState(false);
  const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false);
  const workspaceDialogReturnFocusRef = useRef<HTMLElement | null>(null);
  const [desktopWorkspaceNotice, setDesktopWorkspaceNotice] = useState<WorkspaceNotice | null>(null);
  const navigationRef = useRef(navigation);
  const navigationGuardsRef = useRef(new Set<NavigationGuard>());
  const navigationRequestSequence = useRef(0);
  const navigationHistoryIndex = useRef(0);
  const restoringHistory = useRef(false);
  const workspaceButtonRef = useRef<HTMLButtonElement>(null);
  const tab = navigation.view;

  const commitNavigation = useCallback((intent: NavigationIntent, replace = false) => {
    const next = Object.freeze(intent);
    navigationRef.current = next;
    setNavigation(next);
    rememberNavigation(next);
    const target = navigationUrl(next);
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (target !== current) {
      const nextIndex = replace ? navigationHistoryIndex.current : navigationHistoryIndex.current + 1;
      window.history[replace ? "replaceState" : "pushState"](
        { ...window.history.state, [navigationHistoryIndexKey]: nextIndex },
        "",
        target,
      );
      navigationHistoryIndex.current = nextIndex;
    }
  }, []);

  const navigate = useCallback((intent: NavigationIntent, replace = false) => {
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (navigationUrl(intent) === current) {
      commitNavigation(intent, true);
      return;
    }
    const sequence = ++navigationRequestSequence.current;
    const guards = [...navigationGuardsRef.current];
    if (!guards.length) {
      commitNavigation(intent, replace);
      return;
    }
    void Promise.all(guards.map((guard) => guard())).then((results) => {
      if (results.every(Boolean) && sequence === navigationRequestSequence.current) {
        commitNavigation(intent, replace);
      }
    });
  }, [commitNavigation]);

  const registerNavigationGuard = useCallback((guard: NavigationGuard) => {
    navigationGuardsRef.current.add(guard);
    return () => {
      navigationGuardsRef.current.delete(guard);
    };
  }, []);

  const navigateDataLibrary = useCallback((location: {
    tab: "browse" | "update";
    connectorId?: string;
    stage?: "raw" | "curation" | "approval" | "training";
    revisionId?: string;
    onboardingMode?: "revision" | "mapping" | "new-task";
  }, replace = false) => navigate({
    view: "data-library",
    dataLibraryTab: location.tab,
    sourceConnectorId: location.connectorId,
    sourceStage: location.stage,
    sourceRevisionId: location.revisionId,
    dataOnboardingMode: location.onboardingMode,
  }, replace), [navigate]);

  function openWorkspaceStorage() {
    workspaceDialogReturnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    setWorkspaceDialogOpen(true);
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
      const currentView = navigationRef.current.view;
      navigate({
        view: currentView === "candidate-review" || currentView === "explore"
          ? currentView
          : "candidates",
        projectId,
        candidateId,
      }, true);
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
  const candidateSettlementRef = useRef(editor.settlePending);
  candidateSettlementRef.current = editor.settlePending;
  useEffect(() => registerNavigationGuard(
    () => candidateSettlementRef.current(),
  ), [registerNavigationGuard]);
  const chainProject = activeProject?.scientific_identity?.identity_kind === "chain";
  const predictionGraphProject = activeProject?.scientific_identity?.identity_kind === "prediction_graph";
  const chainIdentity = activeProject?.scientific_identity?.identity_kind === "chain"
    ? activeProject.scientific_identity
    : null;
  const activeChainContext = resolveActiveChainContext({
    identity: chainIdentity,
    templates: chainTemplates,
    templatesLoaded: chainTemplatesLoaded,
    availability: subsystemAvailability,
    availabilityLoaded: subsystemAvailabilityLoaded,
    availabilityError: subsystemAvailabilityError,
    offline: apiState === "offline",
  });
  const activeChainRevision = "revision" in activeChainContext
    ? activeChainContext.revision
    : resolveFixedChain(chainIdentity, chainTemplates).revision;
  const taskUnavailable = taskAvailability?.status === "unavailable";
  const unavailableScopedTab = taskUnavailable
    && !chainProject
    && tab !== "project"
    && tab !== "project-settings"
    && tab !== "workspace"
    && tab !== "data-library"
    && tab !== "profile-workbench";
  // Chain projects have no single-task contract, so these views cannot answer
  // anything. They stay reachable by deep link, and must explain themselves
  // instead of failing inside a single-task surface.
  const chainScopedTab = chainProject
    && (tab === "explore" || tab === "lineage" || tab === "quality" || tab === "candidate-review");
  const predictionGraphScopedTab = predictionGraphProject
    && (tab === "candidates" || tab === "candidate-review" || tab === "explore"
      || tab === "lineage" || tab === "quality" || tab === "chain-graph");
  const dataExplorer = taskUnavailable || predictionGraphProject ? null : resolvedTaskDefinition?.data_explorer;
  const qualityAvailable = dataExplorer?.quality === true;
  const lineageAvailable = dataExplorer?.lineage === true;
  const visibleProjectNavItems = projectNavItems.filter((item) => (
    (predictionGraphProject && (item.id === "project" || item.id === "project-settings"))
      || (!chainProject && !predictionGraphProject && (!taskUnavailable || item.id === "project" || item.id === "project-settings"))
      || (chainProject && (item.id === "project" || item.id === "candidates" || item.id === "chain-graph" || item.id === "project-settings"))
  ) && (!item.requiresDataExplorer || qualityAvailable || lineageAvailable));
  const workspaceLevelMode = tab === "data-library" || tab === "profile-workbench" || tab === "workspace" || tab === "chain-studio";
  const dataLibraryMode = tab === "data-library" || tab === "profile-workbench";

  function selectCandidate(candidateId: string, replace = true) {
    session.selectCandidate(candidateId, false);
    navigate({
      view: tab === "candidate-review"
        ? "candidate-review"
        : tab === "explore"
          ? "explore"
          : "candidates",
      projectId: activeProjectId,
      candidateId,
    }, replace);
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
      candidateId: item.id === "candidates" || item.id === "candidate-review"
        ? selectedId || undefined
        : intent.candidateId,
    });
  }

  function startProjectForDataset(datasetViewRevisionId: string, binding?: Omit<PreparedCsvProjectBinding, "datasetViewId">) {
    setRequestedDatasetViewId(datasetViewRevisionId);
    setRequestedProjectBinding(binding);
    navigate({
      view: "project",
      projectId: activeProjectId,
      preparedProjectBinding: binding
        ? { datasetViewId: datasetViewRevisionId, ...binding }
        : undefined,
    });
  }

  useEffect(() => {
    const onPopState = (event: PopStateEvent) => {
      if (restoringHistory.current) {
        restoringHistory.current = false;
        navigationRequestSequence.current += 1;
        return;
      }
      const intent = readNavigationIntent();
      const previous = navigationRef.current;
      const previousIndex = navigationHistoryIndex.current;
      const stateIndex = Reflect.get(event.state ?? {}, navigationHistoryIndexKey);
      const targetIndex = typeof stateIndex === "number" ? stateIndex : null;
      const sequence = ++navigationRequestSequence.current;
      const applyIntent = () => {
        if (navigationLocationNeedsNormalization(intent)) {
          window.history.replaceState(
            { ...window.history.state, [navigationHistoryIndexKey]: targetIndex ?? previousIndex },
            "",
            navigationUrl(intent),
          );
        }
        if (targetIndex !== null) navigationHistoryIndex.current = targetIndex;
        navigationRef.current = intent;
        setNavigation(intent);
        setRequestedDatasetViewId(intent.preparedProjectBinding?.datasetViewId);
        if (intent.preparedProjectBinding) {
          const { datasetViewId: _datasetViewId, ...binding } = intent.preparedProjectBinding;
          setRequestedProjectBinding(binding);
        } else {
          setRequestedProjectBinding(undefined);
        }
        rememberNavigation(intent);
        const targetProjectId = intent.projectId ?? activeProjectId;
        void session.openLocation(targetProjectId, intent.candidateId);
      };
      const guards = [...navigationGuardsRef.current];
      if (!guards.length) {
        applyIntent();
        return;
      }
      void Promise.all(guards.map((guard) => guard())).then((results) => {
        if (sequence !== navigationRequestSequence.current) return;
        if (results.every(Boolean)) {
          applyIntent();
          return;
        }
        if (targetIndex !== null && targetIndex !== previousIndex) {
          restoringHistory.current = true;
          window.history.go(previousIndex - targetIndex);
        } else {
          window.history.pushState(
            { ...window.history.state, [navigationHistoryIndexKey]: previousIndex },
            "",
            navigationUrl(previous),
          );
        }
        rememberNavigation(previous);
      });
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [activeProjectId, candidates]);

  useEffect(() => {
    const current = navigationRef.current;
    rememberNavigation(current);
    const params = new URLSearchParams(window.location.search);
    const target = !params.has("view") || navigationLocationNeedsNormalization(current)
      ? navigationUrl(current)
      : `${window.location.pathname}${window.location.search}${window.location.hash}`;
    window.history.replaceState(
      { ...window.history.state, [navigationHistoryIndexKey]: navigationHistoryIndex.current },
      "",
      target,
    );
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
    setSubsystemAvailabilityError(false);
    setChainTemplatesLoaded(false);
    void workbenchApi.listSubsystemAvailability().then((items) => {
      if (active) {
        setSubsystemAvailability(items);
        setSubsystemAvailabilityError(false);
        setSubsystemAvailabilityLoaded(true);
      }
    }).catch(() => {
      if (active) {
        setSubsystemAvailability([]);
        setSubsystemAvailabilityError(true);
        setSubsystemAvailabilityLoaded(true);
      }
    });
    void workbenchApi.listChainTemplates().then((items) => {
      if (active) {
        setChainTemplates(items);
        setChainTemplatesLoaded(true);
      }
    }).catch(() => {
      if (active) {
        setChainTemplates([]);
        setChainTemplatesLoaded(true);
      }
    });
    return () => { active = false; };
  }, [apiState]);


  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand" aria-label="Evidence Decision Workbench">
          <span className="brand-full">Evidence Decision Workbench</span>
          <span className="brand-short" aria-hidden="true">EDW</span>
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
            type="button"
            className={tab === "chain-studio" ? "nav-button active" : "nav-button"}
            aria-label="Chain Studio"
            data-short-label="Chain"
            aria-current={tab === "chain-studio" ? "page" : undefined}
            onClick={() => navigate({ view: "chain-studio" })}
          >
            <HomeNavIcon icon="chain" />
            <span className="nav-label-full">Chain Studio</span>
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
              {tab !== "candidates" && !taskUnavailable && !chainProject && !predictionGraphProject && (
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
                aria-current={item.active.includes(tab) ? "page" : undefined}
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
        {(tab === "project" || tab === "project-settings") && (
          <ProjectHub
            surface={tab === "project-settings" ? "settings" : "overview"}
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
            subsystemAvailabilityError={subsystemAvailabilityError}
            offline={apiState === "offline"}
            onProjectChanged={(project) => {
              void session.refreshProjectDefinition(project);
            }}
            onProjectArchived={(projectId) => session.archiveProject(projectId)}
            onProjectRestored={(projectId) => session.restoreProject(projectId)}
            onSampleGalleryInstall={(projectIds) => session.installSampleProjects(projectIds)}
            onSampleGalleryRemove={(projectId) => session.removeSampleProject(projectId)}
            onSwitch={(projectId) => {
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
            requestedProjectBinding={requestedProjectBinding}
            requestedSettingsSection={navigation.projectSettings}
            onOpenSettings={(projectSettings = "general", replace = false) => navigate({
              view: "project-settings",
              projectId: activeProjectId,
              projectSettings,
            }, replace)}
            renderScientificSettings={(project, handleProjectChanged, readOnly) => <ProjectScopedSettings
              project={project}
              taskDefinition={taskDefinition}
              resolvedTaskDefinition={resolvedTaskDefinition}
              availability={taskAvailability}
              readOnly={projectScientificSettingsReadOnly(taskUnavailable, readOnly)}
              initialSection={navigation.projectSettings === "display" || navigation.projectSettings === "task"
                ? navigation.projectSettings
                : "ranges"}
              onSectionChange={(projectSettings) => navigate({
                view: "project-settings",
                projectId: activeProjectId,
                projectSettings,
              }, true)}
              onProjectChanged={handleProjectChanged}
            />}
            onCreationIntentConsumed={() => { setRequestedDatasetViewId(undefined); setRequestedProjectBinding(undefined); }}
          />
        )}
        {tab === "data-library" && <DataLibraryPage
          projects={projects}
          location={{
            tab: navigation.dataLibraryTab ?? "browse",
            connectorId: navigation.sourceConnectorId,
            stage: navigation.sourceStage,
            revisionId: navigation.sourceRevisionId,
            onboardingMode: navigation.dataOnboardingMode,
          }}
          onNavigate={navigateDataLibrary}
          onAddDataset={(mode, baseDatasetRevisionId) => navigate({
            view: "profile-workbench",
            dataOnboardingMode: mode,
            baseDatasetRevisionId,
          })}
          onStartProject={startProjectForDataset}
          onOpenStorage={openWorkspaceStorage}
          onOpenTrainingData={(projectId) => navigate({
            view: "workspace",
            projectId,
            adminSection: "developer",
            developerTab: "training",
          })}
        />}
        {tab === "profile-workbench" && <ProfileWorkbenchPage
          onOpenDataLibrary={() => navigate({ view: "data-library" })}
          onStartProject={startProjectForDataset}
        />}
        {tab === "chain-studio" && <ChainStudioPage onProjectCreated={(project) => {
          void session.loadCreatedProject(project).then((loaded) => {
            if (loaded) navigate({ view: "project", projectId: project.id });
          });
        }} />}
        {unavailableScopedTab && (
          <TaskUnavailablePanel
            message={taskAvailability?.message ?? "このタスクは現在利用できません。"}
            onOpenSettings={() => navigate({
              view: "project-settings",
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
        {predictionGraphScopedTab && (
          <PredictionGraphModeUnavailablePanel onOpenOverview={() => navigate({
            view: "project",
            projectId: activeProjectId,
          })} />
        )}
        {tab === "chain-graph" && chainProject && (
          <ChainGraphViewer
            projectId={activeProjectId}
            candidateId={selectedId || navigation.candidateId}
          />
        )}
        {tab === "workspace" && (
          <WorkspaceAdminPage
            developerTab={navigation.developerTab}
            developerTabError={navigation.developerTabError}
            developerGuideId={navigation.developerGuideId}
            projectId={activeProjectId}
            taskDefinition={taskDefinition}
            onDeveloperLocationChange={(developerTab, developerGuideId) => navigate({
              ...navigationRef.current,
              view: "workspace",
              adminSection: "developer",
              developerTab,
              developerGuideId,
            })}
            onOpenProfileWorkbench={() => navigate({ view: "profile-workbench" })}
            onOpenStorage={openWorkspaceStorage}
          />
        )}
        {tab === "candidates" && chainProject
          && (activeChainContext.status === "loading"
            || activeChainContext.status === "unresolved") && (
          <WorkbenchEmptyState
            loading={activeChainContext.status === "loading"}
            error={activeChainContext.status === "unresolved"
              ? `固定したChain Revisionを解決できません（${activeChainContext.chainRevisionId}）。`
              : null}
            onCreate={() => undefined}
          />
        )}
        {tab === "candidates" && chainProject
          && (activeChainContext.status === "offline"
            || activeChainContext.status === "error") && (
          <WorkbenchEmptyState
            loading={false}
            error={activeChainContext.status === "offline"
              ? "APIへ接続できないため、固定したChain Revisionの利用状況を確認できません。"
              : "Chainの利用状況を取得できませんでした。再読み込みしてから操作してください。"}
            errorHint={activeChainContext.status === "error" ? null : undefined}
            onCreate={() => undefined}
          />
        )}
        {tab === "candidates" && chainProject
          && (activeChainContext.status === "available"
            || activeChainContext.status === "unavailable") && (
          <ChainWorkbenchPage
            projectId={activeProjectId}
            initialCandidateId={navigation.candidateId}
            unavailable={activeChainContext.status === "unavailable"
              ? activeChainContext.availability
              : undefined}
            displayDecimalOverrides={activeProject?.display_decimals}
            registerNavigationGuard={registerNavigationGuard}
            onCandidateSelected={(candidateId) => navigate({
              view: "candidates",
              projectId: activeProjectId,
              candidateId,
            }, true)}
          />
        )}
        {(tab === "candidates" || tab === "candidate-review" || tab === "explore") && !chainProject && !predictionGraphProject && !taskUnavailable &&
          (selected ? (
            <WorkbenchPage
              mode={tab === "candidate-review" ? "review" : tab === "explore" ? "explore" : "comparison"}
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
                view: "candidate-review",
                projectId: activeProjectId,
                candidateId: selectedId || undefined,
                activityId,
                activityRunId,
                candidateSection: undefined,
              }, true)}
              onConfigureGoals={() => navigate({
                view: "project-settings",
                projectId: activeProjectId,
                projectSettings: "targets",
              })}
              previewAvailable={operations?.preview === true}
              pendingPreviewCount={candidates.filter((item) => !item.raw.archived_at && !previewsByCandidate[item.id]).length}
              loadingRemainingPreviews={session.loadingRemainingPreviews}
              onLoadRemainingPreviews={() => void session.loadRemainingPreviews()}
              onConfigureSupport={() => navigate({
                view: "project-settings",
                projectId: activeProjectId,
                projectSettings: "ranges",
              })}
            >
              {tab === "explore" && <ScreeningPage
                projectId={activeProjectId}
                project={activeProject}
                candidates={candidates}
                selectedId={selectedId}
                taskDefinition={taskDefinition}
                resolvedTaskDefinition={resolvedTaskDefinition}
                initialRunId={navigation.screeningRunId}
                onRunChange={(screeningRunId) => navigate({
                  ...navigationRef.current,
                  view: "explore",
                  projectId: activeProjectId,
                  screeningRunId,
                }, true)}
                onSelectCandidate={(candidateId) => selectCandidate(candidateId)}
                onCandidate={(candidate) => {
                  const count = session.acceptCandidate(candidate);
                  rememberCandidate(candidate.id);
                  session.notifySuccess(`${candidate.label} を候補ストックへ追加しました（${count}件）`);
                }}
                onConfigureGoals={() => navigate({
                  view: "project-settings",
                  projectId: activeProjectId,
                  projectSettings: "targets",
                })}
                onCompare={() => navigate({ view: "candidates", projectId: activeProjectId }, true)}
              />}
            </WorkbenchPage>
          ) : (
            <WorkbenchEmptyState
              loading={apiState === "loading"}
              error={loadError}
              onCreate={() => void session.createStarterCandidate()}
            />
          ))}
        {tab === "quality" && !taskUnavailable && !chainProject && !predictionGraphProject && (qualityAvailable ? (
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
        {tab === "lineage" && !taskUnavailable && !chainProject && !predictionGraphProject && (lineageAvailable ? (
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
        <WorkspaceManagerDialog
          open={workspaceDialogOpen}
          onClose={() => {
            setWorkspaceDialogOpen(false);
            window.requestAnimationFrame(() => {
              const target = workspaceDialogReturnFocusRef.current;
              workspaceDialogReturnFocusRef.current = null;
              if (target?.isConnected) target.focus();
              else workspaceButtonRef.current?.focus();
            });
          }}
        />
      </main>
    </div>
  );
}

export default App;
