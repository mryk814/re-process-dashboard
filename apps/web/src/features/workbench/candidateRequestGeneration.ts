export type CandidateRequestToken = Readonly<{
  generation: number;
  projectId: string;
  candidateId: string;
}>;

export class CandidateRequestGeneration {
  private generation = 0;
  private projectId = "";
  private candidateId = "";

  activate(projectId: string, candidateId: string): CandidateRequestToken {
    this.generation += 1;
    this.projectId = projectId;
    this.candidateId = candidateId;
    return this.current();
  }

  current(): CandidateRequestToken {
    return {
      generation: this.generation,
      projectId: this.projectId,
      candidateId: this.candidateId,
    };
  }

  isCurrent(token: CandidateRequestToken): boolean {
    return token.generation === this.generation
      && token.projectId === this.projectId
      && token.candidateId === this.candidateId;
  }

  invalidate(): void {
    this.generation += 1;
    this.projectId = "";
    this.candidateId = "";
  }
}
