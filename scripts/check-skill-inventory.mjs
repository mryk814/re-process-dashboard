import { createHash } from "node:crypto";
import {
  existsSync,
  lstatSync,
  readFileSync,
  readdirSync,
  realpathSync,
} from "node:fs";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from "node:path";
import { fileURLToPath } from "node:url";

export const INVENTORY_SCHEMA = "skill-inventory/v1";
export const DEFAULT_INVENTORY_PATH = ".agents/skill-inventory.json";

const HEX_SHA256 = /^[a-f0-9]{64}$/;
const FULL_COMMIT = /^[a-f0-9]{40}$/;
const CLIENT_STATES = new Set(["observed", "unknown", "unsupported"]);
const LOCAL_ROOTS = [".agents", ".claude"];

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function normalizeRelativePath(value) {
  return typeof value === "string" ? value.replaceAll("\\", "/") : value;
}

function displayPath(repoRoot, absolutePath) {
  return normalizeRelativePath(relative(repoRoot, absolutePath)) || ".";
}

function isWithin(rootPath, targetPath) {
  const rel = relative(rootPath, targetPath);
  return rel === "" || (rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel));
}

function resolveWithin(repoRoot, relativePath) {
  if (typeof relativePath !== "string" || relativePath.length === 0) return null;
  const target = resolve(repoRoot, relativePath);
  return isWithin(resolve(repoRoot), target) ? target : null;
}

function hashBytes(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function hashFile(path) {
  return hashBytes(readFileSync(path));
}

function finding(code, message, details = {}) {
  return { code, severity: "error", message, ...details };
}

function warning(code, message, details = {}) {
  return { code, severity: "warning", message, ...details };
}

function frontMatterName(markdown) {
  const match = markdown.match(/^---\s*\r?\n([\s\S]*?)\r?\n---(?:\s|$)/);
  if (!match) return null;
  const name = match[1].match(/^name:\s*["']?([^"'\r\n]+?)["']?\s*$/m);
  return name?.[1]?.trim() || null;
}

function markdownLinks(markdown) {
  const links = [];
  const pattern = /\]\(\s*(<[^>]+>|[^)\s]+)(?:\s+["'][^"']*["'])?\s*\)/g;
  for (const match of markdown.matchAll(pattern)) {
    const raw = match[1].startsWith("<") && match[1].endsWith(">")
      ? match[1].slice(1, -1)
      : match[1];
    const prefix = markdown.slice(0, match.index);
    links.push({ raw, line: prefix.split(/\r?\n/).length });
  }
  return links;
}

function localLinkPath(repoRoot, sourcePath, rawLink) {
  let decoded;
  try {
    decoded = decodeURIComponent(rawLink);
  } catch {
    return { kind: "invalid", reason: "link is not valid URI encoding" };
  }

  const pathPart = decoded.split(/[?#]/, 1)[0];
  if (!pathPart) return { kind: "anchor" };
  if (/^[a-z][a-z\d+.-]*:/i.test(pathPart) || pathPart.startsWith("//")) {
    return { kind: "external" };
  }

  const normalized = pathPart.replaceAll("\\", "/");
  if (normalized.startsWith("/") || isAbsolute(normalized)) {
    return { kind: "escape", reason: "absolute links are not allowed" };
  }

  const target = resolve(dirname(sourcePath), normalized);
  if (!isWithin(resolve(repoRoot), target)) {
    return { kind: "escape", target };
  }
  return { kind: "local", target };
}

function pathSafety(repoRoot, targetPath) {
  const rootPath = resolve(repoRoot);
  const result = { symlink: null, realpathEscape: null };
  if (!isWithin(rootPath, targetPath)) {
    result.realpathEscape = targetPath;
    return result;
  }

  const parts = relative(rootPath, targetPath).split(/[\\/]/).filter(Boolean);
  let current = rootPath;
  for (const part of parts) {
    current = join(current, part);
    try {
      if (lstatSync(current).isSymbolicLink()) {
        result.symlink = current;
        return result;
      }
    } catch {
      return result;
    }
  }

  try {
    const realRoot = realpathSync.native(rootPath);
    const realTarget = realpathSync.native(targetPath);
    if (!isWithin(realRoot, realTarget)) result.realpathEscape = realTarget;
  } catch {
    // Missing paths are reported by the caller as broken links or missing files.
  }
  return result;
}

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function entryMainPath(repoRoot, entry) {
  const entryPath = resolveWithin(repoRoot, entry?.path);
  if (!entryPath) return null;
  return entry.visibility === "public_entry" ? join(entryPath, "SKILL.md") : entryPath;
}

function rootChildPath(rootPath, childName) {
  const target = resolve(rootPath, childName);
  return isWithin(rootPath, target) && relative(rootPath, target).split(/[\\/]/).length === 1
    ? target
    : null;
}

function isLocalReferencePath(repoRoot, targetPath) {
  const rel = normalizeRelativePath(relative(repoRoot, targetPath));
  return LOCAL_ROOTS.some((root) => rel === root || rel.startsWith(`${root}/`));
}

function validateSafety(entry, findings) {
  if (!isObject(entry.safety)) {
    findings.push(finding("missing-safety-flags", `${entry.id} must declare safety flags`, { entry: entry.id }));
    return;
  }
  for (const key of ["network", "write", "execute", "vendor_scripts", "automatic"]) {
    if (typeof entry.safety[key] !== "boolean") {
      findings.push(finding("invalid-safety-flag", `${entry.id} safety.${key} must be boolean`, { entry: entry.id }));
    }
  }
  if (entry.safety.automatic === true) {
    findings.push(finding("automatic-action-enabled", `${entry.id} cannot automatically run guidance actions`, { entry: entry.id }));
  }
}

function validateClientStates(entry, findings) {
  if (!isObject(entry.client_resolution)) {
    findings.push(finding("missing-client-resolution", `${entry.id} must declare client resolution states`, { entry: entry.id }));
    return;
  }
  for (const [client, state] of Object.entries(entry.client_resolution)) {
    if (!CLIENT_STATES.has(state)) {
      findings.push(finding("invalid-client-state", `${entry.id} has invalid ${client} resolution state`, { entry: entry.id, client, state }));
    }
  }
}

function validatePath(repoRoot, pathValue, code, findings, { mustExist = true, mustNotBeSymlink = true } = {}) {
  const target = resolveWithin(repoRoot, pathValue);
  if (!target) {
    findings.push(finding("path-escape", `${code} resolves outside the repository`, { path: pathValue }));
    return null;
  }
  if (mustExist && !existsSync(target)) {
    findings.push(finding("missing-path", `${code} does not exist`, { path: pathValue }));
    return null;
  }
  const safety = pathSafety(repoRoot, target);
  if (safety.realpathEscape) {
    findings.push(finding("path-escape", `${code} resolves outside the repository through a real path`, { path: pathValue }));
  }
  if (mustNotBeSymlink && safety.symlink) {
    findings.push(finding("symlink-path", `${code} must not traverse a symlink`, { path: pathValue, symlink: displayPath(repoRoot, safety.symlink) }));
  }
  return target;
}

function inspectLocalLinks(repoRoot, sourcePath, findings, seenLinkKeys, duplicateLinkSources) {
  let markdown;
  try {
    markdown = readFileSync(sourcePath, "utf8");
  } catch {
    findings.push(finding("missing-link-source", `cannot read markdown source ${displayPath(repoRoot, sourcePath)}`, { path: displayPath(repoRoot, sourcePath) }));
    return [];
  }

  const localTargets = [];
  for (const link of markdownLinks(markdown)) {
    const resolved = localLinkPath(repoRoot, sourcePath, link.raw);
    if (resolved.kind === "external" || resolved.kind === "anchor") continue;
    const sourceRel = displayPath(repoRoot, sourcePath);
    if (resolved.kind === "invalid") {
      findings.push(finding("invalid-link", `${sourceRel}:${link.line} has an invalid relative link`, { source: sourceRel, line: link.line, link: link.raw }));
      continue;
    }
    if (resolved.kind === "escape") {
      findings.push(finding("path-escape", `${sourceRel}:${link.line} escapes the repository`, { source: sourceRel, line: link.line, link: link.raw }));
      continue;
    }

    const targetRel = displayPath(repoRoot, resolved.target);
    const key = `${sourceRel}|${targetRel}`;
    if (duplicateLinkSources.has(sourcePath) && isLocalReferencePath(repoRoot, resolved.target) && seenLinkKeys.has(key)) {
      findings.push(finding("duplicate-link", `${sourceRel}:${link.line} repeats a local link`, { source: sourceRel, line: link.line, target: targetRel }));
    }
    if (duplicateLinkSources.has(sourcePath) && isLocalReferencePath(repoRoot, resolved.target)) seenLinkKeys.add(key);

    if (!existsSync(resolved.target)) {
      findings.push(finding("broken-link", `${sourceRel}:${link.line} points to a missing file`, { source: sourceRel, line: link.line, target: targetRel }));
      continue;
    }
    const safety = pathSafety(repoRoot, resolved.target);
    if (safety.symlink) {
      findings.push(finding("symlink-link", `${sourceRel}:${link.line} traverses a symlink`, { source: sourceRel, line: link.line, target: targetRel }));
      continue;
    }
    if (safety.realpathEscape) {
      findings.push(finding("path-escape", `${sourceRel}:${link.line} resolves outside the repository`, { source: sourceRel, line: link.line, target: targetRel }));
      continue;
    }
    localTargets.push(resolved.target);
  }
  return localTargets;
}

function reachableLocalPaths(repoRoot, startPath, findings, seenLinkKeys, localLinkGraph, duplicateLinkSources) {
  const reachable = new Set();
  const queue = [startPath];
  while (queue.length > 0) {
    const current = queue.shift();
    if (reachable.has(current)) continue;
    reachable.add(current);
    const targets = inspectLocalLinks(repoRoot, current, findings, seenLinkKeys, duplicateLinkSources);
    localLinkGraph.set(current, new Set(targets));
    for (const target of targets) {
      if (isLocalReferencePath(repoRoot, target) && !reachable.has(target)) queue.push(target);
    }
  }
  return reachable;
}

function validateLocalLinkCycles(localLinkGraph, repoRoot, findings) {
  const visiting = new Set();
  const visited = new Set();
  const stack = [];
  function visit(path) {
    const relativePath = normalizeRelativePath(displayPath(repoRoot, path));
    if (relativePath.startsWith(".agents/vendor/")) return;
    if (visiting.has(path)) {
      const start = stack.indexOf(path);
      findings.push(finding("recursive-local-link", "local Skill reference links form a cycle", {
        chain: [...stack.slice(start), path].map((item) => displayPath(repoRoot, item)),
      }));
      return;
    }
    if (visited.has(path)) return;
    visiting.add(path);
    stack.push(path);
    for (const target of localLinkGraph.get(path) ?? []) visit(target);
    stack.pop();
    visiting.delete(path);
    visited.add(path);
  }
  for (const path of localLinkGraph.keys()) visit(path);
}

function validateProvenance(repoRoot, entry, findings, lockCache) {
  if (entry.source?.kind !== "vendored") return;
  const provenance = entry.provenance;
  if (!isObject(provenance)) {
    findings.push(finding("missing-provenance", `${entry.id} must pin vendored provenance`, { entry: entry.id }));
    return;
  }
  if (typeof provenance.repository !== "string" || !/^https:\/\//.test(provenance.repository)) {
    findings.push(finding("invalid-upstream-url", `${entry.id} must use an https upstream URL`, { entry: entry.id }));
  }
  if (typeof provenance.commit !== "string" || !FULL_COMMIT.test(provenance.commit)) {
    findings.push(finding("unpinned-upstream", `${entry.id} must use a full 40-character commit`, { entry: entry.id }));
  }
  if (typeof provenance.license !== "string" || provenance.license.trim().length === 0) {
    findings.push(finding("missing-license", `${entry.id} must declare license evidence`, { entry: entry.id }));
  }
  const digest = provenance.digest;
  if (!isObject(digest) || digest.algorithm !== "sha256" || !HEX_SHA256.test(digest.value ?? "")) {
    findings.push(finding("invalid-digest", `${entry.id} must declare a SHA-256 digest`, { entry: entry.id }));
  } else {
    const digestPath = validatePath(repoRoot, digest.path, `${entry.id} digest`, findings);
    if (digestPath) {
      const actual = hashFile(digestPath);
      if (actual !== digest.value) {
        findings.push(finding("digest-drift", `${entry.id} digest does not match the pinned file`, { entry: entry.id, path: digest.path, expected: digest.value, actual }));
      }
    }
  }

  const lock = provenance.lock;
  if (!isObject(lock) || typeof lock.path !== "string") {
    findings.push(finding("missing-lock-reference", `${entry.id} must identify external-skills.lock.json`, { entry: entry.id }));
    return;
  }
  const lockPath = validatePath(repoRoot, lock.path, `${entry.id} lock`, findings);
  if (!lockPath) return;
  const lockKey = displayPath(repoRoot, lockPath);
  let lockData = lockCache.get(lockKey);
  if (!lockData) {
    try {
      lockData = readJson(lockPath);
      lockCache.set(lockKey, lockData);
    } catch {
      findings.push(finding("invalid-lock", `${entry.id} lock is not valid JSON`, { entry: entry.id, path: lock.path }));
      return;
    }
  }

  const source = asArray(lockData.sources).find((candidate) => candidate?.repository === provenance.repository);
  if (!source) {
    findings.push(finding("lock-source-missing", `${entry.id} upstream is absent from its lock`, { entry: entry.id, repository: provenance.repository }));
    return;
  }
  if (source.commit !== provenance.commit) {
    findings.push(finding("lock-commit-drift", `${entry.id} lock commit differs from inventory`, { entry: entry.id, expected: provenance.commit, actual: source.commit }));
  }

  if (typeof lock.skill_name === "string") {
    const skill = asArray(source.skills).find((candidate) => candidate?.name === lock.skill_name);
    if (!skill) {
      findings.push(finding("lock-skill-missing", `${entry.id} skill is absent from its lock`, { entry: entry.id, skill: lock.skill_name }));
    } else {
      if (skill.source_path !== lock.source_path) {
        findings.push(finding("lock-source-path-drift", `${entry.id} source path differs from lock`, { entry: entry.id, expected: lock.source_path, actual: skill.source_path }));
      }
      if (skill.sha256 !== digest?.value) {
        findings.push(finding("lock-digest-drift", `${entry.id} digest differs from lock`, { entry: entry.id, expected: digest?.value, actual: skill.sha256 }));
      }
      for (const pinnedFile of asArray(provenance.pinned_files)) {
        const pinnedPath = validatePath(repoRoot, pinnedFile.path, `${entry.id} pinned file`, findings);
        if (!pinnedPath) continue;
        const actual = hashFile(pinnedPath);
        if (actual !== pinnedFile.value) {
          findings.push(finding("pinned-file-drift", `${entry.id} pinned supporting file changed`, { entry: entry.id, path: pinnedFile.path, expected: pinnedFile.value, actual }));
        }
        const scriptName = basename(pinnedPath);
        const lockedScript = skill.script_sha256?.[scriptName];
        if (lockedScript !== pinnedFile.value) {
          findings.push(finding("lock-script-drift", `${entry.id} supporting script digest differs from lock`, { entry: entry.id, script: scriptName, expected: pinnedFile.value, actual: lockedScript ?? null }));
        }
      }
    }
  } else if (typeof lock.snapshot_path === "string") {
    if (source.snapshot?.path !== lock.snapshot_path) {
      findings.push(finding("lock-snapshot-path-drift", `${entry.id} snapshot path differs from lock`, { entry: entry.id, expected: lock.snapshot_path, actual: source.snapshot?.path ?? null }));
    }
    if (source.snapshot?.sha256 !== digest?.value) {
      findings.push(finding("lock-snapshot-digest-drift", `${entry.id} snapshot digest differs from lock`, { entry: entry.id, expected: digest?.value, actual: source.snapshot?.sha256 ?? null }));
    }
  }

  const evidence = provenance.license_evidence;
  if (evidence) {
    const evidencePath = validatePath(repoRoot, evidence.path, `${entry.id} license evidence`, findings);
    if (evidencePath) {
      const actual = hashFile(evidencePath);
      if (actual !== evidence.value) {
        findings.push(finding("license-evidence-drift", `${entry.id} license evidence changed`, { entry: entry.id, path: evidence.path, expected: evidence.value, actual }));
      }
      const lockEvidencePath = source.license_evidence?.path;
      const lockEvidenceDigest = source.license_evidence?.sha256;
      const expectedPath = lockEvidencePath ? `.agents/vendor/${normalizeRelativePath(lockEvidencePath)}` : null;
      if (expectedPath !== evidence.path || lockEvidenceDigest !== evidence.value) {
        findings.push(finding("lock-license-evidence-drift", `${entry.id} license evidence differs from lock`, { entry: entry.id, expectedPath, actualPath: evidence.path, expected: lockEvidenceDigest ?? null, actual: evidence.value }));
      }
    }
  }
}

function scanDiscoveryRoot(repoRoot, discoveryRoot, inventory, findings) {
  const rootPath = validatePath(repoRoot, discoveryRoot, "discovery root", findings);
  if (!rootPath) return { rootPath: null, visible: [] };
  let children;
  try {
    children = readdirSync(rootPath, { withFileTypes: true });
  } catch {
    findings.push(finding("discovery-root-unreadable", "discovery root cannot be read", { path: discoveryRoot }));
    return { rootPath, visible: [] };
  }

  const visible = [];
  for (const child of children.sort((left, right) => left.name.localeCompare(right.name))) {
    const childPath = join(rootPath, child.name);
    const childStat = lstatSync(childPath);
    if (childStat.isSymbolicLink()) {
      findings.push(finding("discovery-root-symlink", "discovery root cannot contain symlinked skill directories", { path: displayPath(repoRoot, childPath) }));
      continue;
    }
    if (!childStat.isDirectory()) continue;
    const skillPath = join(childPath, "SKILL.md");
    if (!existsSync(skillPath)) continue;
    const content = readFileSync(skillPath, "utf8");
    visible.push({ name: frontMatterName(content), path: displayPath(repoRoot, childPath) });
  }

  const expected = isObject(inventory.discovery?.current) ? inventory.discovery.current : inventory.discovery?.baseline;
  if (!isObject(expected) || !Number.isInteger(expected.visible_count) || !Array.isArray(expected.visible_names)) {
    findings.push(finding("invalid-current-observation", "discovery current observation must include a visible count and names"));
  } else {
    const actualNames = visible.map((item) => item.name).sort();
    const expectedNames = [...expected.visible_names].sort();
    if (visible.length !== expected.visible_count || JSON.stringify(actualNames) !== JSON.stringify(expectedNames)) {
      findings.push(finding("current-discovery-drift", "current discovery root differs from the recorded observation", { expectedCount: expected.visible_count, actualCount: visible.length, expectedNames, actualNames }));
    }
  }
  return { rootPath, visible };
}

function validateInventory(inventory, { repoRoot, strictTarget = false } = {}) {
  const findings = [];
  const warnings = [];
  if (!isObject(inventory)) {
    return { findings: [finding("invalid-inventory", "inventory must be a JSON object")], warnings, discovery: {}, counts: {} };
  }
  if (inventory.schema_version !== INVENTORY_SCHEMA) {
    findings.push(finding("invalid-schema-version", `inventory schema must be ${INVENTORY_SCHEMA}`, { actual: inventory.schema_version ?? null }));
  }
  if (typeof inventory.inventory_id !== "string" || inventory.inventory_id.length === 0) {
    findings.push(finding("missing-inventory-id", "inventory_id is required"));
  }
  if (typeof inventory.observed_commit !== "string" || !FULL_COMMIT.test(inventory.observed_commit)) {
    findings.push(finding("invalid-observed-commit", "observed_commit must be a full commit SHA"));
  }

  const discovery = inventory.discovery;
  if (!isObject(discovery) || typeof discovery.root !== "string") {
    findings.push(finding("missing-discovery-root", "discovery.root is required"));
    return { findings, warnings, discovery: {}, counts: {} };
  }
  const rootScan = scanDiscoveryRoot(repoRoot, discovery.root, inventory, findings);
  const rootPath = rootScan.rootPath;

  const entries = asArray(inventory.entries);
  if (entries.length === 0) findings.push(finding("missing-entries", "inventory.entries must not be empty"));
  const entryById = new Map();
  const publicNames = new Map();
  const entryPaths = new Map();
  const legacyPaths = new Map();
  for (const entry of entries) {
    if (!isObject(entry) || typeof entry.id !== "string") {
      findings.push(finding("invalid-entry", "every inventory entry needs an id"));
      continue;
    }
    if (entryById.has(entry.id)) findings.push(finding("duplicate-entry-id", `${entry.id} is declared more than once`, { entry: entry.id }));
    entryById.set(entry.id, entry);
    if (!["public_entry", "internal_reference"].includes(entry.visibility)) {
      findings.push(finding("invalid-visibility", `${entry.id} has an invalid visibility`, { entry: entry.id, visibility: entry.visibility }));
    }
    if (typeof entry.version !== "string" || entry.version.length === 0) findings.push(finding("missing-entry-version", `${entry.id} must declare a version`, { entry: entry.id }));
    if (typeof entry.path !== "string") findings.push(finding("missing-entry-path", `${entry.id} must declare a path`, { entry: entry.id }));
    validateSafety(entry, findings);
    validateClientStates(entry, findings);
    if (typeof entry.path === "string") {
      const normalized = normalizeRelativePath(entry.path);
      if (entryPaths.has(normalized)) findings.push(finding("duplicate-entry-path", `${entry.id} reuses an entry path`, { entry: entry.id, path: normalized, other: entryPaths.get(normalized) }));
      entryPaths.set(normalized, entry.id);
    }

    if (entry.visibility === "public_entry") {
      if (typeof entry.public_name !== "string" || entry.public_name.length === 0) {
        findings.push(finding("missing-public-name", `${entry.id} must declare public_name`, { entry: entry.id }));
      } else if (publicNames.has(entry.public_name)) {
        findings.push(finding("duplicate-public-name", `${entry.public_name} is declared more than once`, { name: entry.public_name, other: publicNames.get(entry.public_name) }));
      } else {
        publicNames.set(entry.public_name, entry.id);
      }
      if (entry.source?.kind !== "repo") findings.push(finding("public-source-kind", `${entry.id} must be repo-owned`, { entry: entry.id }));
      const path = validatePath(repoRoot, entry.path, `${entry.id} wrapper`, findings);
      if (path) {
        if (lstatSync(path).isSymbolicLink() || !lstatSync(path).isDirectory()) findings.push(finding("invalid-wrapper-path", `${entry.id} wrapper must be a real directory`, { entry: entry.id }));
        if (rootPath && !isWithin(rootPath, path)) findings.push(finding("public-outside-discovery-root", `${entry.id} is outside discovery root`, { entry: entry.id }));
        if (rootPath && relative(rootPath, path).split(/[\\/]/).length !== 1) findings.push(finding("public-not-root-child", `${entry.id} must be a direct discovery-root child`, { entry: entry.id }));
        const requiredFiles = asArray(entry.wrapper?.required_files);
        for (const requiredFile of requiredFiles) validatePath(repoRoot, join(entry.path, requiredFile), `${entry.id} required file`, findings);
        const skillPath = entryMainPath(repoRoot, entry);
        if (skillPath && existsSync(skillPath)) {
          const name = frontMatterName(readFileSync(skillPath, "utf8"));
          if (name !== entry.public_name) findings.push(finding("public-name-drift", `${entry.id} frontmatter name differs from public_name`, { entry: entry.id, expected: entry.public_name, actual: name }));
        }
      }
    } else if (entry.visibility === "internal_reference") {
      if (typeof entry.reference_name !== "string" || entry.reference_name.length === 0) findings.push(finding("missing-reference-name", `${entry.id} must declare reference_name`, { entry: entry.id }));
      if (Object.hasOwn(entry, "public_name")) findings.push(finding("internal-public-name", `${entry.id} must not expose public_name`, { entry: entry.id }));
      const path = validatePath(repoRoot, entry.path, `${entry.id} reference`, findings);
      if (path) {
        if (rootPath && isWithin(rootPath, path)) findings.push(finding("internal-in-discovery-root", `${entry.id} is inside the public discovery root`, { entry: entry.id, path: entry.path }));
        if (lstatSync(path).isDirectory()) findings.push(finding("reference-path-directory", `${entry.id} path must point to a file`, { entry: entry.id }));
      }
      if (typeof entry.legacy_discovery_path === "string") {
        const legacy = normalizeRelativePath(entry.legacy_discovery_path);
        if (legacyPaths.has(legacy)) findings.push(finding("duplicate-legacy-path", `${entry.id} reuses a legacy discovery path`, { entry: entry.id, path: legacy, other: legacyPaths.get(legacy) }));
        legacyPaths.set(legacy, entry.id);
        const legacyPath = resolveWithin(repoRoot, legacy);
        if (!legacyPath) {
          findings.push(finding("legacy-path-escape", `${entry.id} legacy path escapes the repository`, { entry: entry.id, path: legacy }));
        } else if (existsSync(legacyPath)) {
          const legacySafety = pathSafety(repoRoot, legacyPath);
          if (legacySafety.symlink || legacySafety.realpathEscape) findings.push(finding("legacy-path-unsafe", `${entry.id} legacy path is unsafe`, { entry: entry.id, path: legacy }));
          if (rootPath && !isWithin(rootPath, legacyPath)) findings.push(finding("legacy-outside-discovery-root", `${entry.id} legacy path is not under discovery root`, { entry: entry.id }));
          const legacySkill = join(legacyPath, "SKILL.md");
          if (existsSync(legacySkill) && typeof entry.reference_name === "string") {
            const legacyName = frontMatterName(readFileSync(legacySkill, "utf8"));
            if (legacyName !== entry.reference_name) findings.push(finding("legacy-name-drift", `${entry.id} legacy wrapper name differs from reference_name`, { entry: entry.id, expected: entry.reference_name, actual: legacyName }));
          }
        } else if (entry.legacy_status !== "migrated") {
          findings.push(finding("legacy-path-missing", `${entry.id} legacy path is absent without a recorded migration`, { entry: entry.id, path: legacy }));
        }
      }
    }
  }

  const lockCache = new Map();
  const seenLinkKeys = new Set();
  const localLinkGraph = new Map();
  const duplicateLinkSources = new Set(entries
    .filter((entry) => entry.visibility === "public_entry")
    .map((entry) => entryMainPath(repoRoot, entry))
    .filter(Boolean));
  const reachableByPublicId = new Map();
  for (const entry of entries) {
    const mainPath = entryMainPath(repoRoot, entry);
    if (!mainPath || !existsSync(mainPath)) continue;
    const reachable = reachableLocalPaths(repoRoot, mainPath, findings, seenLinkKeys, localLinkGraph, duplicateLinkSources);
    if (entry.visibility === "public_entry") reachableByPublicId.set(entry.id, reachable);
    if (entry.visibility === "internal_reference") validateProvenance(repoRoot, entry, findings, lockCache);
  }

  validateLocalLinkCycles(localLinkGraph, repoRoot, findings);

  for (const entry of entries) {
    if (!Array.isArray(entry.references)) {
      findings.push(finding("missing-reference-list", `${entry.id} must declare references`, { entry: entry.id }));
      continue;
    }
    for (const referenceId of entry.references) {
      const reference = entryById.get(referenceId);
      if (!reference) {
        findings.push(finding("unknown-reference", `${entry.id} points to an unknown reference`, { entry: entry.id, reference: referenceId }));
        continue;
      }
      if (reference.visibility !== "internal_reference") {
        findings.push(finding("public-reference-target", `${entry.id} may only point to internal_reference entries`, { entry: entry.id, reference: referenceId }));
      }
      const reachable = reachableByPublicId.get(entry.id);
      if (reachable && typeof reference.path === "string") {
        const referencePath = resolveWithin(repoRoot, reference.path);
        if (!referencePath || !reachable.has(referencePath)) {
          findings.push(finding("unreachable-reference", `${entry.id} cannot reach ${referenceId} through local links`, { entry: entry.id, reference: referenceId, path: reference.path }));
        }
      }
    }
  }

  const visiting = new Set();
  const visited = new Set();
  function visit(id, chain) {
    if (visiting.has(id)) {
      findings.push(finding("recursive-reference", `skill reference graph recurses through ${id}`, { chain: [...chain, id] }));
      return;
    }
    if (visited.has(id)) return;
    visiting.add(id);
    const entry = entryById.get(id);
    for (const next of asArray(entry?.references)) visit(next, [...chain, id]);
    visiting.delete(id);
    visited.add(id);
  }
  for (const entry of entries) visit(entry.id, []);

  const aliases = inventory.migration?.aliases;
  const aliasNames = new Map();
  if (!Array.isArray(aliases)) {
    findings.push(finding("missing-migration-table", "migration.aliases must be an array"));
  } else {
    for (const alias of aliases) {
      if (!isObject(alias) || typeof alias.old_name !== "string" || typeof alias.replacement !== "string") {
        findings.push(finding("invalid-migration-alias", "migration aliases need old_name and replacement"));
        continue;
      }
      if (aliasNames.has(alias.old_name)) findings.push(finding("duplicate-migration-alias", `${alias.old_name} is migrated more than once`, { name: alias.old_name, other: aliasNames.get(alias.old_name) }));
      aliasNames.set(alias.old_name, alias.replacement);
      if (!publicNames.has(alias.replacement)) findings.push(finding("migration-target-not-public", `${alias.old_name} does not migrate to a public entry`, { oldName: alias.old_name, replacement: alias.replacement }));
      if (publicNames.has(alias.old_name)) findings.push(finding("migration-alias-collides", `${alias.old_name} is already a public name`, { name: alias.old_name }));
    }
  }

  const visibleNames = new Set(rootScan.visible.map((item) => item.name));
  for (const visible of rootScan.visible) {
    const publicEntryId = publicNames.get(visible.name);
    const internalEntryId = [...entryById.values()].find((entry) => entry.visibility === "internal_reference" && entry.reference_name === visible.name)?.id;
    if (!publicEntryId && !internalEntryId) {
      findings.push(finding("undocumented-visible-skill", `${visible.name ?? "<unnamed>"} is visible but absent from inventory`, { path: visible.path }));
    }
    if (internalEntryId) {
      const message = `${visible.name} is a legacy internal wrapper still visible in the discovery root`;
      if (strictTarget || discovery.transition?.strict_target_requires_root_public_only === true && strictTarget) {
        findings.push(finding("legacy-visible-internal-entry", message, { name: visible.name, path: visible.path }));
      } else {
        warnings.push(warning("legacy-visible-internal-entry", message, { name: visible.name, path: visible.path }));
      }
    }
  }

  const target = discovery.target;
  if (!isObject(target) || !Number.isInteger(target.visible_count) || !Array.isArray(target.visible_names)) {
    findings.push(finding("invalid-target", "discovery.target must include a visible count and names"));
  } else {
    const expectedTargetNames = [...target.visible_names].sort();
    const actualPublicNames = [...publicNames.keys()].sort();
    if (target.visible_count !== publicNames.size || JSON.stringify(expectedTargetNames) !== JSON.stringify(actualPublicNames)) {
      findings.push(finding("target-drift", "discovery target does not match public entries", { expectedCount: target.visible_count, actualCount: publicNames.size, expectedNames: expectedTargetNames, actualNames: actualPublicNames }));
    }
  }

  const clients = inventory.clients;
  if (!isObject(clients)) {
    findings.push(finding("missing-clients", "client-specific discovery states are required"));
  } else {
    for (const [client, observation] of Object.entries(clients)) {
      if (!isObject(observation) || !CLIENT_STATES.has(observation.status)) {
        findings.push(finding("invalid-client-observation", `${client} needs observed, unknown, or unsupported status`, { client }));
        continue;
      }
      if (observation.status === "observed") {
        if (!Number.isInteger(observation.visible_count) || !Array.isArray(observation.visible_names)) {
          findings.push(finding("observed-client-count-missing", `${client} observed state must include count and names`, { client }));
        } else if (observation.discovery_root === discovery.root) {
          const observedNames = [...observation.visible_names].sort();
          const actualNames = rootScan.visible.map((item) => item.name).sort();
          if (observation.visible_count !== rootScan.visible.length || JSON.stringify(observedNames) !== JSON.stringify(actualNames)) {
            findings.push(finding("client-discovery-drift", `${client} observed count does not match the current discovery root`, { client, expectedCount: rootScan.visible.length, actualCount: observation.visible_count, expectedNames: actualNames, actualNames: observedNames }));
          }
        }
      } else if (observation.visible_count !== null || observation.visible_names !== null) {
        findings.push(finding("unknown-client-count-asserted", `${client} non-observed state must not assert a visible count`, { client }));
      }
      for (const key of ["explicit_skill_resolution", "root_external_reference"]) {
        if (!CLIENT_STATES.has(observation[key])) findings.push(finding("invalid-client-capability-state", `${client}.${key} must be observed, unknown, or unsupported`, { client, key }));
      }
      if (observation.status === "unsupported" && typeof observation.evidence !== "string") {
        findings.push(finding("unsupported-client-evidence-missing", `${client} unsupported state needs evidence`, { client }));
      }
    }
  }

  const legacyVisibleInternalCount = rootScan.visible.filter((item) => [...entryById.values()].some((entry) => entry.visibility === "internal_reference" && entry.reference_name === item.name)).length;
  return {
    findings,
    warnings,
    discovery: {
      root: discovery.root,
      baseline_count: discovery.baseline?.visible_count ?? null,
      current_count: discovery.current?.visible_count ?? rootScan.visible.length,
      observed_count: rootScan.visible.length,
      target_count: discovery.target?.visible_count ?? null,
      observed_names: rootScan.visible.map((item) => item.name),
      target_names: discovery.target?.visible_names ?? null,
      legacy_visible_internal_count: legacyVisibleInternalCount,
      strict_target: strictTarget,
    },
    counts: {
      entries: entries.length,
      public_entries: [...entryById.values()].filter((entry) => entry.visibility === "public_entry").length,
      internal_references: [...entryById.values()].filter((entry) => entry.visibility === "internal_reference").length,
    },
  };
}

export function checkSkillInventory({ repoRoot = process.cwd(), inventoryPath = DEFAULT_INVENTORY_PATH, strictTarget = false } = {}) {
  const absoluteRepoRoot = resolve(repoRoot);
  const absoluteInventoryPath = resolveWithin(absoluteRepoRoot, inventoryPath) ?? (isAbsolute(inventoryPath) ? resolve(inventoryPath) : null);
  if (!absoluteInventoryPath) {
    return {
      ok: false,
      schema_version: INVENTORY_SCHEMA,
      inventory_id: null,
      inventory_digest: null,
      findings: [finding("inventory-path-escape", "inventory path resolves outside the repository", { path: inventoryPath })],
      warnings: [],
      discovery: {},
      counts: {},
    };
  }
  let inventory;
  let inventoryDigest = null;
  try {
    inventoryDigest = hashFile(absoluteInventoryPath);
    inventory = readJson(absoluteInventoryPath);
  } catch (error) {
    return {
      ok: false,
      schema_version: INVENTORY_SCHEMA,
      inventory_id: null,
      inventory_digest: inventoryDigest,
      findings: [finding("inventory-read-failed", `cannot read skill inventory: ${error.message}`, { path: displayPath(absoluteRepoRoot, absoluteInventoryPath) })],
      warnings: [],
      discovery: {},
      counts: {},
    };
  }
  const result = validateInventory(inventory, { repoRoot: absoluteRepoRoot, strictTarget });
  return {
    ok: result.findings.length === 0,
    schema_version: inventory.schema_version ?? null,
    inventory_id: inventory.inventory_id ?? null,
    inventory_digest: inventoryDigest,
    observed_commit: inventory.observed_commit ?? null,
    findings: result.findings,
    warnings: result.warnings,
    discovery: result.discovery,
    counts: result.counts,
  };
}

function printReport(report) {
  const state = report.ok ? "PASS" : "FAIL";
  console.log(`Skill inventory ${state}`);
  console.log(`identity=${report.inventory_id ?? "unknown"}`);
  console.log(`digest=${report.inventory_digest ?? "unknown"}`);
  console.log(`visible=${report.discovery.observed_count ?? "unknown"} target=${report.discovery.target_count ?? "unknown"}`);
  console.log(`entries=${report.counts.entries ?? "unknown"} public=${report.counts.public_entries ?? "unknown"} internal=${report.counts.internal_references ?? "unknown"}`);
  for (const item of [...report.findings, ...report.warnings]) {
    console.log(`${item.severity.toUpperCase()} ${item.code}: ${item.message}`);
  }
}

function parseArgs(args) {
  const options = { json: false, strictTarget: false, inventoryPath: DEFAULT_INVENTORY_PATH, repoRoot: process.cwd() };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--json") options.json = true;
    else if (arg === "--strict-target") options.strictTarget = true;
    else if (arg === "--inventory") options.inventoryPath = args[++index];
    else if (arg === "--repo-root") options.repoRoot = args[++index];
    else if (arg === "--help") options.help = true;
    else throw new Error(`unknown option: ${arg}`);
  }
  return options;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) {
      console.log("usage: node scripts/check-skill-inventory.mjs [--json] [--strict-target] [--inventory path] [--repo-root path]");
      process.exitCode = 0;
    } else {
      const report = checkSkillInventory(options);
      if (options.json) console.log(JSON.stringify(report, null, 2));
      else printReport(report);
      process.exitCode = report.ok ? 0 : 1;
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 2;
  }
}
