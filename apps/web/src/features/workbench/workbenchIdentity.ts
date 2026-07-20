export type WorkbenchIdentity = Readonly<{
  projectId: string;
  taskId: string;
  candidateId: string;
  candidateRevision: number;
}>;

export type WorkbenchOperation =
  | "preview"
  | "detailed"
  | "similarity"
  | "snapshots"
  | "actuals"
  | `response_curve:${string}`;

export type WorkbenchInferenceIdentity = Readonly<{
  projectId: string;
  taskId: string;
  candidateId: string;
  inputIdentity: string;
}>;

export function workbenchRequestKey(identity: WorkbenchIdentity, operation: WorkbenchOperation): string {
  return [identity.projectId, identity.taskId, identity.candidateId, identity.candidateRevision, operation].join("\u001f");
}

export function workbenchInferenceKey(identity: WorkbenchInferenceIdentity, operation: "preview" | `response_curve:${string}`): string {
  return [identity.projectId, identity.taskId, identity.candidateId, identity.inputIdentity, operation].join("\u001f");
}

export class WorkbenchRequestGuard {
  private currentKey = "";

  begin(identity: WorkbenchIdentity, operation: WorkbenchOperation) {
    const key = workbenchRequestKey(identity, operation);
    this.currentKey = key;
    return { key, isCurrent: () => this.currentKey === key };
  }

  invalidate() {
    this.currentKey = "";
  }
}
