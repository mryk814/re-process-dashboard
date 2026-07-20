import type { components } from "../../generated/api-types";
import { apiClient, apiDownloadUrl, requireData, requireSuccess } from "./client";
import { candidateInferencePrefix, inferenceRequestCache, inferenceRequestKey } from "./inferenceRequestCache";

export type ApiCandidate = components["schemas"]["Candidate"];
export type ApiCandidateInput = components["schemas"]["CandidateInput"];
export type ApiCandidateUpdate = components["schemas"]["CandidateUpdate"];
export type ApiProject = components["schemas"]["Project"];
export type ApiProjectInput = components["schemas"]["ProjectInput"];
export type ApiProjectCreateInput = components["schemas"]["ProjectCreateInput"];
export type ApiModelPackage = components["schemas"]["ModelPackageStatus"];
export type ApiPreview = components["schemas"]["PredictionResponse"];
export type ApiSnapshot = components["schemas"]["SnapshotResponse"];
export type ApiActual = components["schemas"]["ActualMeasurement"];
export type ApiActualInput = components["schemas"]["ActualMeasurementInput"];
export type ApiPredictionVsActual = components["schemas"]["PredictionVsActualResponse"];
export type ApiResponseCurves = components["schemas"]["ResponseCurvesResponse"];
export type ApiQuality = components["schemas"]["QualityResponse"];
export type ApiLineage = components["schemas"]["LineageResponse"];
export type ApiLineageIndex = components["schemas"]["LineageIndexResponse"];
export type ApiScreeningRequest = components["schemas"]["ScreeningRequest"];
export type ApiScreeningRun = components["schemas"]["ScreeningRunResponse"];
export type ApiScreeningCandidateBatch = components["schemas"]["ScreeningCandidateBatchResponse"];
export type ApiTaskDefinition = components["schemas"]["ResolvedTaskDefinition"];
export type ApiTaskCatalogItem = components["schemas"]["TaskCatalogItem"];
export type ApiProjectHistory = components["schemas"]["ProjectHistoryResponse"];
export type ApiProjectDecisionInput = components["schemas"]["ProjectDecisionInput"];

const path = (projectId: string, suffix = "") =>
  `/api/projects/${encodeURIComponent(projectId)}${suffix}`;

function isResponseCurves(value: unknown): value is ApiResponseCurves {
  return typeof value === "object" && value !== null
    && typeof Reflect.get(value, "variable") === "object"
    && typeof Reflect.get(value, "curves") === "object"
    && typeof Reflect.get(value, "output_ranges") === "object";
}

export const workbenchApi = {
  async listProjects() {
    return requireData(await apiClient.GET("/api/projects"), "プロジェクトを取得できませんでした。");
  },
  async listTaskDefinitions() {
    return requireData(await apiClient.GET("/api/task-definitions"), "予測タスクを取得できませんでした。");
  },
  async projectHistory(projectId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/history", { params: { path: { project_id: projectId } }, signal }), "プロジェクト履歴を取得できませんでした。");
  },
  async createProject(body: ApiProjectCreateInput) {
    return requireData(await apiClient.POST("/api/projects", { body }), "プロジェクトを作成できませんでした。");
  },
  async updateProject(projectId: string, body: ApiProjectInput) {
    const project = requireData(await apiClient.PUT("/api/projects/{project_id}", { params: { path: { project_id: projectId } }, body }), "プロジェクトを保存できませんでした。");
    inferenceRequestCache.invalidatePrefix(candidateInferencePrefix(projectId));
    return project;
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
  async responseCurves(projectId: string, candidateId: string, expectedRevision: number, inputIdentity: string, variable?: string, signal?: AbortSignal): Promise<ApiResponseCurves> {
    const data = await inferenceRequestCache.get(
      inferenceRequestKey(projectId, candidateId, inputIdentity, "curve", variable ?? ""),
      async (sharedSignal) => requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/response-curves", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { expected_revision: expectedRevision, variable } }, signal: sharedSignal }), "応答曲線を取得できませんでした。"),
      signal,
    );
    if (isResponseCurves(data)) return data;
    throw new Error("設計変数を指定した応答曲線の形式が不正です。");
  },
  async similarCandidates(projectId: string, candidateId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/similar", { params: { path: { project_id: projectId, candidate_id: candidateId } } }), "類似実験を取得できませんでした。");
  },
  async snapshots(projectId: string, candidateId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/snapshots", { params: { path: { project_id: projectId, candidate_id: candidateId } }, signal }), "スナップショットを取得できませんでした。");
  },
  async restoreSnapshot(projectId: string, snapshotId: string) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/snapshots/{snapshot_id}/restore", { params: { path: { project_id: projectId, snapshot_id: snapshotId } } }), "スナップショットを復元できませんでした。");
  },
  async candidate(projectId: string, candidateId: string, includeArchived = false) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { include_archived: includeArchived } } }), "候補を参照できませんでした。");
  },
  async snapshot(projectId: string, snapshotId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/snapshots/{snapshot_id}", { params: { path: { project_id: projectId, snapshot_id: snapshotId } }, signal }), "保存済み予測を参照できませんでした。");
  },
  async predictionVsActual(projectId: string, candidateId: string, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/prediction-vs-actual", { params: { path: { project_id: projectId, candidate_id: candidateId } }, signal }), "予測と実測を取得できませんでした。");
  },
  async createActual(projectId: string, candidateId: string, expectedRevision: number, body: ApiActualInput) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/actuals", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { expected_revision: expectedRevision } }, body }), "実測を保存できませんでした。");
  },
  async deleteActual(projectId: string, candidateId: string, actualId: string) {
    requireSuccess(await apiClient.DELETE("/api/projects/{project_id}/candidates/{candidate_id}/actuals/{actual_id}", { params: { path: { project_id: projectId, candidate_id: candidateId, actual_id: actualId } } }), "実測を削除できませんでした。");
  },
  async quality() {
    return requireData(await apiClient.GET("/api/quality"), "データ品質を取得できませんでした。");
  },
  async qualityCsv() {
    return requireData(await apiClient.GET("/api/quality/export.csv", { parseAs: "text" }), "データ品質CSVを取得できませんでした。");
  },
  async lineageIndex(query: string, entityType: string, issueOnly: boolean, signal?: AbortSignal) {
    return requireData(await apiClient.GET("/api/lineage", { params: { query: { query, entity_type: entityType, issue_only: issueOnly, limit: 40 } }, signal }), "工程系譜を検索できませんでした。");
  },
  async lineage(entityKey: string, limit = 40) {
    return requireData(await apiClient.GET("/api/lineage/{entity_key}", { params: { path: { entity_key: entityKey }, query: { limit } } }), "系譜を取得できませんでした。");
  },
  async createCandidateFromLineage(entityKey: string, projectId: string) {
    return requireData(await apiClient.POST("/api/lineage/{entity_key}/candidate", { params: { path: { entity_key: entityKey }, query: { project_id: projectId } } }), "候補を作成できませんでした。");
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
};
