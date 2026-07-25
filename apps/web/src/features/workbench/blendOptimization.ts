export type BlendTargetDraft = {
  id: number;
  component: string;
  lower: string;
  upper: string;
};

export type BlendCompositionTarget = {
  component: string;
  lower: number;
  upper: number;
};

export function nextBlendTarget(
  components: string[],
  targets: BlendTargetDraft[],
  id: number,
): BlendTargetDraft | null {
  const used = new Set(targets.map((target) => target.component));
  const component = components.find((item) => !used.has(item));
  return component ? { id, component, lower: "0", upper: "100" } : null;
}

export function availableBlendTargetComponents(
  components: string[],
  targets: BlendTargetDraft[],
  targetId: number,
): string[] {
  const usedElsewhere = new Set(
    targets
      .filter((target) => target.id !== targetId)
      .map((target) => target.component),
  );
  return components.filter((component) => !usedElsewhere.has(component));
}

export function blendTargetValidationError(targets: BlendTargetDraft[]): string {
  if (targets.length === 0) return "成分許容範囲を1件以上指定してください";
  const components = targets.map((target) => target.component);
  if (components.some((component) => !component)) return "成分を選択してください";
  if (new Set(components).size !== components.length) return "同じ成分は1回だけ指定してください";
  for (const target of targets) {
    const lower = Number(target.lower);
    const upper = Number(target.upper);
    if (
      !Number.isFinite(lower)
      || !Number.isFinite(upper)
      || lower < 0
      || upper > 100
      || lower > upper
    ) {
      return `${target.component} の許容範囲を0〜100の昇順で指定してください`;
    }
  }
  return "";
}

export function serializeBlendTargets(
  targets: BlendTargetDraft[],
): BlendCompositionTarget[] {
  const validationError = blendTargetValidationError(targets);
  if (validationError) throw new Error(validationError);
  return targets.map((target) => ({
    component: target.component,
    lower: Number(target.lower),
    upper: Number(target.upper),
  }));
}
