import { readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const learningRoot = resolve(dirname(fileURLToPath(import.meta.url)));
const repositoryRoot = resolve(learningRoot, "..", "..");
const manifest = JSON.parse(readFileSync(join(learningRoot, "labs", "manifest.json"), "utf8"));
const report = JSON.parse(readFileSync(join(learningRoot, "labs", "reproducibility.json"), "utf8"));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

assert(report.schema_version === "learning-lab-reproducibility/v1", "Unsupported report schema.");
assert(report.labs.length === manifest.labs.length, "Report must cover every Lab.");
assert(new Set(report.labs.map((lab) => lab.lab_id)).size === manifest.labs.length, "Report Lab IDs must be unique.");
for (const lab of manifest.labs) {
  const measured = report.labs.find((entry) => entry.lab_id === lab.lab_id);
  assert(measured, `Missing measurement for ${lab.lab_id}.`);
  assert(measured.mode === lab.mode, `${lab.lab_id}: mode drift.`);
  const actualBytes = measured.setup_payload_files.reduce(
    (sum, path) => sum + statSync(join(repositoryRoot, ...path.split("/"))).size,
    0,
  );
  assert(actualBytes === measured.setup_payload_bytes, `${lab.lab_id}: setup payload size is stale.`);
  if (lab.mode === "executable") {
    assert(measured.execution.cycles.length === 2, `${lab.lab_id}: two cycles are required.`);
    assert(measured.execution.reproducible === true, `${lab.lab_id}: result hashes differ.`);
    assert(
      new Set(measured.execution.cycles.map((cycle) => cycle.result_hash)).size === 1,
      `${lab.lab_id}: cycle hashes differ.`,
    );
  } else {
    assert(measured.execution.cycles.length === 0, `${lab.lab_id}: guided Lab cannot claim machine cycles.`);
    assert(measured.execution.reproducible === null, `${lab.lab_id}: guided reproducibility must remain unclassified.`);
  }
}
assert(report.summary.lab_count === manifest.labs.length, "Summary Lab count is stale.");
console.log(`Lab reproducibility validation passed: ${report.labs.length} Labs measured.`);
