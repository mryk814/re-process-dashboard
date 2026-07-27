import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const learningRoot = path.dirname(fileURLToPath(import.meta.url));
const checker = path.join(learningRoot, "check-concepts.mjs");

for (const args of [["--self-test"], []]) {
  const result = spawnSync(process.execPath, [checker, ...args], {
    cwd: path.resolve(learningRoot, "..", ".."),
    encoding: "utf8",
  });
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  if (result.status !== 0) {
    throw new Error(`Concept checker failed with exit code ${result.status}.`);
  }
}

console.log("Concept source and generated pages passed regression checks.");
