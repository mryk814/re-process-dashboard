import type {
  ApiChainTemplate,
  ApiProject,
  ApiSubsystemAvailability,
} from "../../shared/api/workbench-api";

type ChainIdentity = Extract<
  NonNullable<ApiProject["scientific_identity"]>,
  { identity_kind: "chain" }
>;

export type ActiveChainContext =
  | { status: "not-chain" }
  | { status: "loading" }
  | { status: "error" }
  | { status: "offline" }
  | { status: "unresolved"; chainRevisionId: string }
  | {
    status: "available";
    revision: ApiChainTemplate["revisions"][number];
  }
  | {
    status: "unavailable";
    revision: ApiChainTemplate["revisions"][number];
    availability: ApiSubsystemAvailability;
  };

export function resolveActiveChainContext({
  identity,
  templates,
  templatesLoaded,
  availability,
  availabilityLoaded,
  availabilityError,
  offline,
}: {
  identity: ChainIdentity | null;
  templates: ApiChainTemplate[];
  templatesLoaded: boolean;
  availability: ApiSubsystemAvailability[];
  availabilityLoaded: boolean;
  availabilityError: boolean;
  offline: boolean;
}): ActiveChainContext {
  if (!identity) return { status: "not-chain" };
  if (offline) return { status: "offline" };
  if (!templatesLoaded || !availabilityLoaded) return { status: "loading" };
  if (availabilityError) return { status: "error" };
  const revision = templates
    .flatMap((template) => template.revisions)
    .find((item) => (
      `${item.chain_id}:r${item.revision}` === identity.chain_revision_id
    ));
  if (!revision) {
    return {
      status: "unresolved",
      chainRevisionId: identity.chain_revision_id,
    };
  }
  const selectedAvailability = availability.find((item) => (
    item.kind === "chain"
    && item.owner_kind === "chain"
    && item.owner_resource_id === revision.chain_id
  ));
  return selectedAvailability?.status === "unavailable"
    ? {
      status: "unavailable",
      revision,
      availability: selectedAvailability,
    }
    : { status: "available", revision };
}
