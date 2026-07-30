import assert from "node:assert/strict";
import { join, resolve } from "node:path";
import { _electron as electron } from "playwright";

const repositoryRoot = resolve(import.meta.dirname, "..");
const appRoot = resolve(
  process.argv[2]
    ?? join(repositoryRoot, "release", "smoke", "installed"),
);
process.env.LOCALAPPDATA = join(
  repositoryRoot,
  "release",
  "smoke",
  "local-app-data",
);
const executablePath = join(appRoot, "Evidence Decision Workbench.exe");
const timeout = 120_000;

const electronApp = await electron.launch({ executablePath, timeout });
try {
  const window = await electronApp.firstWindow({ timeout });
  await window.getByRole(
    "heading",
    { name: "ワークスペース", exact: true },
  ).waitFor({ timeout });
  assert.equal(await window.title(), "Evidence Decision Workbench");

  const runtime = await window.evaluate(() => window.workbenchDesktop);
  assert(runtime?.apiBaseUrl);
  assert(runtime.launchToken);
  const projectResponse = await fetch(`${runtime.apiBaseUrl}/api/projects/default`, {
    headers: { "X-Workbench-Launch-Token": runtime.launchToken },
  });
  assert.equal(projectResponse.status, 200);
  assert.equal(
    (await projectResponse.json()).notes,
    "packaged-smoke-portable-before-backup",
  );
} finally {
  await electronApp.close();
}

console.log("Installer upgrade preserved the legacy Workspace path and data: OK");
