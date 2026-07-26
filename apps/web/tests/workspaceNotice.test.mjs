import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { noticeRole, noticeTimeoutMs, SUCCESS_NOTICE_TIMEOUT_MS } from "../src/shared/workspaceNotice.ts";

// Working copies may be CRLF; assertions are authored with LF.
const source = async (relativePath) => {
  const content = await readFile(new URL(relativePath, import.meta.url), "utf8");
  return content.split("\r\n").join("\n");
};

test("a failure is announced as an alert and never expires on its own", () => {
  assert.equal(noticeRole("error"), "alert");
  assert.equal(noticeTimeoutMs("error"), null);
});

test("a success is a receipt: announced politely and withdrawn on a timer", () => {
  assert.equal(noticeRole("success"), "status");
  assert.equal(noticeTimeoutMs("success"), SUCCESS_NOTICE_TIMEOUT_MS);
  assert.ok(SUCCESS_NOTICE_TIMEOUT_MS >= 3000 && SUCCESS_NOTICE_TIMEOUT_MS <= 8000);
});

test("the notice banner carries its role, a close control and the success timer", async () => {
  const banner = await source("../src/shared/ui/WorkspaceNoticeBanner.tsx");
  assert.match(banner, /role=\{noticeRole\(notice\.kind\)\}/);
  assert.match(banner, /aria-label="通知を閉じる"/);
  assert.match(banner, /window\.setTimeout\(onDismiss, timeout\)/);
  assert.match(banner, /notice\.kind\}`/);
});

test("a preview failure never becomes the connection state", async () => {
  const session = await source("../src/features/workbench/useWorkbenchSession.ts");
  assert.doesNotMatch(session, /API未接続: 予測結果は表示できません/);
  // The initial preview stage is isolated from the load that owns apiState.
  assert.match(session, /try \{\n\s+const activeCandidates = imported\.filter/);
  assert.match(session, /\} catch \{\n\s+\/\/ Keep the loaded project usable/);
});

test("a failed write reports itself without claiming the API is unreachable", async () => {
  const session = await source("../src/features/workbench/useWorkbenchSession.ts");
  assert.doesNotMatch(session, /setApiState\("offline"\);\s*\n\s*notifyError/);
  assert.equal((session.match(/setApiState\("offline"\)/g) ?? []).length, 1);
});

test("opening a project reports no operation result", async () => {
  const session = await source("../src/features/workbench/useWorkbenchSession.ts");
  assert.doesNotMatch(session, /Chain Revisionを固定しました/);
  assert.doesNotMatch(session, /候補がありません。過去条件または新規入力から追加できます/);
});

test("every notice declares whether it reports success or failure", async () => {
  const files = [
    "../src/features/workbench/useWorkbenchSession.ts",
    "../src/features/workbench/useWorkbenchPrediction.ts",
    "../src/features/candidates/useCandidateEditor.ts",
    "../src/app/App.tsx",
  ];
  for (const file of files) {
    const content = await source(file);
    assert.doesNotMatch(content, /onNotice\(/, `${file} still uses an untyped notice`);
    assert.doesNotMatch(content, /session\.setNotice\(/, `${file} still sets a bare notice`);
  }
});

test("the shell renders one notice banner instead of a bare status div", async () => {
  const app = await source("../src/app/App.tsx");
  assert.match(app, /<WorkspaceNoticeBanner notice=\{notice\} onDismiss=\{session\.dismissNotice\}/);
  assert.doesNotMatch(app, /className="workspace-notice"/);
  assert.doesNotMatch(app, /notice !== preview\?\.support\.message/);
});

test("the notice surface is interactive and separates failure from success", async () => {
  const styles = await source("../src/styles.css");
  assert.doesNotMatch(styles, /\.workspace-notice \{[^}]*pointer-events: none/);
  assert.match(styles, /\.workspace-notice\.error \{/);
  assert.match(styles, /\.workspace-notice > button \{/);
});

test("an unopened workspace states one recovery action instead of loading forever", async () => {
  const app = await source("../src/app/App.tsx");
  const hub = await source("../src/features/projects/ProjectHub.tsx");
  const session = await source("../src/features/workbench/useWorkbenchSession.ts");
  assert.match(app, /apiState === "offline" && <ConnectionBanner/);
  assert.match(app, /session\.retryOpenWorkspace\(\)/);
  assert.match(session, /async function retryOpenWorkspace\(\)/);
  // The history reports failure, and recovers with the workspace.
  assert.match(hub, /historyState === "error" \? <div className="project-history-error"/);
  assert.match(hub, /履歴を再取得/);
  // Dependency lists get reformatted; the recovery dependency itself must stay.
  assert.match(hub, /Recovering the workspace also recovers this overview[\s\S]{0,300}\boffline\b/);
});

test("changes are inert while the workspace is offline", async () => {
  const hub = await source("../src/features/projects/ProjectHub.tsx");
  assert.doesNotMatch(hub, /disabled=\{taskUnavailable\}/);
  assert.doesNotMatch(hub, /disabled=\{chainExecutionPending\}/);
  assert.match(hub, /disabled=\{taskUnavailable \|\| chainExecutionPending \|\| offline\}/);
  assert.match(hub, /disabled=\{createOpen \|\| offline\}/);
  assert.match(hub, /className="danger-outline-button" disabled=\{offline\}/);
});
