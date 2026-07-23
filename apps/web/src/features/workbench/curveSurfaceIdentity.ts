type ResponseCurveSurfaceIdentity = Readonly<{
  projectId: string;
  taskId: string;
  candidateId: string;
  candidateRevision: number;
  inputIdentity: string;
  outputKey: string;
  variableId: string;
  rangeIdentity: string;
}>;

export function responseCurveSurfaceIdentity(identity: ResponseCurveSurfaceIdentity) {
  const storageKey = [
    identity.projectId,
    identity.taskId,
    identity.candidateId,
    identity.outputKey,
    identity.variableId,
    identity.rangeIdentity,
  ].join("\u001f");
  const requestIdentity = [
    identity.projectId,
    identity.taskId,
    identity.candidateId,
    identity.candidateRevision,
    "response_curve:9",
    identity.inputIdentity,
    identity.outputKey,
    identity.variableId,
    identity.rangeIdentity,
  ].join("\u001f");
  return { storageKey, requestIdentity };
}

type CurveFamilyScopeIdentity = Readonly<{
  projectId: string;
  taskId: string;
  candidateId: string;
  axisPath: string;
  varyId: string;
  levels: number;
  outputKeys: string;
}>;

export function curveFamilyScopeIdentity(identity: CurveFamilyScopeIdentity) {
  return [
    identity.projectId,
    identity.taskId,
    identity.candidateId,
    identity.axisPath,
    identity.varyId,
    identity.levels,
    identity.outputKeys,
  ].join("\u001f");
}
