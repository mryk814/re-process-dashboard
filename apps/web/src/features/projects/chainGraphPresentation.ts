import type {
  ApiChainExecution,
  ApiChainGraph,
  ApiPredictionGraphExecution,
} from "../../shared/api/workbench-api";

type ChainDefinition = ApiChainGraph["definition"];
type ChainRevision = ApiChainGraph["revision"];
type ChainBinding = ChainDefinition["bindings"][number];
type ChainPort = ApiChainGraph["prediction_graph"]["inputs"][number]["port"];
type StageSurface = NonNullable<ApiChainGraph["stage_contracts"][number]["surface"]>;
type StagePort = StageSurface["input_ports"][number] | StageSurface["output_ports"][number];

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
  sourcePort?: ChainPort | StagePort;
  targetPort?: StagePort;
  status: "available" | "unavailable";
  reason?: string;
  branchCount: number;
  mergeCount: number;
};

export function shortDigest(value: string | null | undefined): string {
  if (!value) return "未固定";
  return value.length > 19 ? `${value.slice(0, 15)}…` : value;
}

export type ApiGraphExecution = ApiChainExecution | ApiPredictionGraphExecution;

export function stageStatus(execution: ApiGraphExecution | null, stageId: string) {
  return execution?.stages.find((item) => item.stage_id === stageId)?.status ?? "未実行";
}

function portSurface(graph: ApiChainGraph, stageId: string) {
  return graph.stage_contracts.find((item) => item.stage_id === stageId);
}

function stagePort(graph: ApiChainGraph, stageId: string, direction: "input" | "output", path: string) {
  const stage = portSurface(graph, stageId);
  if (!stage?.surface) return { port: undefined, reason: stage?.reason ?? "固定したStage contract surfaceがありません。" };
  const ports = direction === "input" ? stage.surface.input_ports : stage.surface.output_ports;
  const port = ports.find((item) => item.path === path);
  return { port, reason: port ? undefined : `${stageId} の${direction} port ${path} は固定surfaceにありません。` };
}

export function buildChainGraph(graph: ApiChainGraph): ChainGraphEdge[] {
  const { definition } = graph;
  const externalPorts = new Map(
    graph.prediction_graph.inputs.map((input) => [input.input_id, input.port]),
  );
  const raw = definition.bindings.map((binding, index) => {
    const source = binding.source.source_kind === "external"
      ? { kind: "external" as const, id: binding.source.path, label: binding.source.path }
      : { kind: "stage" as const, id: binding.source.stage_id, label: `${binding.source.stage_id}.${binding.source.output_key}` };
    const sourceResolution = binding.source.source_kind === "external"
      ? { port: externalPorts.get(binding.source.path), reason: externalPorts.has(binding.source.path) ? undefined : `外部input ${binding.source.path} は固定Definitionにありません。` }
      : stagePort(graph, binding.source.stage_id, "output", binding.source.output_key);
    const targetResolution = stagePort(graph, binding.target_stage_id, "input", binding.target_input_path);
    const reason = sourceResolution.reason ?? targetResolution.reason;
    return {
      id: `${source.kind}:${source.label}->${binding.target_stage_id}.${binding.target_input_path}:${index}`,
      source,
      target: { kind: "stage" as const, id: binding.target_stage_id, label: `${binding.target_stage_id}.${binding.target_input_path}` },
      binding,
      sourcePort: sourceResolution.port,
      targetPort: targetResolution.port,
      status: reason ? "unavailable" as const : "available" as const,
      reason,
    };
  });
  return raw.map((edge) => ({
    ...edge,
    branchCount: raw.filter((candidate) => candidate.source.label === edge.source.label).length,
    mergeCount: raw.filter((candidate) => candidate.target.id === edge.target.id).length,
  }));
}

export function stageBindingCounts(graph: ApiChainGraph, stageId: string) {
  const surface = portSurface(graph, stageId)?.surface;
  return {
    inputs: surface?.input_ports.length ?? 0,
    outputs: surface?.output_ports.length ?? 0,
  };
}

export function revisionStage(revision: ChainRevision, stageId: string) {
  return revision.stages.find((stage) => stage.stage_id === stageId);
}
