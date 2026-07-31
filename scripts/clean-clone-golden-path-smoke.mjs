import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";


const repositoryRoot = resolve(
  fileURLToPath(new URL("..", import.meta.url)),
);
const branch = execFileSync(
  "git",
  ["branch", "--show-current"],
  { cwd: repositoryRoot, encoding: "utf8" },
).trim();
if (!branch) {
  throw new Error("clean-clone smoke requires a named local branch");
}

const temporaryRoot = mkdtempSync(join(tmpdir(), "decision-workbench-clean-clone-"));
const checkout = join(temporaryRoot, "checkout");
const run = (command, args) => {
  const options = {
    cwd: checkout,
    env: { ...process.env, PYTHONUTF8: "1" },
    stdio: "inherit",
  };
  if (process.platform === "win32" && command.endsWith(".cmd")) {
    return execFileSync(
      process.env.ComSpec ?? "cmd.exe",
      ["/d", "/s", "/c", [command, ...args].join(" ")],
      options,
    );
  }
  return execFileSync(command, args, options);
};

try {
  execFileSync(
    "git",
    [
      "clone",
      "--no-hardlinks",
      "--single-branch",
      "--branch",
      branch,
      repositoryRoot,
      checkout,
    ],
    { stdio: "inherit" },
  );
  run("uv", ["sync", "--extra", "dev"]);
  run("npm.cmd", ["ci"]);
  run("npm.cmd", ["run", "model:golden-path:smoke"]);
  run("uv", [
    "run",
    "pytest",
    "backend/tests/test_model_workflow_golden_path.py",
    "-q",
  ]);
  process.stdout.write(
    JSON.stringify({
      ok: true,
      branch,
      steps: [
        "git clone",
        "uv sync --extra dev",
        "npm ci",
        "model:golden-path:smoke",
        "model workflow API smoke",
      ],
    }) + "\n",
  );
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
}
