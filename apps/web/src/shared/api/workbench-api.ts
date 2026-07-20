import type { components } from "../../generated/api-types";
import { apiClient, apiDownloadUrl, requireData, requireSuccess } from "./client";

export type ApiCandidate = components["schemas"]["Candidate"];
export type ApiCandidateInput = components["schemas"]["CandidateInput"];
export type ApiCandidateUpdate = components["schemas"]["CandidateUpdate"];
export type ApiProject = components["schemas"]["Project"];
export type ApiProjectInput = components["schemas"]["ProjectInput"];
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
export type ApiTaskDefinition = components["schemas"]["ResolvedTaskDefinition"];

const path = (projectId: string, suffix = "") =>
  `/api/projects/${encodeURIComponent(projectId)}${suffix}`;

export const workbenchApi = {
  async listProjects() {
    return requireData(await apiClient.GET("/api/projects"), "プロジェクトを取得できませんでした。");
  },
  async createProject(body: ApiProjectInput) {
    return requireData(await apiClient.POST("/api/projects", { body }), "プロジェクトを作成できませんでした。");
  },
  async updateProject(projectId: string, body: ApiProjectInput) {
    return requireData(await apiClient.PUT("/api/projects/{project_id}", { params: { path: { project_id: projectId } }, body }), "プロジェクトを保存できませんでした。");
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
  },
  async previewCandidate(projectId: string, candidateId: string) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/preview", { params: { path: { project_id: projectId, candidate_id: candidateId } } }), "プレビューを取得できませんでした。");
  },
  async predictCandidate(projectId: string, candidateId: string) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/predict", { params: { path: { project_id: projectId, candidate_id: candidateId } } }), "詳細予測を取得できませんでした。");
  },
  async responseCurves(projectId: string, candidateId: string, variable?: string, signal?: AbortSignal) {
    const data = requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/response-curves", { params: { path: { project_id: projectId, candidate_id: candidateId }, query: { variable } }, signal }), "応答曲線を取得できませんでした。");
    if ("variable" in data && "curves" in data && "output_ranges" in data) return data;
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
  async predictionVsActual(projectId: string, candidateId: string) {
    return requireData(await apiClient.GET("/api/projects/{project_id}/candidates/{candidate_id}/prediction-vs-actual", { params: { path: { project_id: projectId, candidate_id: candidateId } } }), "予測と実測を取得できませんでした。");
  },
  async createActual(projectId: string, candidateId: string, body: ApiActualInput) {
    return requireData(await apiClient.POST("/api/projects/{project_id}/candidates/{candidate_id}/actuals", { params: { path: { project_id: projectId, candidate_id: candidateId } }, body }), "実測を保存できませんでした。");
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
  async lineage(entityKey: string) {
    return requireData(await apiClient.GET("/api/lineage/{entity_key}", { params: { path: { entity_key: entityKey } } }), "系譜を取得できませんでした。");
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
  async candidateFromScreening(projectId: string, runId: string, pointIndex: number) {
    return requireData(await apiClient.POST("/api/screening/{run_id}/points/{point_index}/candidate", { params: { path: { run_id: runId, point_index: pointIndex }, query: { project_id: projectId } } }), "候補を作成できませんでした。");
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
