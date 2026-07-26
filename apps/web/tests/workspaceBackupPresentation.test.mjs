import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = (relativePath) => readFile(new URL(relativePath, import.meta.url), "utf8");

test("Workspace backup and restore stay global and explain the atomic switch", async () => {
  const app = await source("../src/app/App.tsx");
  const dialog = await source("../src/features/workspace/WorkspaceManagerDialog.tsx");
  const preload = await source("../../desktop/src/preload.ts");

  assert.match(app, />\s*ワークスペース\s*</);
  assert.match(app, /WorkspaceManagerDialog/);
  assert.match(dialog, /バックアップを作る/);
  assert.match(dialog, /バックアップから復元/);
  assert.match(dialog, /失敗した場合は現在の内容へ戻します/);
  assert.match(dialog, /Project、候補、固定snapshot、実測、Chain証拠/);
  assert.match(preload, /workbench:workspace-export/);
  assert.match(preload, /workbench:workspace-prepare-restore/);
  assert.match(preload, /workbench:workspace-confirm-restore/);
  assert.doesNotMatch(preload, /destination|sourcePath|filePath/);
});
