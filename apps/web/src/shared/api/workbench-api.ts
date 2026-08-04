import type { components } from "../../generated/api-types";
import { apiClient, apiDownloadUrl, requireData, requireSuccess } from "./client";
import { candidateInferencePrefix, inferenceRequestCache, inferenceRequestKey } from "./inferenceRequestCache";

export type ApiCandidate = components["schemas"]["Candidate"];
export type ApiCandidateInput = components["schemas"]["CandidateInput"];
export type ApiCandidateUpdate = components["schemas"]["CandidateUpdate"];
export type ApiCandidateCapacity = components["schemas"]["CandidateCapacity"];
export type ApiProject = components["schemas"]["Project"];
export type ApiProjectInput = components["schemas"]["ProjectUpdateInput"];
export type ApiProjectCreateInput = components["schemas"]["ProjectCreateInput"];
export type ApiModelPackage = components["schemas"]["ModelPackageStatus"];
export type ApiModelTrainingDataPage = components["schemas"]["ModelTrainingDataPage"];
export type ApiOutputSpaceEvidence = components["schemas"]["OutputSpaceEvidenceResponse"];
export type ApiInputSpaceEmbedding = components["schemas"]["InputSpaceEmbeddingResponse"];
export type ApiPreview = components["schemas"]["PredictionResponse"];
export type ApiSnapshot = components["schemas"]["SnapshotResponse"];
export type ApiActualMeasurementInput = components["schemas"]["ActualMeasurementInput"];
export type ApiPredictionVsActual = components["schemas"]["PredictionVsActualResponse"];
export type ApiResponseCurve = components["schemas"]["ResponseCurveResponse"];
export type ApiCurveFamily = components["schemas"]["CurveFamilyResponse"];
export type ApiResponseContour = components["schemas"]["ResponseContourResponse"];
export type ApiInferenceDiagnostics = components["schemas"]["InferenceDiagnosticsResponse"];
export type ApiSimilarObservation = components["schemas"]["SimilarObservation"];
export type ApiCandidateOriginEvidence = components["schemas"]["CandidateOriginEvidence"];
export type ApiHistoricalObservationList = components["schemas"]["HistoricalObservationListResponse"];
export type ApiHistoricalObservationEvidence = components["schemas"]["HistoricalObservationEvidence"];
export type ApiHistoricalObservationCandidate = components["schemas"]["HistoricalObservationCandidateResponse"];
export type ApiQuality = components["schemas"]["QualityResponse"];
export type ApiLineage = components["schemas"]["LineageResponse"];
export type ApiLineageCandidateOption = components["schemas"]["LineageCandidateOption"];
export type ApiLineageIndex = components["schemas"]["LineageIndexResponse"];
export type ApiLineageNodeReview = components["schemas"]["LineageNodeReview"];
export type ApiLineageNodeReviewInput = components["schemas"]["LineageNodeReviewInput"];
export type ApiLineageNodeReviewList = components["schemas"]["LineageNodeReviewList"];
export type ApiScreeningRequest = components["schemas"]["ScreeningRequest"];
export type ApiScreeningRun = components["schemas"]["ScreeningRunResponse"];
export type ApiScreeningCandidateBatch = components["schemas"]["ScreeningCandidateBatchResponse"];
export type ApiProposalStrategyAvailability = components["schemas"]["ProposalStrategyAvailability"];
export type ApiProposalLabCreateRequest = components["schemas"]["ProposalLabCreateRequest"];
export type ApiProposalLabReport = components["schemas"]["ProposalLabReport"];
export type ApiTaskDefinition = components["schemas"]["ResolvedTaskDefinition"];
export type ApiTaskCatalogItem = components["schemas"]["TaskCatalogItem"];
export type ApiProjectHistory = components["schemas"]["ProjectHistoryResponse"];
export type ApiProjectDecisionInput = components["schemas"]["ProjectDecisionInput"];
export type ApiProjectGroupMoveInput = components["schemas"]["ProjectGroupMoveInput"];
export type ApiProjectCreationOptions = components["schemas"]["ProjectCreationOptions"];
export type ApiChainTemplate = components["schemas"]["ChainTemplateItem"];
export type ApiChainStudioCatalog = components["schemas"]["ChainStudioCatalogResponse"];
export type ApiChainStudioDraft = components["schemas"]["ChainStudioDraftRequest"];
export type ApiChainStudioDraftValidation = components["schemas"]["ChainStudioDraftValidation"];
export type ApiPredictionGraphCatalog = components["schemas"]["PredictionGraphCatalogResponse"];
export type ApiPredictionGraphDefinition = components["schemas"]["PredictionGraphDefinition"];
export type ApiPredictionGraphValidation = components["schemas"]["PredictionGraphDraftValidation"];
export type ApiPredictionGraphPublishResponse = components["schemas"]["PredictionGraphPublishResponse"];
export type ApiPredictionGraphProjectCreate = components["schemas"]["PredictionGraphProjectCreateRequest"];
export type ApiPredictionGraphDraftContent = components["schemas"]["PredictionGraphDraftContent"];
export type ApiPredictionGraphDraftDocument = components["schemas"]["PredictionGraphDraftDocument"];
export type ApiPredictionGraphDraftConflict = components["schemas"]["PredictionGraphDraftConflictResponse"];
export type ApiPredictionGraphDraftSaveResult =
  | { status: "saved"; document: ApiPredictionGraphDraftDocument }
  | { status: "conflict"; conflict: ApiPredictionGraphDraftConflict };
export type ApiModelLibraryCatalog = components["schemas"]["ModelLibraryCatalog"];
export type ApiModelPlaygroundPreview = components["schemas"]["ModelPlaygroundContextPreview"];
export type ApiModelExplorationRun = components["schemas"]["ModelExplorationRun"];
export type ApiModelExplorationRunCreate = components["schemas"]["ModelExplorationRunCreateRequest"];
export type ApiModelExplorationAdoptionMemo = components["schemas"]["ModelExplorationAdoptionMemoRequest"];
export type ApiChainGraph = components["schemas"]["ChainGraphResponse"];
export type ApiChainDistributionCapability = components["schemas"]["ChainDistributionCapability"];
export type ApiChainDistributionRun = components["schemas"]["ChainDistributionRun"];
export type ApiChainEvaluation = components["schemas"]["ResolvedChainEvaluation"];
export type ApiSubsystemAvailability = components["schemas"]["SubsystemAvailability"];
export type ApiWorkspaceHealth = {
  ready: boolean;
  resources_loading_error: string | null;
  workspace: {
    database_path: string;
    data_library_path: string;
    kind: string;
  };
  storage: {
    ready: boolean;
    task_store: ApiWorkspaceStorage;
    model_store: ApiWorkspaceStorage;
    next_action: string;
  };
};
export type ApiWorkspaceStorage = {
  label: string;
  path: string | null;
  available: boolean;
  reason: string;
  next_action: string;
};
export type ApiChainCandidateContract = components["schemas"]["ChainCandidateContractResponse"];
export type ApiChainCandidateCapability = components["schemas"]["ChainCandidateCapability"];
export type ApiChainExecution = components["schemas"]["ChainExecution"];
export type ApiPredictionGraphExecution = components["schemas"]["PredictionGraphExecution"];
export type ApiPredictionGraphCandidateInput =
  components["schemas"]["PredictionGraphCandidateInputDefinition"];
export type ApiPredictionGraphSnapshot = components["schemas"]["PredictionGraphSnapshot"];
export type ApiPredictionGraphDecisionOutputActual =
  components["schemas"]["PredictionGraphDecisionOutputActual"];
export type ApiPredictionGraphDecisionOutputActualInput =
  components["schemas"]["PredictionGraphDecisionOutputActualInput"];
export type ApiPredictionGraphDesignSpace =
  components["schemas"]["PredictionGraphDesignSpace"];
export type ApiPredictionGraphObjective =
  components["schemas"]["PredictionGraphObjective"];
export type ApiPredictionGraphObjectiveInput =
  components["schemas"]["PredictionGraphObjectiveInput"];
export type ApiPredictionGraphGoalSearchRun =
  components["schemas"]["PredictionGraphGoalSearchRun"];
export type ApiPredictionGraphGoalSearchRequest =
  components["schemas"]["PredictionGraphGoalSearchRequest"];
export type ApiPredictionGraphPromotionRequest =
  components["schemas"]["PredictionGraphPromotionRequest"];
export type ApiChainSnapshot = components["schemas"]["ChainSnapshot"];
export type ApiActualConditionedVariant = components["schemas"]["ActualConditionedVariant"];
export type ApiActualConditionedVariantInput = components["schemas"]["ActualConditionedVariantRequest"];
export type ApiDataLibraryDataset = components["schemas"]["DataLibraryDataset"];
export type ApiDatasetView = components["schemas"]["DatasetViewRevision"];
export type ApiDatasetViewCreateInput = components["schemas"]["DatasetViewRevisionCreateInput"];
export type ApiModelPackageRef = components["schemas"]["DataLibraryModelPackage"];
export type ApiProjectSeries = components["schemas"]["ProjectSeries"];
export type ApiSampleGalleryItem = components["schemas"]["SampleGalleryItem"];
export type ApiProfileWorkbenchInspection = components["schemas"]["ProfileWorkbenchInspection"];
export type ApiProfileWorkbenchProfile = components["schemas"]["ProfileWorkbenchProfileOption"];
export type ApiProfileWorkbenchRegistration = components["schemas"]["ProfileWorkbenchRegistration"];
export type ApiProfileWorkbenchDraft = components["schemas"]["ProfileWorkbenchDraftSave"];
export type ApiObservationAuthoringTask = components["schemas"]["ObservationAuthoringTask"];
export type ApiObservationAuthoringResult = components["schemas"]["ObservationAuthoringResult"];
export type ApiDeveloperOverview = components["schemas"]["DeveloperOverview"];
export type ApiDeveloperCapabilityAtlas = components["schemas"]["DeveloperCapabilityAtlas"];
export type ApiRuntimeDiagnostics = components["schemas"]["RuntimeDiagnosticsReport"];
export type ApiDeveloperCommand = components["schemas"]["DeveloperCommand"];
export type ApiChangeGuideEntry = components["schemas"]["ChangeGuideEntry"];
export type ApiObservationTrainingProfile = components["schemas"]["ObservationTrainingProfileSummary"];
export type ApiObservationTrainingPage = components["schemas"]["ObservationTrainingInspectionPage"];
export type ApiBlendMaterial = components["schemas"]["BlendMaterialDescriptor"];
export type ApiBlendOptimizationContext = components["schemas"]["BlendOptimizationContext"];
export type ApiBlendOptimizationRequest = components["schemas"]["BlendOptimizationRequest"];
export type ApiBlendOptimizationResult = components["schemas"]["BlendOptimizationResult"];
export type ApiBlendEditorContext = components["schemas"]["BlendEditorContext"];
export type ApiDeterministicTransform = components["schemas"]["DeterministicTransformCatalogItem"];
export type ApiDeterministicTransformResult = components["schemas"]["DeterministicLinearResult"];
export type ApiDecisionActivityAvailability = components["schemas"]["DecisionActivityAvailability"];
export type ApiDecisionActivityRun = components["schemas"]["DecisionActivityRun"];
export type ApiDecisionActivityRunRequest = components["schemas"]["DecisionActivityRunRequest"];
export type ApiRawSeriesAsset = components["schemas"]["RawSeriesAsset"];
export type ApiSeriesAssetDetail = components["schemas"]["SeriesAssetDetail"];
export type ApiCanonicalSeriesRevision = components["schemas"]["CanonicalSeriesRevision"];
export type ApiSeriesFeaturePreview = components["schemas"]["SeriesFeaturePreview"];
export type ApiSeriesFeatureContract = components["schemas"]["SeriesFeatureContract"];
export type ApiDataLifecycleCatalog = components["schemas"]["DataLifecycleCatalog"];
export type ApiConnectorLifecycleDetail = components["schemas"]["ConnectorLifecycleSummary"];
export type ApiRawSnapshotRowPage = components["schemas"]["RawSnapshotRowPage"];
export type ApiCurationRunRowPage = components["schemas"]["CurationRunRowPage"];
export type ApiSourceConnectorInput = components["schemas"]["SourceConnectorCreateInput"];
export type ApiSourceFetchRequest = components["schemas"]["SourceFetchRequest"];
export type ApiCurationRecipeInput = components["schemas"]["CurationRecipeCreateInput"];
export type ApiCurationRunInput = components["schemas"]["CurationRunCreateInput"];
export type ApiDatasetApprovalInput = components["schemas"]["DatasetApprovalRequest"];
export type ApiTrainingSnapshotInput = components["schemas"]["TrainingSnapshotCreateRequest"];
export type ApiTrainingSnapshotDetail = components["schemas"]["ApprovedTrainingSnapshotDetail"];

const path = (projectId: string, suffix = "") =>
  `/api/projects/${encodeURIComponent(projectId)}${suffix}`;

export const workbenchApi = {
  async health(): Promise<ApiWorkspaceHealth> {
    return requireData(
      await apiClient.GET("/api/health"),
      "Workspace情報を取得できませんでした。",
    ) as ApiWorkspaceHealth;
  },
  async candidateCapacity(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidate-capacity", {
      params: { path: { project_id: projectId } },
    }), "候補枠を取得できませんでした。");
  },
  async dataLifecycleCatalog() {
    return requireData(await apiClient.GET("/api/data-lifecycle"), "データ更新履歴を取得できませんでした。");
  },
  async createSourceConnector(body: ApiSourceConnectorInput) {
    return requireData(await apiClient.POST("/api/data-lifecycle/connectors", { body }), "Source Connectorを登録できませんでした。");
  },
  async sourceConnectorDetail(connectorId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/data-lifecycle/connectors/{connector_id}", {
      params: { path: { connector_id: connectorId } },
      signal,
    }), "Source Connectorの履歴を取得できませんでした。");
  },
  async rawSnapshotRows(snapshotId: string, offset = 0, limit = 50, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/data-lifecycle/raw-snapshots/{snapshot_id}/rows", {
      params: { path: { snapshot_id: snapshotId }, query: { offset, limit } },
      signal,
    }), "Raw Snapshotの行を取得できませんでした。");
  },
  async curationRunRows(
    runId: string,
    offset = 0,
    limit = 50,
    signal?: AbortSignal,
    status?: "accepted" | "warning" | "quarantined" | "blocked",
    reasonedOnly = false,
  ) {
    return requireData(await apiClient.GET("/api/data-lifecycle/curation-runs/{run_id}/rows", {
      params: {
        path: { run_id: runId },
        query: { offset, limit, status, reasoned_only: reasonedOnly },
      },
      signal,
    }), "品質判定の行を取得できませんでした。");
  },
  async fetchSourceConnector(connectorId: string, body: ApiSourceFetchRequest, credential = "") {
    return requireData(await apiClient.POST("/api/data-lifecycle/connectors/{connector_id}/fetch", {
      params: { path: { connector_id: connectorId } },
      body,
      headers: credential ? { "X-Source-Credential": credential } : undefined,
    }), "SourceからRaw Snapshotを取得できませんでした。");
  },
  async createCurationRecipe(body: ApiCurationRecipeInput) {
    return requireData(await apiClient.POST("/api/data-lifecycle/recipes", { body }), "Curation Recipeを登録できませんでした。");
  },
  async curateRawSnapshot(snapshotId: string, body: ApiCurationRunInput) {
    return requireData(await apiClient.POST("/api/data-lifecycle/raw-snapshots/{snapshot_id}/curation-runs", {
      params: { path: { snapshot_id: snapshotId } },
      body,
    }), "Raw Snapshotをcurationできませんでした。");
  },
  async approveCurationRun(runId: string, body: ApiDatasetApprovalInput) {
    return requireData(await apiClient.POST("/api/data-lifecycle/curation-runs/{run_id}/approve", {
      params: { path: { run_id: runId } },
      body,
    }), "Canonical Dataset Revisionを承認できませんでした。");
  },
  async createApprovedTrainingSnapshot(revisionId: string, body: ApiTrainingSnapshotInput) {
    return requireData(await apiClient.POST("/api/data-lifecycle/canonical-dataset-revisions/{revision_id}/training-snapshots", {
      params: { path: { revision_id: revisionId } },
      body,
    }), "Training Snapshotを作成できませんでした。");
  },
  async approvedTrainingSnapshot(snapshotId: string) {
    return requireData(await apiClient.GET("/api/data-lifecycle/training-snapshots/{snapshot_id}", {
      params: { path: { snapshot_id: snapshotId } },
    }), "Training Snapshotの詳細を取得できませんでした。");
  },
  async listSeriesAssets() {
    return requireData(await apiClient.GET("/api/series-assets"), "系列データを取得できませんでした。");
  },
  async seriesAsset(seriesId: string) {
    return requireData(await apiClient.GET("/api/series-assets/{series_id}", {
      params: { path: { series_id: seriesId } },
    }), "系列データの詳細を取得できませんでした。");
  },
  async seriesFeaturePreview(revisionId: string, body: ApiSeriesFeatureContract) {
    return requireData(await apiClient.POST("/api/canonical-series/{revision_id}/feature-preview", {
      params: { path: { revision_id: revisionId } },
      body,
    }), "系列特徴量を確認できませんでした。");
  },
  async chainCandidateCapability(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/candidate-capability", {
      params: { path: { project_id: projectId } },
    }), "Chainの候補入力capabilityを取得できませんでした。");
  },
  async chainCandidateInputs(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/candidate-inputs", {
      params: { path: { project_id: projectId } },
    }), "Chain候補の入力契約を取得できませんでした。");
  },
  async chainCandidateContract(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/candidate-contract", {
      params: { path: { project_id: projectId } },
    }), "Chain候補契約を取得できませんでした。");
  },
  async chainStarterCandidate(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/starter-candidate", {
      params: { path: { project_id: projectId } },
    }), "Chainの基準候補を取得できませんでした。");
  },
  async listChainCandidates(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/candidates", {
      params: { path: { project_id: projectId } },
    }), "Chain候補を取得できませんでした。");
  },
  async createChainCandidate(projectId: string, body: ApiCandidateInput) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/chain/candidates", {
      params: { path: { project_id: projectId } },
      body,
    }), "Chain候補を作成できませんでした。");
  },
  async updateChainCandidate(projectId: string, candidateId: string, body: ApiCandidateUpdate) {
    return requireData(await apiClient.PUT("/api/projects/{project_id}/chain/candidates/{candidate_id}", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body,
    }), "Chain候補を保存できませんでした。");
  },
  async chainExecution(projectId: string, candidateId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/candidates/{candidate_id}/execution", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      signal,
    }), "Chain実行結果を取得できませんでした。");
  },
  async predictionGraphExecution(projectId: string, candidateId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/prediction-graphs/projects/{project_id}/candidates/{candidate_id}/execution", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      signal,
    }), "Prediction Graph実行結果を取得できませんでした。");
  },
  async predictionGraphExecutionResource(projectId: string, candidateId: string, signal?: AbortSignal) {
    const result = await apiClient.GET("/api/prediction-graphs/projects/{project_id}/candidates/{candidate_id}/execution", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      signal,
    });
    if (result.response.status === 404) return null;
    return requireData(result, "Prediction Graph実行結果を取得できませんでした。");
  },
  async createPredictionGraphCandidate(projectId: string, body: ApiCandidateInput) {
    return requireData(await apiClient.POST("/api/prediction-graphs/projects/{project_id}/candidates", {
      params: { path: { project_id: projectId } },
      body,
    }), "Prediction Graph候補を作成できませんでした。");
  },
  async predictionGraphStarterCandidate(projectId: string) {
    return requireData(await apiClient.GET("/api/prediction-graphs/projects/{project_id}/starter-candidate", {
      params: { path: { project_id: projectId } },
    }), "Prediction Graphの基準候補を取得できませんでした。");
  },
  async predictionGraphCandidateInputs(projectId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/prediction-graphs/projects/{project_id}/candidate-inputs", {
      params: { path: { project_id: projectId } },
      signal,
    }), "Prediction Graph候補入力契約を取得できませんでした。");
  },
  async updatePredictionGraphCandidate(projectId: string, candidateId: string, body: ApiCandidateUpdate) {
    const result = await apiClient.PUT("/api/prediction-graphs/projects/{project_id}/candidates/{candidate_id}", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body,
    });
    if (result.response.status === 409) return { status: "conflict" as const };
    return {
      status: "saved" as const,
      candidate: requireData(result, "Prediction Graph候補を保存できませんでした。"),
    };
  },
  async executePredictionGraph(projectId: string, candidateId: string, candidateRevision: number, requestId: string) {
    return requireData(await apiClient.POST("/api/prediction-graphs/projects/{project_id}/candidates/{candidate_id}/executions", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body: { candidate_revision: candidateRevision, request_id: requestId, debounce_ms: 0 },
    }), "Prediction Graphを実行できませんでした。");
  },
  async listPredictionGraphSnapshots(projectId: string, candidateId: string) {
    return requireData(await apiClient.GET("/api/prediction-graphs/projects/{project_id}/candidates/{candidate_id}/snapshots", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
    }), "Prediction Graph Snapshotを取得できませんでした。");
  },
  async createPredictionGraphSnapshot(projectId: string, candidateId: string, candidateRevision: number) {
    return requireData(await apiClient.POST("/api/prediction-graphs/projects/{project_id}/candidates/{candidate_id}/snapshots", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body: { candidate_revision: candidateRevision, debounce_ms: 0 },
    }), "Prediction Graph Snapshotを保存できませんでした。");
  },
  async listPredictionGraphActuals(projectId: string, candidateId: string) {
    return requireData(await apiClient.GET("/api/prediction-graphs/projects/{project_id}/candidates/{candidate_id}/decision-output-actuals", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
    }), "Decision Output Actualを取得できませんでした。");
  },
  async createPredictionGraphActual(
    projectId: string,
    candidateId: string,
    body: ApiPredictionGraphDecisionOutputActualInput,
  ) {
    return requireData(await apiClient.POST("/api/prediction-graphs/projects/{project_id}/candidates/{candidate_id}/decision-output-actuals", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body,
    }), "Decision Output Actualを保存できませんでした。");
  },
  async predictionGraphGoalSearchDesignSpace(projectId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/prediction-graphs/projects/{project_id}/goal-search/design-space", {
      params: { path: { project_id: projectId } },
      signal,
    }), "Graph探索範囲を取得できませんでした。");
  },
  async listPredictionGraphObjectives(projectId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/prediction-graphs/projects/{project_id}/objectives", {
      params: { path: { project_id: projectId } },
      signal,
    }), "Graph Objectiveを取得できませんでした。");
  },
  async createPredictionGraphObjective(projectId: string, body: ApiPredictionGraphObjectiveInput) {
    return requireData(await apiClient.POST("/api/prediction-graphs/projects/{project_id}/objectives", {
      params: { path: { project_id: projectId } },
      body,
    }), "Graph Objectiveを保存できませんでした。");
  },
  async listPredictionGraphGoalSearchRuns(projectId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/prediction-graphs/projects/{project_id}/goal-search-runs", {
      params: { path: { project_id: projectId } },
      signal,
    }), "Graph goal-search Runを取得できませんでした。");
  },
  async createPredictionGraphGoalSearchRun(projectId: string, body: ApiPredictionGraphGoalSearchRequest) {
    return requireData(await apiClient.POST("/api/prediction-graphs/projects/{project_id}/goal-search-runs", {
      params: { path: { project_id: projectId } },
      body,
    }), "Graph goal-searchを実行できませんでした。");
  },
  async promotePredictionGraphGoalSearchResult(
    projectId: string,
    runId: string,
    body: ApiPredictionGraphPromotionRequest,
  ) {
    return requireData(await apiClient.POST("/api/prediction-graphs/projects/{project_id}/goal-search-runs/{run_id}/promotions", {
      params: { path: { project_id: projectId, run_id: runId } },
      body,
    }), "Graph goal-search結果を候補へ昇格できませんでした。");
  },
  async chainGraph(projectId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/graph", {
      params: { path: { project_id: projectId } },
      signal,
    }), "固定したChain構成を取得できませんでした。");
  },
  async chainStudioCatalog(signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/chains/studio/catalog", { signal }), "Chain StudioのTask catalogを取得できませんでした。");
  },
  async validateChainStudioDraft(body: ApiChainStudioDraft) {
    return requireData(await apiClient.POST("/api/chains/studio/validate", { body }), "Chain draftを検証できませんでした。");
  },
  async publishChainStudioDraft(body: ApiChainStudioDraft) {
    return requireData(await apiClient.POST("/api/chains/studio/publish", { body }), "Chain Revisionを公開できませんでした。");
  },
  async predictionGraphCatalog(signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/prediction-graphs/catalog", { signal }), "Prediction Graph catalogを取得できませんでした。");
  },
  async modelLibraryCatalog(signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/model-library", { signal }), "Model Libraryを取得できませんでした。");
  },
  async modelPlaygroundPreview(
    taskId: string,
    profileRevisionId: string,
    trainingSnapshotId: string,
    signal?: AbortSignal,
  ) {
    return requireData(await apiClient.GET("/api/model-playground/preview", {
      params: {
        query: {
          task_id: taskId,
          profile_revision_id: profileRevisionId,
          training_snapshot_id: trainingSnapshotId,
        },
      },
      signal,
    }), "Model Playgroundの固定contextを取得できませんでした。");
  },
  async createModelExplorationRun(body: ApiModelExplorationRunCreate) {
    return requireData(await apiClient.POST("/api/model-playground/runs", { body }), "Model Playground Runを作成できませんでした。");
  },
  async modelExplorationRun(runId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/model-playground/runs/{run_id}", {
      params: { path: { run_id: runId } },
      signal,
    }), "Model Playground Runを取得できませんでした。");
  },
  async executeModelExplorationRecipe(runId: string, recipeId: string, expectedRevision: number) {
    return requireData(await apiClient.POST("/api/model-playground/runs/{run_id}/recipes/{recipe_id}/attempts", {
      params: { path: { run_id: runId, recipe_id: recipeId } },
      body: { expected_revision: expectedRevision },
    }), "選択したmodel recipeを実行できませんでした。");
  },
  async registerModelExplorationAttempt(runId: string, attemptId: string, expectedRevision: number) {
    return requireData(await apiClient.POST("/api/model-playground/runs/{run_id}/attempts/{attempt_id}/register", {
      params: { path: { run_id: runId, attempt_id: attemptId } },
      body: { expected_revision: expectedRevision },
    }), "Model PackageをModel Libraryへ登録できませんでした。");
  },
  async saveModelExplorationAdoptionMemo(
    runId: string,
    body: ApiModelExplorationAdoptionMemo,
  ) {
    return requireData(await apiClient.PUT("/api/model-playground/runs/{run_id}/adoption-memo", {
      params: { path: { run_id: runId } },
      body,
    }), "Adoption memoを保存できませんでした。");
  },
  async createPredictionGraphDraft(content: ApiPredictionGraphDraftContent) {
    return requireData(await apiClient.POST("/api/prediction-graph-drafts", {
      body: { content },
    }), "Prediction Graph draftを保存できませんでした。");
  },
  async predictionGraphDraft(draftId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/prediction-graph-drafts/{draft_id}", {
      params: { path: { draft_id: draftId } },
      signal,
    }), "保存済みのPrediction Graph draftを取得できませんでした。");
  },
  async updatePredictionGraphDraft(
    draftId: string,
    expectedVersion: number,
    content: ApiPredictionGraphDraftContent,
  ): Promise<ApiPredictionGraphDraftSaveResult> {
    const result = await apiClient.PUT("/api/prediction-graph-drafts/{draft_id}", {
      params: { path: { draft_id: draftId } },
      body: { expected_version: expectedVersion, content },
    });
    if (result.data !== undefined) return { status: "saved", document: result.data };
    if (
      result.response.status === 409
      && result.error !== undefined
      && "current" in result.error
      && result.error.code === "revision_conflict"
    ) {
      return { status: "conflict", conflict: result.error };
    }
    return {
      status: "saved",
      document: requireData<ApiPredictionGraphDraftDocument>(
        result,
        "Prediction Graph draftを更新できませんでした。",
      ),
    };
  },
  async validatePredictionGraph(definition: ApiPredictionGraphDefinition) {
    return requireData(await apiClient.POST("/api/prediction-graphs/validate", { body: { definition } }), "Prediction Graphを検証できませんでした。");
  },
  async publishPredictionGraph(definition: ApiPredictionGraphDefinition) {
    return requireData(await apiClient.POST("/api/prediction-graphs/publish", { body: { definition } }), "Prediction Graph Revisionを公開できませんでした。");
  },
  async createPredictionGraphProject(body: ApiPredictionGraphProjectCreate) {
    return requireData(await apiClient.POST("/api/prediction-graphs/projects", { body }), "Prediction Graph Projectを作成できませんでした。");
  },
  async executeChain(projectId: string, candidateId: string, candidateRevision: number, requestId: string, signal?: AbortSignal) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/chain/candidates/{candidate_id}/executions", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body: { candidate_revision: candidateRevision, request_id: requestId, debounce_ms: 250 },
      signal,
    }), "Chainを実行できませんでした。");
  },
  async listChainSnapshots(projectId: string, candidateId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/candidates/{candidate_id}/snapshots", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
    }), "Chainスナップショットを取得できませんでした。");
  },
  async createChainSnapshot(projectId: string, candidateId: string, candidateRevision: number) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/chain/candidates/{candidate_id}/snapshots", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body: { candidate_revision: candidateRevision, debounce_ms: 0 },
    }), "Chainスナップショットを保存できませんでした。");
  },
  async chainSnapshot(projectId: string, snapshotId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain-snapshots/{snapshot_id}", {
      params: { path: { project_id: projectId, snapshot_id: snapshotId } },
      signal,
    }), "Chainスナップショットを取得できませんでした。");
  },
  async listChainAnalysisVariants(projectId: string, candidateId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/candidates/{candidate_id}/analysis-variants", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
    }), "実測を使った分析を取得できませんでした。");
  },
  async createChainAnalysisVariant(projectId: string, candidateId: string, body: ApiActualConditionedVariantInput) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/chain/candidates/{candidate_id}/analysis-variants", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body,
    }), "実測を使った分析を保存できませんでした。");
  },
  async deterministicTransforms() {
    return requireData(await apiClient.GET("/api/transforms"), "Stage A Transformを取得できませんでした。");
  },
  async blendEditorContext(transformId: string) {
    return requireData(await apiClient.GET("/api/transforms/{transform_id}/blend-editor", {
      params: { path: { transform_id: transformId } },
    }), "配合用の原料catalogを取得できませんでした。");
  },
  async resolveBlendEditorContext(
    transformId: string,
    blend: components["schemas"]["SparseBlend"],
  ) {
    return requireData(await apiClient.POST("/api/transforms/{transform_id}/blend-editor/resolve", {
      params: { path: { transform_id: transformId } },
      body: { blend },
    }), "保存候補の原料catalogを解決できませんでした。");
  },
  async executeDeterministicTransform(transformId: string, blend: components["schemas"]["SparseBlend"], signal?: AbortSignal) {
    return requireData(await apiClient.POST("/api/transforms/{transform_id}/execute", {
      params: { path: { transform_id: transformId } },
      body: { blend },
      signal,
    }), "Stage Aの派生成分を計算できませんでした。");
  },
  async developerOverview() {
    return requireData(await apiClient.GET("/api/developer/overview"), "Developer構成を取得できませんでした。");
  },
  async developerCapabilityAtlas() {
    return requireData(
      await apiClient.GET("/api/developer/capability-atlas"),
      "Capability Atlasを取得できませんでした。",
    );
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
  async listProjects(includeArchived = false) {
    return requireData(await apiClient.GET("/api/projects", {
      params: { query: { include_archived: includeArchived } },
    }), "プロジェクトを取得できませんでした。");
  },
  async listSampleGallery() {
    return requireData(
      await apiClient.GET("/api/sample-gallery"),
      "サンプル一覧を取得できませんでした。",
    );
  },
  async installSampleGallery(projectIds: string[] = []) {
    return requireData(
      await apiClient.POST("/api/sample-gallery", {
        body: { project_ids: projectIds },
      }),
      "サンプルを追加できませんでした。",
    );
  },
  async removeSampleGallery(projectId: string) {
    requireSuccess(
      await apiClient.DELETE("/api/sample-gallery/{project_id}", {
        params: { path: { project_id: projectId } },
      }),
      "サンプルを取り除けませんでした。",
    );
  },
  async listTaskDefinitions() {
    return requireData(await apiClient.GET("/api/task-definitions"), "予測タスクを取得できませんでした。");
  },
  async listSubsystemAvailability() {
    return requireData(
      await apiClient.GET("/api/subsystem-availability"),
      "機能の利用可否を取得できませんでした。",
    );
  },
  async projectCreationOptions() {
    return requireData(await apiClient.GET("/api/project-creation-options"), "プロジェクト作成条件を取得できませんでした。");
  },
  async listChainTemplates() {
    return requireData(await apiClient.GET("/api/chains"), "Chain Templateを取得できませんでした。");
  },
  async chainDistributionCapability(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/distribution-capability", {
      params: { path: { project_id: projectId } },
    }), "Chainの分布実行可否を取得できませんでした。");
  },
  async latestChainDistribution(projectId: string, candidateId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/candidates/{candidate_id}/distribution-runs/latest", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      signal,
    }), "保存済みのChain分布を取得できませんでした。");
  },
  async runChainDistribution(
    projectId: string,
    candidateId: string,
    candidateRevision: number,
    seed = 20260725,
    sampleCount = 512,
    signal?: AbortSignal,
  ) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/chain/candidates/{candidate_id}/distribution-runs", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body: { candidate_revision: candidateRevision, seed, sample_count: sampleCount },
      signal,
    }), "Chainの分布を実行できませんでした。");
  },
  async projectChainEvaluation(projectId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/chain/evaluation", {
      params: { path: { project_id: projectId } },
      signal,
    }), "Chain評価を取得できませんでした。");
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
  async refreshModelPackageRefs() {
    return requireData(
      await apiClient.POST("/api/data-library/model-packages/refresh"),
      "個人Model Packageを再読込できませんでした。",
    );
  },
  async refreshTaskResources() {
    return requireData(
      await apiClient.POST("/api/data-library/tasks/refresh"),
      "個人Taskを再読込できませんでした。",
    );
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
  async listObservationAuthoringTasks() {
    return requireData(
      await apiClient.GET("/api/profile-workbench/observation-authoring/tasks"),
      "反復測定に対応する予測タスクを取得できませんでした。",
    );
  },
  async authorObservationProfile(file: File, contract: {
    task_id: string;
    observation_grain: string;
    observation_id_column: string;
    group_column: string;
    inputs: Array<{
      path: string;
      column: string;
      source_unit?: string;
    }>;
    targets: Array<{ key: string; column: string; source_unit: string }>;
    technical_metadata_columns: string[];
    validation_folds: number;
    ridge_alpha: number;
  }) {
    const form = new FormData();
    const contractJson = JSON.stringify(contract);
    form.append("file", file);
    form.append("contract_json", contractJson);
    return requireData(await apiClient.POST("/api/profile-workbench/observation-authoring", {
      body: { file: file.name, contract_json: contractJson },
      bodySerializer: () => form,
    }), "反復測定Profileを作成できませんでした。");
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
  async saveProfileWorkbenchDraft(
    file: File,
    baseProfileDigest: string,
    expectedSourceSha256: string,
    bindings: Array<{
      slot_id: string;
      state: "confirmed";
      source_name: string;
      source_unit?: string;
    }>,
  ) {
    const form = new FormData();
    form.append("file", file);
    form.append("base_profile_digest", baseProfileDigest);
    form.append("expected_source_sha256", expectedSourceSha256);
    form.append("bindings_json", JSON.stringify(bindings));
    return requireData(await apiClient.POST("/api/profile-workbench/profiles/drafts", {
      body: {
        file: file.name,
        base_profile_digest: baseProfileDigest,
        expected_source_sha256: expectedSourceSha256,
        bindings_json: JSON.stringify(bindings),
      },
      bodySerializer: () => form,
    }), "Profile draftを保存できませんでした。");
  },
  profileWorkbenchExportUrl(profileDigest: string) {
    return apiDownloadUrl(`/api/profile-workbench/profiles/${encodeURIComponent(profileDigest)}/export`);
  },
  async registerProfileWorkbook(file: File, profileDigest: string, expectedSourceSha256: string, name: string, baseDatasetRevisionId?: string) {
    const form = new FormData();
    form.append("file", file);
    form.append("profile_digest", profileDigest);
    form.append("expected_source_sha256", expectedSourceSha256);
    if (name.trim()) form.append("name", name.trim());
    if (baseDatasetRevisionId) form.append("base_dataset_revision_id", baseDatasetRevisionId);
    return requireData(await apiClient.POST("/api/profile-workbench/register", {
      body: {
        file: file.name,
        profile_digest: profileDigest,
        expected_source_sha256: expectedSourceSha256,
        name: name.trim() || undefined,
        base_dataset_revision_id: baseDatasetRevisionId,
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
  async archiveProject(projectId: string) {
    requireSuccess(await apiClient.DELETE("/api/projects/{project_id}", { params: { path: { project_id: projectId } } }), "プロジェクトをアーカイブできませんでした。");
    inferenceRequestCache.invalidatePrefix(candidateInferencePrefix(projectId));
  },
  async restoreProject(projectId: string) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/restore", {
      params: { path: { project_id: projectId } },
    }), "プロジェクトを復元できませんでした。");
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
  async outputSpaceEvidence(
    projectId: string,
    candidateId: string,
    candidateRevision: number,
    xTarget: string,
    yTarget: string,
    distanceFilter: "supported" | "caution" | "all",
    limit = 200,
    signal?: AbortSignal,
  ) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/model-package/output-space-evidence", {
      params: {
        path: { project_id: projectId },
        query: {
          x_target: xTarget,
          y_target: yTarget,
          candidate_id: candidateId,
          expected_revision: candidateRevision,
          distance_filter: distanceFilter,
          limit,
        },
      },
      signal,
    }), "特性バランスの実績を取得できませんでした。");
  },
  async inputSpace(
    projectId: string,
    candidateId: string,
    candidateRevision: number,
    signal?: AbortSignal,
  ) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/model-package/input-space", {
      params: {
        path: { project_id: projectId },
        query: {
          candidate_id: candidateId,
          expected_revision: candidateRevision,
        },
      },
      signal,
    }), "入力空間の位置を取得できませんでした。");
  },
  async listCandidates(projectId: string, includeArchived = false, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates", {
      params: { path: { project_id: projectId }, query: { include_archived: includeArchived } },
      signal,
    }), "候補を取得できませんでした。");
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
  async restoreCandidate(projectId: string, candidateId: string) {
    const restored = requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/restore", { params: { path: { project_id: projectId, candidate_id: candidateId } } }), "候補を復元できませんでした。");
    inferenceRequestCache.invalidatePrefix(candidateInferencePrefix(projectId, candidateId));
    return restored;
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
  async responseContour(projectId: string, candidateId: string, expectedRevision: number, inputIdentity: string, target: string, xVariable: string, yVariable: string, points = 11, signal?: AbortSignal): Promise<ApiResponseContour> {
    return inferenceRequestCache.get(
      inferenceRequestKey(projectId, candidateId, inputIdentity, "response_contour", `${expectedRevision}\u001f${target}\u001f${xVariable}\u001f${yVariable}\u001f${points}`),
      async (sharedSignal) => requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/response-contour", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { expected_revision: expectedRevision, target, x_variable: xVariable, y_variable: yVariable, points } }, signal: sharedSignal }), "予測コンターを取得できませんでした。"),
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
  async promoteDecisionActivityProposal(projectId: string, runId: string, proposalId: string) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/decision-activity-runs/{run_id}/proposals/{proposal_id}/candidate", {
      params: { path: { project_id: projectId, run_id: runId, proposal_id: proposalId } },
    }), "選択した変更案を候補にできませんでした。");
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
    }), "指定した編集版を参照できませんでした。");
  },
  async candidateBlendMaterials(projectId: string, candidateId: string, revision?: number) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/blend-materials", {
      params: {
        path: { project_id: projectId, candidate_id: candidateId },
        query: { revision },
      },
    }), "原料情報を取得できませんでした。");
  },
  async blendOptimizationContext(projectId: string, candidateId: string, expectedRevision: number) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/blend-optimization", {
      params: {
        path: { project_id: projectId, candidate_id: candidateId },
        query: { expected_revision: expectedRevision },
      },
    }), "配合逆算の条件を取得できませんでした。");
  },
  async runBlendOptimization(projectId: string, candidateId: string, body: ApiBlendOptimizationRequest) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/blend-optimization", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      body,
    }), "配合逆算を実行できませんでした。");
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
  async candidateOriginEvidence(projectId: string, candidateId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/origin-evidence", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      signal,
    }), "作成元実測を取得できませんでした。");
  },
  async historicalObservations(projectId: string, signal?: AbortSignal): Promise<ApiHistoricalObservationList> {
    return requireData(await apiClient.GET("/api/projects/{project_id}/historical-observations", {
      params: { path: { project_id: projectId } },
      signal,
    }), "過去の実測recordを取得できませんでした。");
  },
  async createCandidateFromHistoricalObservation(projectId: string, observationId: string): Promise<ApiHistoricalObservationCandidate> {
    return requireData(await apiClient.POST("/api/projects/{project_id}/historical-observations/{observation_id}/candidate", {
      params: { path: { project_id: projectId, observation_id: observationId } },
    }), "過去の実測recordから候補を作成できませんでした。");
  },
  async historicalObservationEvidence(projectId: string, candidateId: string, signal?: AbortSignal): Promise<ApiHistoricalObservationEvidence> {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/historical-evidence", {
      params: { path: { project_id: projectId, candidate_id: candidateId } },
      signal,
    }), "候補化した過去の実測recordを取得できませんでした。");
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
  async proposalStrategies(projectId: string, target: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/proposal-strategies", {
      params: { path: { project_id: projectId }, query: { target } },
    }), "提案戦略を取得できませんでした。");
  },
  async proposalLabReports(projectId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/proposal-lab/reports", {
      params: { path: { project_id: projectId } },
    }), "Proposal Labの評価記録を取得できませんでした。");
  },
  async createProposalLabReport(projectId: string, body: ApiProposalLabCreateRequest) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/proposal-lab/reports", {
      params: { path: { project_id: projectId } },
      body,
    }), "Proposal Labの評価記録を保存できませんでした。");
  },
  async createScreeningRun(projectId: string, body: ApiScreeningRequest) {
    return requireData(await apiClient.POST("/api/screening", { params: { query: { project_id: projectId } }, body }), "範囲探索を実行できませんでした。");
  },
  async screeningRun(projectId: string, runId: string) {
    return requireData(await apiClient.GET("/api/screening/{run_id}", { params: { path: { run_id: runId }, query: { project_id: projectId } } }), "保存済み探索を開けませんでした。");
  },
  async deleteScreeningRun(projectId: string, runId: string) {
    requireSuccess(await apiClient.DELETE("/api/screening/{run_id}", { params: { path: { run_id: runId }, query: { project_id: projectId } } }), "保存済み探索を削除できませんでした。");
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
  lineageEvidenceImageUrl(projectId: string, entityKey: string) {
    return apiDownloadUrl(
      `${path(projectId, `/lineage/${encodeURIComponent(entityKey)}/evidence-image`)}`,
    );
  },
};
