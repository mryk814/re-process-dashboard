import type {
  ApiPredictionGraphCatalog,
  ApiPredictionGraphDefinition,
} from "../../shared/api/workbench-api";

export type GraphStage = ApiPredictionGraphDefinition["stages"][number];
export type GraphInput = ApiPredictionGraphDefinition["inputs"][number];
export type GraphBinding = ApiPredictionGraphDefinition["bindings"][number];
export type GraphDecisionOutput = ApiPredictionGraphDefinition["decision_outputs"][number];
export type GraphPort = NonNullable<ApiPredictionGraphCatalog["stages"][number]["surface"]>["input_ports"][number];
export type BindingSource = GraphBinding["source"];
export type DraftSelection =
  | { kind: "input"; id: string }
  | { kind: "stage"; id: string; port?: string }
  | { kind: "output"; id: string }
  | { kind: "binding"; id: string; port: string };

export type SourceOption = {
  key: string;
  label: string;
  source: BindingSource;
  port: GraphPort;
};

export type GraphPresentationEdge = {
  key: string;
  kind: "binding" | "decision_output";
  sourceKey: string;
  targetKey: string;
};

export function stageCatalogItem(
  catalog: ApiPredictionGraphCatalog,
  stage: GraphStage,
) {
  return catalog.stages.find((item) => (
    item.stage_kind === stage.stage_kind
    && item.contract_id === stage.contract_id
    && item.status === "available"
    && item.surface
  ));
}

export function samePort(left: GraphPort, right: GraphPort) {
  return left.value_kind === right.value_kind
    && left.quantity === right.quantity
    && left.unit === right.unit
    && left.basis === right.basis;
}

export function sourceKey(source: BindingSource) {
  return source.source_kind === "external"
    ? `input:${source.path}`
    : `stage:${source.stage_id}:${source.output_key}`;
}

export function sourceLabel(source: BindingSource) {
  return source.source_kind === "external"
    ? source.path
    : `${source.stage_id}.${source.output_key}`;
}

export function emptyPredictionGraph(): ApiPredictionGraphDefinition {
  return {
    schema_version: "prediction-graph-definition/v1",
    graph_id: "prediction-graph-draft",
    label: "新しい判断グラフ",
    stages: [],
    inputs: [],
    bindings: [],
    decision_outputs: [],
  };
}

function uniqueId(existing: string[], seed: string) {
  const base = seed.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "node";
  let candidate = base.match(/^[A-Za-z]/) ? base : `n-${base}`;
  let suffix = 2;
  while (existing.includes(candidate)) {
    candidate = `${base}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}

export function addStage(
  definition: ApiPredictionGraphDefinition,
  catalogItem: ApiPredictionGraphCatalog["stages"][number],
) {
  const stageId = uniqueId(
    definition.stages.map((stage) => stage.stage_id),
    catalogItem.stage_kind === "task" ? "model" : "transform",
  );
  return {
    definition: {
      ...definition,
      stages: [...definition.stages, {
        stage_id: stageId,
        stage_kind: catalogItem.stage_kind,
        contract_id: catalogItem.contract_id,
      }],
    },
    stageId,
  };
}

export function removeStage(
  definition: ApiPredictionGraphDefinition,
  stageId: string,
) {
  return {
    ...definition,
    stages: definition.stages.filter((stage) => stage.stage_id !== stageId),
    bindings: definition.bindings.filter((binding) => (
      binding.target_stage_id !== stageId
      && !(binding.source.source_kind === "stage_output" && binding.source.stage_id === stageId)
    )),
    decision_outputs: definition.decision_outputs.filter((output) => output.source_stage_id !== stageId),
  };
}

export function moveStage(
  definition: ApiPredictionGraphDefinition,
  stageId: string,
  direction: -1 | 1,
) {
  const index = definition.stages.findIndex((stage) => stage.stage_id === stageId);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= definition.stages.length) return definition;
  const stages = [...definition.stages];
  [stages[index], stages[target]] = [stages[target], stages[index]];
  return { ...definition, stages };
}

function candidatePath(port: GraphPort) {
  if (port.value_kind === "sparse_blend") return "blend";
  if (port.path.startsWith("composition.") || port.path.startsWith("process.") || port.path.startsWith("categorical.")) {
    return port.path;
  }
  return `${port.value_kind === "categorical" ? "categorical" : "process"}.${port.quantity}`;
}

export function graphPresentationEdges(
  definition: ApiPredictionGraphDefinition,
): GraphPresentationEdge[] {
  return [
    ...definition.bindings.map((binding) => ({
      key: `binding:${binding.target_stage_id}:${binding.target_input_path}:${sourceKey(binding.source)}`,
      kind: "binding" as const,
      sourceKey: `source:${sourceKey(binding.source)}`,
      targetKey: `target:${binding.target_stage_id}:${binding.target_input_path}`,
    })),
    ...definition.decision_outputs.map((output) => ({
      key: `decision:${output.output_id}:${output.source_stage_id}:${output.source_output_key}`,
      kind: "decision_output" as const,
      sourceKey: `source:stage:${output.source_stage_id}:${output.source_output_key}`,
      targetKey: `decision:${output.output_id}`,
    })),
  ];
}

export function addInputAndBind(
  definition: ApiPredictionGraphDefinition,
  stageId: string,
  targetPort: GraphPort,
): ApiPredictionGraphDefinition {
  const inputId = uniqueId(
    definition.inputs.map((input) => input.input_id),
    `input-${targetPort.quantity}`,
  );
  const input: GraphInput = {
    input_id: inputId,
    label: targetPort.path,
    port: { ...targetPort, path: inputId },
    role: "design_variable",
    value_source: {
      source_kind: "candidate",
      candidate_path: candidatePath(targetPort),
    },
    required: true,
    default_presentation_group: "design",
  };
  const binding: GraphBinding = {
    target_stage_id: stageId,
    target_input_path: targetPort.path,
    source: { source_kind: "external", path: inputId },
    conversion: null,
  };
  return {
    ...definition,
    inputs: [...definition.inputs, input],
    bindings: [
      ...definition.bindings.filter((binding) => (
        binding.target_stage_id !== stageId
        || binding.target_input_path !== targetPort.path
      )),
      binding,
    ],
  };
}

export function setInputRole(
  definition: ApiPredictionGraphDefinition,
  inputId: string,
  role: GraphInput["role"],
) {
  return {
    ...definition,
    inputs: definition.inputs.map((input) => {
      if (input.input_id !== inputId || (role === "fixed_parameter" && input.port.value_kind === "sparse_blend")) {
        return input;
      }
      return {
        ...input,
        role,
        default_presentation_group: role === "design_variable" ? "design" : role === "scenario_context" ? "scenario" : "fixed",
        value_source: role === "fixed_parameter"
          ? {
              source_kind: "fixed_value" as const,
              value: input.port.value_kind === "number" ? 0 : "",
            }
          : {
              source_kind: "candidate" as const,
              candidate_path: input.value_source.source_kind === "candidate"
                ? input.value_source.candidate_path
                : candidatePath({ ...input.port, path: input.label }),
            },
      };
    }),
  };
}

export function removeInput(
  definition: ApiPredictionGraphDefinition,
  inputId: string,
) {
  return {
    ...definition,
    inputs: definition.inputs.filter((input) => input.input_id !== inputId),
    bindings: definition.bindings.filter((binding) => (
      binding.source.source_kind !== "external" || binding.source.path !== inputId
    )),
  };
}

function outgoingStages(definition: ApiPredictionGraphDefinition, stageId: string) {
  return definition.bindings.flatMap((binding) => (
    binding.source.source_kind === "stage_output"
    && binding.source.stage_id === stageId
      ? [binding.target_stage_id]
      : []
  ));
}

function reaches(
  definition: ApiPredictionGraphDefinition,
  fromStageId: string,
  targetStageId: string,
  visited = new Set<string>(),
): boolean {
  if (fromStageId === targetStageId) return true;
  if (visited.has(fromStageId)) return false;
  visited.add(fromStageId);
  return outgoingStages(definition, fromStageId).some((next) => (
    reaches(definition, next, targetStageId, visited)
  ));
}

export function compatibleSources(
  definition: ApiPredictionGraphDefinition,
  catalog: ApiPredictionGraphCatalog,
  targetStageId: string,
  targetPort: GraphPort,
) {
  const inputs: SourceOption[] = definition.inputs
    .filter((input) => samePort(input.port, targetPort))
    .map((input) => ({
      key: `input:${input.input_id}`,
      label: `Input · ${input.label}`,
      source: { source_kind: "external", path: input.input_id },
      port: input.port,
    }));
  const outputs: SourceOption[] = definition.stages.flatMap((stage) => {
    if (stage.stage_id === targetStageId || reaches(definition, targetStageId, stage.stage_id)) return [];
    const surface = stageCatalogItem(catalog, stage)?.surface;
    return (surface?.output_ports ?? [])
      .filter((port) => samePort(port, targetPort))
      .map((port) => ({
        key: `stage:${stage.stage_id}:${port.path}`,
        label: `${stage.stage_id}.${port.path}`,
        source: {
          source_kind: "stage_output" as const,
          stage_id: stage.stage_id,
          output_key: port.path,
        },
        port,
      }));
  });
  return [...inputs, ...outputs];
}

export function connectSource(
  definition: ApiPredictionGraphDefinition,
  catalog: ApiPredictionGraphCatalog,
  targetStageId: string,
  targetPort: GraphPort,
  source: BindingSource,
): { definition: ApiPredictionGraphDefinition; error?: string } {
  const allowed = compatibleSources(definition, catalog, targetStageId, targetPort);
  if (!allowed.some((option) => sourceKey(option.source) === sourceKey(source))) {
    return {
      definition,
      error: `${sourceLabel(source)} は ${targetStageId}.${targetPort.path} と型・依存関係が互換ではありません。`,
    };
  }
  const binding: GraphBinding = {
    target_stage_id: targetStageId,
    target_input_path: targetPort.path,
    source,
    conversion: null,
  };
  return {
    definition: {
      ...definition,
      bindings: [
        ...definition.bindings.filter((binding) => (
          binding.target_stage_id !== targetStageId
          || binding.target_input_path !== targetPort.path
        )),
        binding,
      ],
    },
  };
}

export function removeBinding(
  definition: ApiPredictionGraphDefinition,
  stageId: string,
  portPath: string,
) {
  return {
    ...definition,
    bindings: definition.bindings.filter((binding) => (
      binding.target_stage_id !== stageId || binding.target_input_path !== portPath
    )),
  };
}

export function addDecisionOutput(
  definition: ApiPredictionGraphDefinition,
  stageId: string,
  port: GraphPort,
): ApiPredictionGraphDefinition {
  if (definition.decision_outputs.some((output) => (
    output.source_stage_id === stageId && output.source_output_key === port.path
  ))) return definition;
  const outputId = uniqueId(
    definition.decision_outputs.map((output) => output.output_id),
    `decision-${port.path}`,
  );
  const primary = definition.decision_outputs.length === 0;
  const output: GraphDecisionOutput = {
    output_id: outputId,
    source_stage_id: stageId,
    source_output_key: port.path,
    label: port.path,
    group: "decision",
    role: primary ? "primary_objective" : "diagnostic",
    required_for_complete_result: primary,
  };
  return {
    ...definition,
    decision_outputs: [...definition.decision_outputs, output],
  };
}

export function removeDecisionOutput(
  definition: ApiPredictionGraphDefinition,
  outputId: string,
) {
  return {
    ...definition,
    decision_outputs: definition.decision_outputs.filter((output) => output.output_id !== outputId),
  };
}

export function topologicalLayers(definition: ApiPredictionGraphDefinition) {
  const stageIds = definition.stages.map((stage) => stage.stage_id);
  const incoming = new Map(stageIds.map((id) => [id, new Set<string>()]));
  for (const binding of definition.bindings) {
    if (binding.source.source_kind === "stage_output") {
      incoming.get(binding.target_stage_id)?.add(binding.source.stage_id);
    }
  }
  const remaining = new Set(stageIds);
  const layers: string[][] = [];
  while (remaining.size) {
    const layer = stageIds.filter((id) => (
      remaining.has(id)
      && [...(incoming.get(id) ?? [])].every((source) => !remaining.has(source))
    ));
    if (!layer.length) return [stageIds];
    layers.push(layer);
    layer.forEach((id) => remaining.delete(id));
  }
  return layers;
}

export function initializeGraph(catalog: ApiPredictionGraphCatalog) {
  const first = catalog.stages.find((item) => item.status === "available" && item.surface);
  if (!first?.surface) return emptyPredictionGraph();
  const added = addStage(emptyPredictionGraph(), first);
  let definition = added.definition;
  for (const port of first.surface.input_ports) {
    definition = addInputAndBind(definition, added.stageId, port);
  }
  if (first.surface.output_ports[0]) {
    definition = addDecisionOutput(definition, added.stageId, first.surface.output_ports[0]);
  }
  return definition;
}
