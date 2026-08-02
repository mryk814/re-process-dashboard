import type {
  ApiChainTemplate,
  ApiProject,
  ApiSubsystemAvailability,
} from "../../shared/api/workbench-api";

type ChainIdentity = Extract<
  NonNullable<ApiProject["scientific_identity"]>,
  { identity_kind: "chain" }
>;

export type ExecutableChainDefinition = Extract<
  ApiChainTemplate["definition"],
  { schema_version: "chain-definition/v1" }
>;

export type ExecutableChainRevision = Extract<
  ApiChainTemplate["revisions"][number],
  { schema_version: "chain-revision/v1" }
>;

export function isExecutableChainDefinition(
  definition: ApiChainTemplate["definition"],
): definition is ExecutableChainDefinition {
  return definition.schema_version === "chain-definition/v1";
}

export function isExecutableChainRevision(
  revision: ApiChainTemplate["revisions"][number],
): revision is ExecutableChainRevision {
  return revision.schema_version === "chain-revision/v1";
}

export function resolveFixedChain(
  identity: ChainIdentity | null,
  templates: ApiChainTemplate[],
) {
  if (!identity) return {};
  const template = templates.find((item) => (
    isExecutableChainDefinition(item.definition)
    && item.revisions.some((revision) => (
      isExecutableChainRevision(revision)
      && `${revision.chain_id}:r${revision.revision}` === identity.chain_revision_id
    ))
  ));
  const revision = template?.revisions.find(
    (item): item is ExecutableChainRevision => (
      isExecutableChainRevision(item)
      && `${item.chain_id}:r${item.revision}` === identity.chain_revision_id
    ),
  );
  return {
    template: template && isExecutableChainDefinition(template.definition)
      ? template
      : undefined,
    revision,
  };
}

export function chainStagePath(
  revision: ExecutableChainRevision | undefined,
) {
  return revision?.stages.map((stage) => stage.stage_id).join(" → ") || "Stage未解決";
}

export function chainAvailability(
  items: ApiSubsystemAvailability[],
  chainId: string | undefined,
  kind: "chain" | "chain_evaluation",
) {
  if (!chainId) return undefined;
  return items.find((item) => (
    item.kind === kind
    && item.owner_kind === "chain"
    && item.owner_resource_id === chainId
  ));
}

export type ProjectOperation = "metadata" | "prediction" | "destructive";

export function projectOperationDisabled({
  operation,
  offline,
  archived,
  pending,
  taskUnavailable,
  subsystemUnavailable,
}: {
  operation: ProjectOperation;
  offline: boolean;
  archived?: boolean;
  pending?: boolean;
  taskUnavailable?: boolean;
  subsystemUnavailable?: boolean;
}) {
  if (offline || pending) return true;
  if (operation === "destructive") return Boolean(archived);
  if (operation === "prediction") {
    return Boolean(archived || taskUnavailable || subsystemUnavailable);
  }
  return false;
}
