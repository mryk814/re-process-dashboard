export type DistributionRequestIdentity = Readonly<{
  projectId: string;
  candidateId: string;
  candidateRevision: number;
  chainRevisionDigest: string;
  pointExecutionRequestId: string;
}>;

export type DistributionRequestToken = Readonly<{
  generation: number;
  identity: DistributionRequestIdentity;
}>;

function sameIdentity(
  left: DistributionRequestIdentity,
  right: DistributionRequestIdentity,
) {
  return left.projectId === right.projectId
    && left.candidateId === right.candidateId
    && left.candidateRevision === right.candidateRevision
    && left.chainRevisionDigest === right.chainRevisionDigest
    && left.pointExecutionRequestId === right.pointExecutionRequestId;
}

export class DistributionRequestGeneration {
  private generation = 0;
  private identity: DistributionRequestIdentity | null = null;

  activate(identity: DistributionRequestIdentity): DistributionRequestToken {
    this.generation += 1;
    this.identity = identity;
    return { generation: this.generation, identity };
  }

  isCurrent(token: DistributionRequestToken): boolean {
    return token.generation === this.generation
      && this.identity !== null
      && sameIdentity(token.identity, this.identity);
  }

  invalidate(): void {
    this.generation += 1;
    this.identity = null;
  }
}

export function distributionMatchesIdentity(
  run: {
    provenance: {
      candidate_id: string;
      candidate_revision: number;
      chain_revision_digest: string;
      point_execution_request_id: string;
    };
  },
  identity: DistributionRequestIdentity,
) {
  return run.provenance.candidate_id === identity.candidateId
    && run.provenance.candidate_revision === identity.candidateRevision
    && run.provenance.chain_revision_digest === identity.chainRevisionDigest
    && run.provenance.point_execution_request_id === identity.pointExecutionRequestId;
}
