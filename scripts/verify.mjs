import { spawnSync } from "node:child_process";

const mode = process.argv[2];
const forwarded = process.argv.slice(3);
if (forwarded[0] === "--") forwarded.shift();
const npmCli = process.env.npm_execpath;

function run(label, command, args) {
  const grouped = process.env.GITHUB_ACTIONS === "true";
  process.stdout.write(grouped ? `::group::${label}\n` : `\n== ${label} ==\n`);
  const result = spawnSync(command, args, { stdio: "inherit", env: process.env });
  if (grouped) process.stdout.write("::endgroup::\n");
  if (result.error) {
    process.stderr.write(`${label}: ${result.error.message}\n`);
    process.exit(1);
  }
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function runNpm(label, args) {
  if (npmCli) run(label, process.execPath, [npmCli, ...args]);
  else run(label, "npm", args);
}

if (mode === "focused") {
  if (forwarded.length === 0) {
    process.stderr.write(
      "focused testを指定してください。例: npm.cmd run verify:focused -- backend/tests/test_screening_score.py\n",
    );
    process.exit(2);
  }
  run("focused pytest", "uv", ["run", "python", "-m", "pytest", ...forwarded]);
  runNpm("TypeScript typecheck", ["run", "typecheck"]);
} else if (mode === "full") {
  const baseRef = process.env.VERIFY_BASE_REF || "origin/main";
  run("full pytest", "uv", ["run", "python", "-m", "pytest"]);
  runNpm("web unit tests", ["run", "test", "-w", "apps/web"]);
  runNpm("TypeScript typecheck", ["run", "typecheck"]);
  runNpm("application build", ["run", "build"]);
  run("working-tree diff check", "git", ["diff", "--check"]);
  run(`branch diff check (${baseRef}...HEAD)`, "git", ["diff", "--check", `${baseRef}...HEAD`]);
} else {
  process.stderr.write("verify mode must be focused or full\n");
  process.exit(2);
}
