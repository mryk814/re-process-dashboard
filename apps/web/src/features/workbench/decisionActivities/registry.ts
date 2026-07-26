import type { ComponentType } from "react";
import { CandidateDifferenceActivityView } from "./CandidateDifferenceActivityView";
import { RobustnessActivityView } from "./RobustnessActivityView";
import { CounterfactualActivityView } from "./CounterfactualActivityView";
import type { DecisionActivityViewProps } from "./types";

/**
 * Activity id -> its own surface. Adding an activity means adding one entry and
 * one component; it must not change an existing activity's view.
 */
export const DECISION_ACTIVITY_VIEWS: Record<string, ComponentType<DecisionActivityViewProps>> = {
  "robustness-analysis-v1": RobustnessActivityView,
  "candidate-difference-v1": CandidateDifferenceActivityView,
  "counterfactual-target-reach-v1": CounterfactualActivityView,
};

export function decisionActivityView(
  activityId: string,
): ComponentType<DecisionActivityViewProps> | null {
  return DECISION_ACTIVITY_VIEWS[activityId] ?? null;
}
