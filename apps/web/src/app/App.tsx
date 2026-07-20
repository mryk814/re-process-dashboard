import { useEffect, useRef, useState } from "react";
import { provenanceNavigation } from "./candidateProvenance";
import { navigationUrl, readNavigationIntent, withView, type NavigationIntent, type WorkbenchView } from "./navigation";
import { WorkbenchEmptyState, WorkbenchPage, useWorkbenchSession } from "../features/workbench";
import { ProjectHub } from "../features/projects";
import { ScreeningPage } from "../features/screening";
import { LineagePage } from "../features/lineage";
import { DataExploreNavigation, LiveDataQualityPage } from "../features/quality";
import { DeveloperAdminPage } from "../features/admin";

type Tab = WorkbenchView;
const primaryNavItems: Array<{ id: Tab; label: string; active: Tab[] }> = [
  { id: "project", label: "プロジェクト概要", active: ["project"] },
  { id: "lineage", label: "データ探索", active: ["lineage", "quality"] },
  { id: "explore", label: "範囲探索", active: ["explore"] },
  { id: "candidates", label: "候補比較", active: ["candidates"] },
];


function PlayIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m9 5 10 7-10 7V5Z" />
    </svg>
  );
}

function App() {
  const [navigation, setNavigation] = useState<NavigationIntent>(() => readNavigationIntent());
  const navigationRef = useRef(navigation);
  const tab = navigation.view;

  function navigate(intent: NavigationIntent, replace = false) {
    const next = Object.freeze(intent);
    navigationRef.current = next;
    setNavigation(next);
    window.history[replace ? "replaceState" : "pushState"]({}, "", navigationUrl(next));
  }

  const session = useWorkbenchSession({
    requestedProjectId: navigation.projectId,
    requestedCandidateId: navigation.candidateId,
    onLocationReplace: (projectId, candidateId) => {
      navigate({ ...navigationRef.current, projectId, candidateId }, true);
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
  } = session;
  const { error: previewError, metrics, preview, previewStatus, previewsByCandidate } = prediction;

  function selectCandidate(candidateId: string, replace = true) {
    session.selectCandidate(candidateId, false);
    navigate({ view: "candidates", projectId: activeProjectId, candidateId }, replace);
  }

  function rememberCandidate(candidateId: string) {
    session.selectCandidate(candidateId, false);
    navigate({ ...navigationRef.current, projectId: activeProjectId, candidateId }, true);
  }

  useEffect(() => {
    const onPopState = () => {
      const intent = readNavigationIntent();
      navigationRef.current = intent;
      setNavigation(intent);
      const targetProjectId = intent.projectId ?? activeProjectId;
      void session.openLocation(targetProjectId, intent.candidateId);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [activeProjectId, candidates]);


  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">Material Decision Workbench</div>
        <nav aria-label="画面">
          <span className="nav-group-label">予測・検討</span>
          {primaryNavItems.map((item) => (
            <button
              className={item.active.includes(tab) ? "nav-button active" : "nav-button"}
              onClick={() => {
                const destination = item.active.includes(tab) ? tab : item.id;
                const intent = withView(navigationRef.current, destination);
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
          <span className="nav-divider" aria-hidden="true" />
          <button
            className={tab === "settings" ? "nav-button admin active" : "nav-button admin"}
            onClick={() => navigate(withView(navigationRef.current, "settings"))}
          >
            開発・管理
          </button>
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
              <><button
                className="primary-button"
                disabled={!operations?.detailed_prediction || !["idle", "saved"].includes(editor.saveStates[selected.id] ?? "idle")}
                title={!operations?.detailed_prediction ? "このタスクでは詳細予測を利用できません" : undefined}
                onClick={() => {
                  void prediction.runDetailedPrediction();
                }}
              >
                <PlayIcon />
                {selected.label}の詳細予測を保存
              </button><button className="outline-button" onClick={() => navigate({ view: "project", projectId: activeProjectId, candidateId: selected.id })}>保存結果・履歴</button></>
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
              void session.refreshProjectDefinition(project);
            }}
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
          />
        )}
        {tab === "settings" && (
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
          />
        )}
        {tab === "candidates" &&
          (selected ? (
            <WorkbenchPage
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
              previewStatus={previewStatus}
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
              onHeat={session.updateHeat}
              onAddHeat={session.addHeatPoint}
              onDeleteHeat={session.deleteHeatPoint}
              onCopy={session.copyCandidate}
              onDelete={() => {
                void session.deleteCandidate();
              }}
              onAdd={() => {
                void session.addCandidate();
              }}
              onImported={(imported) => {
                if (imported.length) void session.loadProject(activeProjectId, selectedId || undefined);
              }}
            />
          ) : (
            <WorkbenchEmptyState
              loading={apiState === "loading"}
              error={loadError}
              onCreate={() => void session.createStarterCandidate()}
            />
          ))}
        {tab === "quality" && (
          <div className="data-explore-page">
            <DataExploreNavigation active="quality" onNavigate={(view) => navigate(withView(navigationRef.current, view))} />
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
          </div>
        )}
        {tab === "lineage" && (
          <div className="data-explore-page">
            <DataExploreNavigation active="lineage" onNavigate={(view) => navigate(withView(navigationRef.current, view))} />
            <LineagePage
            projectId={activeProjectId}
            supportsCandidateCreation={taskDefinition?.input_groups.some((group) => group.key === "heat_pattern") ?? false}
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
              session.setNotice(`${candidate.label} を候補ストックへ追加しました（${count}件）`);
            }}
            />
          </div>
        )}
        {tab === "explore" && (
          <ScreeningPage
            projectId={activeProjectId}
            candidates={candidates}
            selectedId={selectedId}
            taskDefinition={taskDefinition}
            resolvedTaskDefinition={resolvedTaskDefinition}
            initialRunId={navigation.screeningRunId}
            onRunChange={(screeningRunId) => navigate({ view: "explore", projectId: activeProjectId, screeningRunId }, true)}
            onCandidate={(candidate) => {
              const count = session.acceptCandidate(candidate);
              rememberCandidate(candidate.id);
              session.setNotice(`${candidate.label} を候補ストックへ追加しました（${count}件）`);
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
