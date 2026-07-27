export type WorkspaceRestoreTransactionOperations = {
  stopSidecar: () => Promise<void>;
  commit: (restoreToken: string) => Promise<void>;
  startAndVerifyHealth: (port: number) => Promise<void>;
  finalize: (restoreToken: string) => Promise<void>;
  rollback: (restoreToken: string) => Promise<void>;
  cancel: (restoreToken: string) => Promise<void>;
  rollbackFailed: (error: unknown) => never;
  restartFailed: (error: unknown) => never;
};

/**
 * Replace the Workspace only if the restored API passes its real startup
 * health check.  The injected start operation is the same startSidecarOnPort
 * used during normal Electron startup, so a rejected health check necessarily
 * enters the rollback branch before the original Workspace is restarted.
 */
export async function runWorkspaceRestoreTransaction(
  restoreToken: string,
  restartPort: number,
  operations: WorkspaceRestoreTransactionOperations,
): Promise<void> {
  let committed = false;
  await operations.stopSidecar();
  try {
    await operations.commit(restoreToken);
    committed = true;
    await operations.startAndVerifyHealth(restartPort);
    await operations.finalize(restoreToken);
  } catch (error) {
    await operations.stopSidecar();
    if (committed) {
      try {
        await operations.rollback(restoreToken);
      } catch (rollbackError) {
        operations.rollbackFailed(rollbackError);
      }
    } else {
      await operations.cancel(restoreToken);
    }
    try {
      await operations.startAndVerifyHealth(restartPort);
    } catch (restartError) {
      operations.restartFailed(restartError);
    }
    throw error;
  }
}
