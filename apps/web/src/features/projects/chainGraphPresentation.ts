import type { ApiChainExecution, ApiChainTemplate } from "../../shared/api/workbench-api";

type ChainDefinition = ApiChainTemplate["definition"];
type ChainRevision = ApiChainTemplate["revisions"][number];
type ChainBinding = ChainDefinition["bindings"][number];
type ChainPort = ChainDefinition["external_inputs"][number];

export type ChainGraphEndpoint = {
  kind: "external" | "stage";
  id: string;
  label: string;
};

export type ChainGraphEdge = {
  id: string;
  source: ChainGraphEndpoint;
  target: ChainGraphEndpoint;
  binding: ChainBinding;
  sourcePort?: ChainPort;
};

export function shortDigest(value: string | null | undefined): string {
  if (!value) return "未固定";
  return value.length > 19 ? `${value.slice(0, 15)}…` : value;
}

export function stageStatus(execution: ApiChainExecution | null, stageId: string) {
  return execution?.stages.find((item) => item.stage_id === stageId)?.status ?? "未実行";
}

export function buildChainGraph(definition: ChainDefinition): ChainGraphEdge[] {
  const externalPorts = new Map(definition.external_inputs.map((port) => [port.path, port]));
  return definition.bindings.map((binding, index) => {
    const source = binding.source.source_kind === "external"
      ? {
        kind: "external" as const,
        id: binding.source.path,
        label: binding.source.path,
      }
      : {
        kind: "stage" as const,
        id: binding.source.stage_id,
        label: `${binding.source.stage_id}.${binding.source.output_key}`,
      };
    return {
      id: `${source.kind}:${source.label}->${binding.target_stage_id}.${binding.target_input_path}:${index}`,
      source,
      target: {
        kind: "stage",
        id: binding.target_stage_id,
        label: `${binding.target_stage_id}.${binding.target_input_path}`,
      },
      binding,
      sourcePort: binding.source.source_kind === "external"
        ? externalPorts.get(binding.source.path)
        : undefined,
    };
  });
}

export function stageBindingCounts(definition: ChainDefinition, stageId: string) {
  return {
    inputs: definition.bindings.filter((binding) => binding.target_stage_id === stageId).length,
    outputs: definition.bindings.filter((binding) => (
      binding.source.source_kind === "stage_output" && binding.source.stage_id === stageId
    )).length,
  };
}

export function revisionStage(revision: ChainRevision, stageId: string) {
  return revision.stages.find((stage) => stage.stage_id === stageId);
}
