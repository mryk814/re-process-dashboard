export type LabSurfaceState = "ready" | "loading" | "stale" | "error";
export type LabSupportStatus = "supported" | "caution" | "extrapolated";

export type LabNumericField = {
  path: string;
  label: string;
  unit: string;
  group: "composition" | "process";
  min: number;
  max: number;
  step: number;
};

export type LabCategoricalField = {
  path: string;
  label: string;
  group: "categorical";
  choices: string[];
};

export type LabField = LabNumericField | LabCategoricalField;

export type LabOutputDefinition = {
  key: string;
  label: string;
  unit: string;
  goalDirection: "at_least" | "at_most" | "target";
};

export type LabRuntimeCapability = {
  uncertainty: boolean;
  support: boolean;
  responseCurves: boolean;
  similarity: boolean;
  detailedPrediction: boolean;
};

export type LabTaskDefinition = {
  id: string;
  label: string;
  description: string;
  fields: LabField[];
  outputs: LabOutputDefinition[];
  capabilities: LabRuntimeCapability;
};

export type LabCandidate = {
  id: string;
  name: string;
  values: Record<string, number | string>;
  archived?: boolean;
};

export type LabPrediction = {
  outputKey: string;
  value: number;
  lower?: number;
  upper?: number;
  goalValue?: number;
  goalProbability?: number;
};

export type LabPreview = {
  candidateId: string;
  predictions: LabPrediction[];
  supportStatus: LabSupportStatus;
  supportMessage: string;
  nearestLabel: string;
  warning?: string;
};

export type LabCurvePoint = {
  x: number;
  value: number;
  lower?: number;
  upper?: number;
};

export type LabCurveSeries = {
  candidateId: string;
  outputKey: string;
  points: LabCurvePoint[];
};

export type LabFixture = {
  task: LabTaskDefinition;
  candidates: LabCandidate[];
  previews: Record<string, LabPreview>;
};
