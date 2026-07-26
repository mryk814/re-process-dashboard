import { expect, type APIRequestContext } from "@playwright/test";

/**
 * Every spec must resolve the API port the same way playwright.config.ts does.
 * Hard-coding 8875 makes the suite fail whenever the default port is taken.
 */
export const apiBaseUrl = `http://127.0.0.1:${process.env.PLAYWRIGHT_API_PORT ?? "8875"}`;

type CreationOptions = {
  datasets: Array<{
    data_asset: { sha256: string };
    profile_revision: { profile_digest: string };
    dataset_views: Array<{ id: string }>;
  }>;
  model_packages: Array<{
    id: string;
    package_id: string;
    task_id: string;
    task_contract_digest: string;
    manifest_digest: string;
    manifest_json: { provenance: { training_data_id: string; dataset_profile_id: string } };
  }>;
  task_contract_digests: Record<string, string>;
};

export type ProjectBinding = {
  task_id: string;
  dataset_view_revision_id: string;
  model_package_ref_id: string;
  task_contract_digest: string;
  model_package_manifest_digest: string;
};

/**
 * A Project pins the exact Dataset View and Model Package it was created against,
 * so creation requires those references. Resolve them the same way the UI does:
 * a package is usable only if it was trained on that dataset and matches the
 * current task contract.
 */
export async function resolveProjectBinding(
  request: APIRequestContext,
  taskId: string,
  packageId?: string,
): Promise<ProjectBinding> {
  const response = await request.get(`${apiBaseUrl}/api/project-creation-options`);
  expect(response.status(), "project-creation-options").toBe(200);
  const options = await response.json() as CreationOptions;
  const digest = options.task_contract_digests[taskId];
  expect(digest, `task contract digest for ${taskId}`).toBeTruthy();

  for (const dataset of options.datasets) {
    const view = dataset.dataset_views[0];
    if (!view) continue;
    const modelPackage = options.model_packages.find((item) => (
      item.task_id === taskId
      && item.task_contract_digest === digest
      && item.manifest_json.provenance.training_data_id === `sha256:${dataset.data_asset.sha256}`
      && item.manifest_json.provenance.dataset_profile_id === dataset.profile_revision.profile_digest
      // A task can have packages trained on several datasets; a spec that asserts
      // concrete keys has to say which dataset it means.
      && (packageId === undefined || item.package_id === packageId)
    ));
    if (!modelPackage) continue;
    return {
      task_id: taskId,
      dataset_view_revision_id: view.id,
      model_package_ref_id: modelPackage.id,
      task_contract_digest: digest,
      model_package_manifest_digest: modelPackage.manifest_digest,
    };
  }
  throw new Error(`no dataset view and model package pair is available for ${taskId}${packageId ? ` (${packageId})` : ""}`);
}

export async function createProjectWithBinding(
  request: APIRequestContext,
  taskId: string,
  name: string,
  packageId?: string,
) {
  const binding = await resolveProjectBinding(request, taskId, packageId);
  const created = await request.post(`${apiBaseUrl}/api/projects`, { data: { name, ...binding } });
  expect(created.status(), await created.text()).toBe(201);
  return await created.json() as { id: string };
}

export async function starterCandidate(request: APIRequestContext, taskId: string) {
  const response = await request.get(`${apiBaseUrl}/api/task-definitions`);
  expect(response.status(), "task-definitions").toBe(200);
  const catalog = await response.json() as Array<{
    definition: { task_definition: { id: string } };
    starter_candidate: Record<string, unknown>;
  }>;
  const task = catalog.find((item) => item.definition.task_definition.id === taskId);
  expect(task, `starter candidate for ${taskId}`).toBeTruthy();
  return task!.starter_candidate;
}

export async function createProjectWithCandidate(
  request: APIRequestContext,
  taskId: string,
  projectName: string,
  candidateName: string,
) {
  const starter = await starterCandidate(request, taskId);
  const project = await createProjectWithBinding(request, taskId, projectName);
  const candidate = await request.post(`${apiBaseUrl}/api/projects/${project.id}/candidates`, {
    data: { ...starter, name: candidateName },
  });
  expect(candidate.status(), await candidate.text()).toBe(201);
  return project;
}
