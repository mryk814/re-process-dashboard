import { registerOwnedStoreCleanup } from "./owned-database-cleanup.mjs";

export default function globalTeardown(config) {
  const root = config.metadata?.currentMainOwnedRoot;
  if (root) registerOwnedStoreCleanup(root);
}
