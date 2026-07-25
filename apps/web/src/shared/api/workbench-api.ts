import type { components } from "../../generated/api-types";
import { apiClient, apiDownloadUrl, requireData, requireSuccess } from "./client";
import { candidateInferencePrefix, inferenceRequestCache, inferenceRequestKey } from "./inferenceRequestCache";

export type ApiCandidate = components["schemas"]["Candidate"];
export type ApiCandidateInput = components["schemas"]["CandidateInput"];
export type ApiCandidateUpdate = components["schemas"]["CandidateUpdate"];
export type ApiProject = components["schemas"]["Project"];
export type ApiProjectInput = components["schemas"]["ProjectUpdateInput"];
export type ApiProjectCreateInput = components["schemas"]["ProjectCreateInput"];
export type ApiModelPackage = components["schemas"]["ModelPackageStatus"];
export type ApiModelTrainingDataPage = components["schemas"]["ModelTrainingDataPage"];
export type ApiPreview = components["schemas"]["PredictionResponse"];
export type ApiSnapshot = components["schemas"]["SnapshotResponse"];
export type ApiActualMeasurementInput = components["schemas"]["ActualMeasurementInput"];
export type ApiPredictionVsActual = components["schemas"]["PredictionVsActualResponse"];
export type ApiResponseCurve = components["schemas"]["ResponseCurveResponse"];
export type ApiCurveFamily = components["schemas"]["CurveFamilyResponse"];
export type ApiInferenceDiagnostics = components["schemas"]["InferenceDiagnosticsResponse"];
export type ApiSimilarObservation = components["schemas"]["SimilarObservation"];
export type ApiQuality = components["schemas"]["QualityResponse"];
export type ApiLineage = components["schemas"]["LineageResponse"];
export type ApiLineageIndex = components["schemas"]["LineageIndexResponse"];
export type ApiLineageNodeReview = components["schemas"]["LineageNodeReview"];
export type ApiLineageNodeReviewInput = components["schemas"]["LineageNodeReviewInput"];
export type ApiLineageNodeReviewList = components["schemas"]["LineageNodeReviewList"];
export type ApiScreeningRequest = components["schemas"]["ScreeningRequest"];
export type ApiScreeningRun = components["schemas"]["ScreeningRunResponse"];
export type ApiScreeningCandidateBatch = components["schemas"]["ScreeningCandidateBatchResponse"];
export type ApiTaskDefinition = components["schemas"]["ResolvedTaskDefinition"];
export type ApiTaskCatalogItem = components["schemas"]["TaskCatalogItem"];
export type ApiProjectHistory = components["schemas"]["ProjectHistoryResponse"];
export type ApiProjectDecisionInput = components["schemas"]["ProjectDecisionInput"];
export type ApiProjectGroupMoveInput = components["schemas"]["ProjectGroupMoveInput"];
export type ApiProjectCreationOptions = components["schemas"]["ProjectCreationOptions"];
export type ApiChainTemplate = components["schemas"]["ChainTemplateItem"];
export type ApiDataLibraryDataset = components["schemas"]["DataLibraryDataset"];
export type ApiDatasetView = components["schemas"]["DatasetViewRevision"];
export type ApiDatasetViewCreateInput = components["schemas"]["DatasetViewRevisionCreateInput"];
export type ApiModelPackageRef = components["schemas"]["ModelPackageRef"];
export type ApiProjectSeries = components["schemas"]["ProjectSeries"];
export type ApiProfileWorkbenchInspection = components["schemas"]["ProfileWorkbenchInspection"];
export type ApiProfileWorkbenchProfile = components["schemas"]["ProfileWorkbenchProfileOption"];
export type ApiProfileWorkbenchRegistration = components["schemas"]["ProfileWorkbenchRegistration"];
export type ApiDeveloperOverview = components["schemas"]["DeveloperOverview"];
export type ApiRuntimeDiagnostics = components["schemas"]["RuntimeDiagnosticsReport"];
export type ApiDeveloperCommand = components["schemas"]["DeveloperCommand"];
export type ApiChangeGuideEntry = components["schemas"]["ChangeGuideEntry"];
export type ApiObservationTrainingProfile = components["schemas"]["ObservationTrainingProfileSummary"];
export type ApiObservationTrainingPage = components["schemas"]["ObservationTrainingInspectionPage"];
export type ApiBlendMaterial = components["schemas"]["BlendMaterialDescriptor"];
export type ApiDecisionActivityAvailability = components["schemas"]["DecisionActivityAvailability"];
export type ApiDecisionActivityRun = components["schemas"]["DecisionActivityRun"];
export type ApiDecisionActivityRunRequest = components["schemas"]["DecisionActivityRunRequest"];

const path = (projectId: string, suffix = "") =>
  `/api/projects/${encodeURIComponent(projectId)}${suffix}`;

export const workbenchApi = {
  async developerOverview() {
    return requireData(await apiClient.GET("/api/developer/overview"), "Developer構成を取得できませんでした。");
  },
  async developerDiagnostics() {
    return requireData(await apiClient.GET("/api/developer/diagnostics"), "Developer診断を実行できませんでした。");
  },
  async developerChangeGuide() {
    return requireData(await apiClient.GET("/api/developer/change-guide"), "変更判断ガイドを取得できませんでした。");
  },
  async developerObservationTrainingProfiles() {
    return requireData(await apiClient.GET("/api/developer/observation-training-profiles"), "観測Profileを取得できませんでした。");
  },
  async developerObservationTrainingData(profileId: string, family: string, target: string, offset = 0, limit = 25, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/developer/observation-training-data", {
      params: { query: { profile_id: profileId, family, target, offset, limit } },
      signal,
    }), "観測学習データを取得できませんでした。");
  },
  async listProjects() {
    return requireData(await apiClient.GET("/api/projects"), "プロジェクトを取得できませんでした。");
  },
  async listTaskDefinitions() {
    return requireData(await apiClient.GET("/api/task-definitions"), "予測タスクを取得できませんでした。");
  },
  async projectCreationOptions() {
    return requireData(await apiClient.GET("/api/project-creation-options"), "プロジェクト作成条件を取得できませんでした。");
  },
  async listChainTemplates() {
    return requireData(await apiClient.GET("/api/chains"), "Chain Templateを取得できませんでした。");
  },
  async listDataLibraryDatasets(includeArchived = false) {
    return requireData(await apiClient.GET("/api/data-library/datasets", {
      params: { query: { include_archived: includeArchived } },
    }), "データライブラリを取得できませんでした。");
  },
  async setDatasetArchived(revisionId: string, archived: boolean) {
    return requireData(await apiClient.PATCH("/api/data-library/datasets/{revision_id}", {
      params: { path: { revision_id: revisionId } },
      body: { archived },
    }), archived ? "Datasetを利用停止できませんでした。" : "Datasetを復元できませんでした。");
  },
  async listModelPackageRefs(includeArchived = false) {
    return requireData(await apiClient.GET("/api/data-library/model-packages", {
      params: { query: { include_archived: includeArchived } },
    }), "Model Package一覧を取得できませんでした。");
  },
  async setModelPackageArchived(referenceId: string, archived: boolean) {
    return requireData(await apiClient.PATCH("/api/data-library/model-packages/{reference_id}", {
      params: { path: { reference_id: referenceId } },
      body: { archived },
    }), archived ? "Model Packageを利用停止できませんでした。" : "Model Packageを復元できませんでした。");
  },
  async createDatasetView(body: ApiDatasetViewCreateInput) {
    return requireData(await apiClient.POST("/api/data-library/views", { body }), "比較セットを作成できませんでした。");
  },
  async createProjectSeries(name: string, description = "") {
    return requireData(await apiClient.POST("/api/project-series", { body: { name, description } }), "検討グループを作成できませんでした。");
  },
  async updateProjectSeries(seriesId: string, name: string, description = "") {
    return requireData(await apiClient.PUT("/api/project-series/{series_id}", {
      params: { path: { series_id: seriesId } },
      body: { name, description, archived: false },
    }), "検討グループを保存できませんでした。");
  },
  async listProfileWorkbenchProfiles() {
    return requireData(await apiClient.GET("/api/profile-workbench/profiles"), "Dataset Profileを取得できませんでした。");
  },
  async inspectProfileWorkbook(file: File, profileDigest?: string, signal?: AbortSignal) {
    const form = new FormData();
    form.append("file", file);
    if (profileDigest) form.append("profile_digest", profileDigest);
    return requireData(await apiClient.POST("/api/profile-workbench/inspect", {
      body: { file: file.name, profile_digest: profileDigest },
      bodySerializer: () => form,
      signal,
    }), "Excelの内容を確認できませんでした。");
  },
  async registerProfileWorkbook(file: File, profileDigest: string, expectedSourceSha256: string, name: string) {
    const form = new FormData();
    form.append("file", file);
    form.append("profile_digest", profileDigest);
    form.append("expected_source_sha256", expectedSourceSha256);
    if (name.trim()) form.append("name", name.trim());
    return requireData(await apiClient.POST("/api/profile-workbench/register", {
      body: {
        file: file.name,
        profile_digest: profileDigest,
        expected_source_sha256: expectedSourceSha256,
        name: name.trim() || undefined,
      },
      bodySerializer: () => form,
    }), "Datasetを登録できませんでした。");
  },
  async projectHistory(projectId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/history", { params: { path: { project_id: projectId } }, signal }), "プロジェクト履歴を取得できませんでした。");
  },
  async createProject(body: ApiProjectCreateInput) {
    return requireData(await apiClient.POST("/api/projects", { body }), "プロジェクトを作成できませんでした。");
  },
  async updateProject(projectId: string, body: ApiProjectInput, options?: { invalidateInference?: boolean }) {
    const project = requireData(await apiClient.PUT("/api/projects/{project_id}", { params: { path: { project_id: projectId } }, body }), "プロジェクトを保存できませんでした。");
    if (options?.invalidateInference !== false) inferenceRequestCache.invalidatePrefix(candidateInferencePrefix(projectId));
    return project;
  },
  async moveProjectToGroup(projectId: string, body: ApiProjectGroupMoveInput) {
    return requireData(await apiClient.PUT("/api/projects/{project_id}/group", {
      params: { path: { project_id: projectId } },
      body,
    }), "所属グループを変更できませんでした。");
  },
  async deleteProject(projectId: string) {
    requireSuccess(await apiClient.DELETE("/api/projects/{project_id}", { params: { path: { project_id: projectId } } }), "プロジェクトを削除できませんでした。");
    inferenceRequestCache.invalidatePrefix(candidateInferencePrefix(projectId));
  },
  async updateProjectDecision(projectId: string, body: ApiProjectDecisionInput) {
    return requireData(await apiClient.PUT("/api/projects/{project_id}/decision", { params: { path: { project_id: projectId } }, body }), "採用判断を保存できませんでした。");
  },
  async taskDefinition(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/task-definition", { params: { path: { project_id: projectId } } }), "タスク定義を取得できませんでした。");
  },
  async modelPackage(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/model-package", { params: { path: { project_id: projectId } } }), "モデルPackageを取得できませんでした。");
  },
  async modelTrainingData(projectId: string, stage: "curation" | "selected" | "features", target: string, offset = 0, limit = 25, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/model-package/training-data", {
      params: { path: { project_id: projectId }, query: { stage, target, offset, limit } },
      signal,
    }), "学習データを取得できませんでした。");
  },
  async listCandidates(projectId: string, includeArchived = false) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates", { params: { path: { project_id: projectId }, query: { include_archived: includeArchived } } }), "候補を取得できませんでした。");
  },
  async createCandidate(projectId: string, body: ApiCandidateInput) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates", { params: { path: { project_id: projectId } }, body }), "候補を作成できませんでした。");
  },
  async updateCandidate(projectId: string, candidateId: string, body: ApiCandidateUpdate) {
    return requireData(await apiClient.PUT("/api/projects/{project_id}/candidates/{candidate_id}", { params: { path: { project_id: projectId, candidate_id: candidateId } }, body }), "候補を保存できませんでした。");
  },
  async deleteCandidate(projectId: string, candidateId: string, expectedRevision: number) {
    requireSuccess(await apiClient.DELETE("/api/projects/{project_id}/candidates/{candidate_id}", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { expected_revision: expectedRevision } } }), "候補を一覧から外せませんでした。");
    inferenceRequestCache.invalidatePrefix(candidateInferencePrefix(projectId, candidateId));
  },
  async previewCandidate(projectId: string, candidateId: string, expectedRevision: number, inputIdentity: string, signal?: AbortSignal) {
    return inferenceRequestCache.get(
      inferenceRequestKey(projectId, candidateId, inputIdentity, "preview"),
      async (sharedSignal) => requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/preview", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { expected_revision: expectedRevision } }, signal: sharedSignal }), "プレビューを取得できませんでした。"),
      signal,
    );
  },
  async predictCandidate(projectId: string, candidateId: string, expectedRevision: number) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/predict", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { expected_revision: expectedRevision } } }), "詳細予測を取得できませんでした。");
  },
  async responseCurve(projectId: string, candidateId: string, expectedRevision: number, inputIdentity: string, target: string, variable: string, points = 9, rangeMin?: number, rangeMax?: number, stageName?: string, stagePositionM?: number, signal?: AbortSignal): Promise<ApiResponseCurve> {
    return inferenceRequestCache.get(
      inferenceRequestKey(projectId, candidateId, inputIdentity, "curve", `${target}\u001f${variable}\u001f${stageName ?? ""}\u001f${stagePositionM ?? ""}\u001f${points}\u001f${rangeMin ?? "auto"}\u001f${rangeMax ?? "auto"}`),
      async (sharedSignal) => requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/response-curve", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { expected_revision: expectedRevision, target, variable, points, range_min: rangeMin, range_max: rangeMax, stage_name: stageName, stage_position_m: stagePositionM } }, signal: sharedSignal }), "応答曲線を取得できませんでした。"),
      signal,
    );
  },
  async curveFamily(projectId: string, candidateId: string, expectedRevision: number, inputIdentity: string, target: string, vary: string, levels = 5, points = 15, signal?: AbortSignal): Promise<ApiCurveFamily> {
    return inferenceRequestCache.get(
      inferenceRequestKey(projectId, candidateId, inputIdentity, "curve_family", `${target}${vary}${levels}${points}`),
      async (sharedSignal) => requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/curve-family", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { expected_revision: expectedRevision, target, vary, levels, points } }, signal: sharedSignal }), "曲線ビューを取得できませんでした。"),
      signal,
    );
  },
  async similarCandidates(projectId: string, candidateId: string, expectedRevision: number, inputIdentity: string, target?: string, limit = 6, signal?: AbortSignal) {
    return inferenceRequestCache.get(
      inferenceRequestKey(projectId, candidateId, inputIdentity, "similarity", `${target ?? ""}\u001f${limit}`),
      async (sharedSignal) => requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/similar", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { expected_revision: expectedRevision, limit, target } }, signal: sharedSignal }), "類似実験を取得できませんでした。"),
      signal,
    );
  },
  async inferenceDiagnostics() {
    return requireData(await apiClient.GET("/api/diagnostics/inference"), "推論diagnosticsを取得できませんでした。");
  },
  async decisionActivities(projectId: string, candidateId: string, expectedRevision: number, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/decision-activities", {
      params: { path: { project_id: projectId }, query: { candidate_id: candidateId, expected_revision: expectedRevision } },
      signal,
    }), "検討アクティビティを取得できませんでした。");
  },
  async decisionActivityRuns(projectId: string, candidateId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/decision-activity-runs", {
      params: { path: { project_id: projectId }, query: { candidate_id: candidateId } },
      signal,
    }), "保存済みの検討アクティビティを取得できませんでした。");
  },
  async runDecisionActivity(projectId: string, candidateId: string, activityId: string, body: ApiDecisionActivityRunRequest, signal?: AbortSignal) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/decision-activities/{activity_id}/runs", {
      params: { path: { project_id: projectId, candidate_id: candidateId, activity_id: activityId } },
      body,
      signal,
    }), "検討アクティビティを実行できませんでした。");
  },
  async snapshots(projectId: string, candidateId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/snapshots", { params: { path: { project_id: projectId, candidate_id: candidateId } }, signal }), "スナップショットを取得できませんでした。");
  },
  async predictionVsActual(projectId: string, candidateId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/prediction-vs-actual", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      signal,
    }), "予測と実測の照合履歴を取得できませんでした。");
  },
  async createActual(projectId: string, candidateId: string, expectedRevision: number, body: ApiActualMeasurementInput) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/actuals", {
      params: {
        path: { project_id: projectId, candidate_id: candidateId },
        query: { expected_revision: expectedRevision },
      },
      body,
    }), "実測を登録できませんでした。");
  },
  async restoreSnapshot(projectId: string, snapshotId: string) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/snapshots/{snapshot_id}/restore", { params: { path: { project_id: projectId, snapshot_id: snapshotId } } }), "スナップショットを復元できませんでした。");
  },
  async candidate(projectId: string, candidateId: string, includeArchived = false) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { include_archived: includeArchived } } }), "候補を参照できませんでした。");
  },
  async candidateRevision(projectId: string, candidateId: string, revision: number) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/revisions/{revision}", {
      params: { path: { project_id: projectId, candidate_id: candidateId, revision } },
    }), "指定した候補版を参照できませんでした。");
  },
  async candidateBlendMaterials(projectId: string, candidateId: string, revision?: number) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/blend-materials", {
      params: {
        path: { project_id: projectId, candidate_id: candidateId },
        query: { revision },
      },
    }), "原料情報を取得できませんでした。");
  },
  async candidateDerivationChain(projectId: string, candidateId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/derivation-chain", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
    }), "候補の派生履歴を取得できませんでした。");
  },
  async snapshot(projectId: string, snapshotId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/snapshots/{snapshot_id}", { params: { path: { project_id: projectId, snapshot_id: snapshotId } }, signal }), "保存済み予測を参照できませんでした。");
  },
  async quality(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/quality", { params: { path: { project_id: projectId } } }), "データ品質を取得できませんでした。");
  },
  async qualityCsv(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/quality/export.csv", { params: { path: { project_id: projectId } }, parseAs: "text" }), "データ品質CSVを取得できませんでした。");
  },
  async lineageIndex(projectId: string, query: string, entityType: string, issueFilter: "all" | "with_issues" | "without_issues", signal?: AbortSignal) {
    const normalizedEntityType = entityType === "すべて" ? "" : entityType;
    return requireData(await apiClient.GET("/api/projects/{project_id}/lineage", { params: { path: { project_id: projectId }, query: { query, entity_type: normalizedEntityType, issue_filter: issueFilter, include_hidden: false, limit: 200 } }, signal }), "実績・工程を検索できませんでした。");
  },
  async lineage(projectId: string, entityKey: string, limit = 40, allReachable = false, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/lineage/{entity_key}", { params: { path: { project_id: projectId, entity_key: entityKey }, query: { limit, all_reachable: allReachable } }, signal }), "系譜を取得できませんでした。");
  },
  async createCandidateFromLineage(entityKey: string, projectId: string, processKey?: string, meltKey?: string) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/lineage/{entity_key}/candidate", { params: { path: { project_id: projectId, entity_key: entityKey }, query: { process_key: processKey, melt_key: meltKey } } }), "候補を作成できませんでした。");
  },
  async lineageReviews(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/lineage-reviews", { params: { path: { project_id: projectId } } }), "確認メモを取得できませんでした。");
  },
  async saveLineageReview(projectId: string, entityKey: string, body: ApiLineageNodeReviewInput) {
    return requireData(await apiClient.PUT("/api/projects/{project_id}/lineage-reviews/{entity_key}", { params: { path: { project_id: projectId, entity_key: entityKey } }, body }), "確認メモを保存できませんでした。");
  },
  async deleteLineageReview(projectId: string, entityKey: string) {
    requireSuccess(await apiClient.DELETE("/api/projects/{project_id}/lineage-reviews/{entity_key}", { params: { path: { project_id: projectId, entity_key: entityKey } } }), "確認メモを削除できませんでした。");
  },
  async lineageReviewsCsv(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/lineage-reviews/export.csv", { params: { path: { project_id: projectId } }, parseAs: "text" }), "確認メモCSVを出力できませんでした。");
  },
  async listScreeningRuns(projectId: string) {
    return requireData(await apiClient.GET("/api/screening", { params: { query: { project_id: projectId } } }), "保存済み探索を取得できませんでした。");
  },
  async createScreeningRun(projectId: string, body: ApiScreeningRequest) {
    return requireData(await apiClient.POST("/api/screening", { params: { query: { project_id: projectId } }, body }), "範囲探索を実行できませんでした。");
  },
  async screeningRun(projectId: string, runId: string) {
    return requireData(await apiClient.GET("/api/screening/{run_id}", { params: { path: { run_id: runId }, query: { project_id: projectId } } }), "保存済み探索を開けませんでした。");
  },
  async candidatesFromScreening(projectId: string, runId: string, pointIndices: number[]) {
    return requireData(await apiClient.POST("/api/screening/{run_id}/candidates", { params: { path: { run_id: runId }, query: { project_id: projectId } }, body: { point_indices: pointIndices } }), "候補を作成できませんでした。");
  },
  async importCandidates(projectId: string, file: File) {
    const form = new FormData();
    form.append("file", file);
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates/import", {
      params: { path: { project_id: projectId } },
      body: { file: file.name },
      bodySerializer: () => form,
    }), "XLSXを取り込めませんでした。");
  },
  candidateExportUrl(projectId: string) {
    return apiDownloadUrl(`${path(projectId, "/candidates/export.xlsx")}`);
  },
  candidateTemplateUrl(projectId: string) {
    return apiDownloadUrl(`${path(projectId, "/candidates/template.xlsx")}`);
  },
};
