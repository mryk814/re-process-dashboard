import type { CandidateViewModel, TaskDefinitionContract } from "../../candidates";
import type {
  ApiDecisionActivityAvailability,
  ApiDecisionActivityRun,
  ApiDecisionActivityRunRequest,
} from "../../../shared/api/workbench-api";

export type DecisionActivityParameters = ApiDecisionActivityRunRequest["parameters"];

/** Every activity view receives the same shell state and the same run callback. */
export type DecisionActivityViewProps = {
  projectId: string;
  candidate: CandidateViewModel;
  candidates: CandidateViewModel[];
  taskDefinition: TaskDefinitionContract;
  ready: boolean;
  availability: ApiDecisionActivityAvailability;
  runs: ApiDecisionActivityRun[];
  running: boolean;
  onRun: (parameters: DecisionActivityParameters) => Promise<void>;
};
