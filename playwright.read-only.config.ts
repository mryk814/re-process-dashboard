import { defineConfig } from "@playwright/test";
import baseConfig from "./playwright.config";
import { sharedReadOnlySpecs } from "./e2e/suite-inventory.mjs";

const workers = Number(process.env.PLAYWRIGHT_READ_ONLY_WORKERS ?? "2");
if (!Number.isInteger(workers) || workers < 2) {
  throw new Error("PLAYWRIGHT_READ_ONLY_WORKERS must be an integer of at least 2");
}

/**
 * These specs read the seeded workspace only. They can share one fresh server
 * and use Playwright workers by spec. A spec keeps its own examples ordered:
 * some lineage reads issue several large graph requests and are not a safe
 * test-level parallel unit on Windows.
 */
export default defineConfig({
  ...baseConfig,
  testMatch: sharedReadOnlySpecs,
  fullyParallel: false,
  workers,
  retries: 0,
});
