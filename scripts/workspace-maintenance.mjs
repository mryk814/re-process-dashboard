import { spawnSync } from "node:child_process";
import { resolveDevWorkspace } from "./dev-workspace.mjs";

const mainWorkspace = process.argv.includes("--main-workspace");
const workspace = resolveDevWorkspace({ mainWorkspace });
const forwarded = process.argv
  .slice(2)
  .filter((value) => value !== "--main-workspace");
const result = spawnSync(
  "uv",
  [
    "run",
    "python",
    "backend/scripts/workspace_maintenance.py",
    "--database",
    workspace.database,
    ...forwarded,
  ],
  {
    cwd: workspace.repositoryRoot,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
    },
    stdio: "inherit",
  },
);
if (result.error) throw result.error;
process.exit(result.status ?? 1);
