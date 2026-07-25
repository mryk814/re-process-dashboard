import type { ApiCandidate, ApiCandidateInput } from "../../shared/api/workbench-api";

export type HeatTimeBasis = "line_speed" | "elapsed_time";

export type CandidateViewModel = {
  raw: ApiCandidate;
  id: string;
  label: string;
  heatTimeBasis: HeatTimeBasis;
  heat: Array<{
    time: number;
    temperature: number;
    segmentStart?: boolean;
    stageName?: string;
    stageCategory?: string;
  }>;
};

export function fromApiCandidate(candidate: ApiCandidate): CandidateViewModel {
  return {
    raw: candidate,
    id: candidate.id,
    label: candidate.name,
    heatTimeBasis: candidate.inputs.heat_time_basis ?? "line_speed",
    heat: (candidate.inputs.heat_pattern ?? []).map((point) => ({
      time: point.time_s / 60,
      temperature: point.temperature_c,
      segmentStart: point.segment_start,
      stageName: point.stage_name ?? undefined,
      stageCategory: point.stage_category ?? undefined,
    })),
  };
}

export function scaleHeatTimesForLineSpeed(
  heat: CandidateViewModel["heat"],
  oldSpeed: number,
  newSpeed: number,
): CandidateViewModel["heat"] {
  if (!Number.isFinite(oldSpeed) || oldSpeed <= 0 || !Number.isFinite(newSpeed) || newSpeed <= 0 || heat.length === 0) {
    return heat;
  }
  const origin = heat[0].time;
  const scale = oldSpeed / newSpeed;
  return heat.map((point) => ({
    ...point,
    time: origin + (point.time - origin) * scale,
  }));
}

export function toApiCandidate(candidate: CandidateViewModel): ApiCandidateInput {
  return {
    name: candidate.label,
    inputs: {
      composition: candidate.raw.inputs.composition,
      process: candidate.raw.inputs.process,
      categorical: candidate.raw.inputs.categorical,
      heat_time_basis: candidate.heatTimeBasis,
      ...(candidate.raw.inputs.heat_pattern === null && candidate.heat.length === 0
        ? { heat_pattern: null }
        : {
            heat_pattern: candidate.heat.map((point, index) => ({
              ...candidate.raw.inputs.heat_pattern?.[index],
              time_s: point.time * 60,
              temperature_c: point.temperature,
              segment_start: point.segmentStart ?? false,
              stage_name: point.stageName?.trim() || null,
              ...(point.stageCategory ? { stage_category: point.stageCategory } : {}),
            })),
          }),
    },
    blend: candidate.raw.blend,
    editor_state: candidate.raw.editor_state,
    blend_validation: candidate.raw.blend_validation,
    provenance: candidate.raw.provenance,
  };
}

export function candidateSaveContractError(
  saved: ApiCandidate,
  requested: ApiCandidateInput,
): string | null {
  if (saved.inputs.heat_time_basis !== requested.inputs.heat_time_basis) {
    return "起動中のAPIが時間基準の保存に対応していません。開発サーバーまたはアプリを再起動してください";
  }
  if (JSON.stringify(saved.blend ?? null) !== JSON.stringify(requested.blend ?? null)) {
    return "起動中のAPIが配合明細の保存に対応していません。開発サーバーまたはアプリを再起動してください";
  }
  if (JSON.stringify(saved.editor_state?.locked_material_ids ?? []) !== JSON.stringify(requested.editor_state?.locked_material_ids ?? [])) {
    return "配合行のlock状態を保存できませんでした";
  }
  return null;
}
