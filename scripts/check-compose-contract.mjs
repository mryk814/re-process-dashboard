import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const repositoryRoot = resolve(import.meta.dirname, "..");
const composePath = resolve(repositoryRoot, "compose.yaml");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function composeConfig() {
  const result = spawnSync(
    "docker",
    ["compose", "--profile", "infra", "--profile", "test", "config", "--format", "json"],
    { cwd: repositoryRoot, encoding: "utf8" },
  );
  if (result.status !== 0) {
    throw new Error(`docker compose config failed:\n${result.stderr || result.stdout}`);
  }
  return JSON.parse(result.stdout);
}

function profile(service, expected) {
  assert(
    service.profiles?.length === 1 && service.profiles[0] === expected,
    `${service.image}: expected only profile ${expected}.`,
  );
}

function pinnedImage(service, expectedPrefix) {
  assert(service.image?.startsWith(expectedPrefix), `Expected image ${expectedPrefix}*, found ${service.image}.`);
  assert(!service.image.endsWith(":latest"), `${service.image}: latest tags are forbidden.`);
}

const config = composeConfig();
const services = config.services;
const required = [
  "postgres",
  "object-storage",
  "bucket-init",
  "migration",
  "object-smoke",
  "postgres-test",
  "object-storage-test",
  "bucket-init-test",
  "migration-test",
  "integration-smoke",
];
assert(required.every((name) => services[name]), "Compose service inventory is incomplete.");
assert(Object.keys(services).length === required.length, "Unexpected Compose service added; review its boundary.");

for (const name of ["postgres", "object-storage", "bucket-init", "migration", "object-smoke"]) {
  profile(services[name], "infra");
}
for (const name of ["postgres-test", "object-storage-test", "bucket-init-test", "migration-test", "integration-smoke"]) {
  profile(services[name], "test");
}

for (const name of ["postgres", "migration", "postgres-test", "migration-test"]) {
  pinnedImage(services[name], "postgres:17.10-alpine");
}
for (const name of ["object-storage", "object-storage-test"]) {
  pinnedImage(services[name], "minio/minio:RELEASE.2025-09-07T16-13-09Z");
}
for (const name of ["bucket-init", "object-smoke", "bucket-init-test", "integration-smoke"]) {
  pinnedImage(services[name], "minio/mc:RELEASE.2025-08-13T08-35-41Z-cpuv1");
}

assert(services.postgres.healthcheck, "Persistent PostgreSQL needs a healthcheck.");
assert(services["object-storage"].healthcheck, "Persistent object storage needs a healthcheck.");
assert(services["postgres-test"].healthcheck, "Ephemeral PostgreSQL needs a healthcheck.");
assert(services["object-storage-test"].healthcheck, "Ephemeral object storage needs a healthcheck.");
assert(
  services.postgres.ports.every((port) => port.host_ip === "127.0.0.1") &&
    services["object-storage"].ports.every((port) => port.host_ip === "127.0.0.1"),
  "Persistent infrastructure ports must bind only to loopback.",
);
assert(
  services.postgres.volumes.some((volume) => volume.type === "volume") &&
    services["object-storage"].volumes.some((volume) => volume.type === "volume"),
  "infra profile must use named persistent volumes.",
);
assert(
  services["postgres-test"].tmpfs?.includes("/var/lib/postgresql/data") &&
    services["object-storage-test"].tmpfs?.includes("/data"),
  "test profile must keep state in tmpfs.",
);
assert(
  services.migration.depends_on?.postgres?.condition === "service_healthy" &&
    services["bucket-init"].depends_on?.["object-storage"]?.condition === "service_healthy",
  "Initialization must wait for healthy dependencies.",
);
assert(
  services["integration-smoke"].depends_on?.["migration-test"]?.condition === "service_completed_successfully" &&
    services["integration-smoke"].depends_on?.["bucket-init-test"]?.condition === "service_completed_successfully",
  "Integration smoke must require successful migration and bucket initialization.",
);

const migration = readFileSync(
  resolve(repositoryRoot, "infrastructure", "compose", "migrations", "001_shared_fixture.sql"),
  "utf8",
);
for (const table of [
  "schema_migrations",
  "workspaces",
  "actors",
  "projects",
  "candidate_revisions",
  "activity_runs",
  "review_runs",
  "artifact_references",
]) {
  assert(migration.includes(`workbench_shared.${table}`), `Migration is missing ${table}.`);
}
assert(migration.includes("ON CONFLICT (version) DO NOTHING"), "Migration must be repeatable.");
assert(
  migration.includes("FOREIGN KEY (project_id, candidate_id, candidate_revision)"),
  "Activity Run must bind Candidate Revision within the same Project.",
);

const objectSmoke = readFileSync(
  resolve(repositoryRoot, "infrastructure", "compose", "scripts", "object-smoke.sh"),
  "utf8",
);
for (const evidence of ["sha256sum", "mc stat", "mc pipe", "mc cat", "roundtrip_digest"]) {
  assert(objectSmoke.includes(evidence), `Object smoke is missing ${evidence}.`);
}

const envExample = readFileSync(resolve(repositoryRoot, ".env.example"), "utf8");
for (const key of [
  "WORKBENCH_PERSISTENCE_BACKEND=sqlite",
  "WORKBENCH_ARTIFACT_BACKEND=local",
  "WORKBENCH_DATABASE_URL=postgresql://",
  "WORKBENCH_POSTGRES_PASSWORD=",
  "WORKBENCH_S3_SECRET_KEY=",
]) {
  assert(envExample.includes(key), `.env.example is missing ${key}.`);
}
assert(!readFileSync(composePath, "utf8").includes("data/source"), "Compose must not mount the read-only source data.");
assert(!readFileSync(composePath, "utf8").includes("models/packages"), "Compose must not mount Model Packages.");
const integrationRunner = readFileSync(
  resolve(repositoryRoot, "scripts", "run-compose-integration.mjs"),
  "utf8",
);
assert(
  integrationRunner.includes('"--project-name"')
  && integrationRunner.includes("material-workbench-test-${process.pid}"),
  "Integration profile must use a process-isolated Compose project.",
);
assert(
  !integrationRunner.includes('"--volumes"'),
  "Ephemeral test cleanup must not target persistent infra volumes.",
);

console.log(`Compose contract passed: ${required.length} services, persistent infra and ephemeral test profiles.`);
