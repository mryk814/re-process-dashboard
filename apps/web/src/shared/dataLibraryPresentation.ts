import type {
  ApiDataLibraryDataset,
  ApiDatasetView,
  ApiModelPackageRef,
  ApiProject,
  ApiProjectCreationOptions,
} from "./api/workbench-api";

const asRecord = (value: unknown): Record<string, unknown> | null => (
  value != null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

const standardTrainingMetadata = (
  predictor: Record<string, unknown>,
): Record<string, unknown> | null => {
  const config = asRecord(predictor.config);
  const training = asRecord(config?.training);
  return training?.schema_version === "standard-training-metadata/v1"
    ? training
    : null;
};

export function trainingDataSha(modelPackage: ApiModelPackageRef): string | null {
  const provenance = asRecord(asRecord(modelPackage.manifest_json)?.provenance);
  const trainingDataId = provenance?.training_data_id;
  if (typeof trainingDataId !== "string" || !trainingDataId.startsWith("sha256:")) return null;
  return trainingDataId.slice("sha256:".length);
}

export function trainingProfileDigest(modelPackage: ApiModelPackageRef): string | null {
  const provenance = asRecord(asRecord(modelPackage.manifest_json)?.provenance);
  return typeof provenance?.dataset_profile_id === "string"
    ? provenance.dataset_profile_id
    : null;
}

export function trainingDataset(
  modelPackage: ApiModelPackageRef | undefined,
  datasets: ApiDataLibraryDataset[],
): ApiDataLibraryDataset | undefined {
  if (!modelPackage) return undefined;
  const sha = trainingDataSha(modelPackage);
  const profileDigest = trainingProfileDigest(modelPackage);
  return sha ? datasets.find((item) => (
    item.data_asset.sha256 === sha
    && (profileDigest == null || item.profile_revision.profile_digest === profileDigest)
  )) : undefined;
}

export function datasetDisplayName(dataset: ApiDataLibraryDataset | undefined): string {
  if (!dataset) return "Dataset未解決";
  const source = dataset.data_asset.original_filename.replace(/\.(xlsx|csv)$/i, "");
  return `${source} · ${dataset.profile_revision.name}`;
}

export type ProjectDatasetChoice = {
  id: string;
  label: string;
  purposeLabel: string;
  sourceLabel: string;
  createdAt: string;
  usageCount: number;
  projectNames: string[];
  group: "used" | "unused";
};

type ProjectDatasetChoiceInput = {
  datasets: ApiDataLibraryDataset[];
  views: ApiDatasetView[];
  projects: Array<Pick<ApiProject, "archived_at" | "dataset_view_revision_id" | "id" | "name">>;
  taskLabels: ReadonlyMap<string, string>;
  chainLabelsByViewId?: ReadonlyMap<string, readonly string[]>;
  datasetViewIdsByProjectId?: ReadonlyMap<string, readonly string[]>;
};

const registrationLabel = (createdAt: string): string => {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return "登録日時不明";
  return `登録 ${date.toLocaleString("ja-JP", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })}`;
};

export function projectDatasetChoices({
  datasets,
  views,
  projects,
  taskLabels,
  chainLabelsByViewId = new Map(),
  datasetViewIdsByProjectId = new Map(),
}: ProjectDatasetChoiceInput): ProjectDatasetChoice[] {
  const datasetByViewId = new Map(
    datasets.flatMap((dataset) => (dataset.dataset_views ?? []).map((view) => [view.id, dataset] as const)),
  );
  const activeProjects = projects.filter((project) => !project.archived_at);
  const provisional = views
    .filter((view) => view.kind === "single")
    .map((view) => {
      const dataset = datasetByViewId.get(view.id);
      const sourceLabel = dataset?.data_asset.original_filename.replace(/\.(xlsx|csv)$/i, "") ?? view.name;
      const taskPurposes = (dataset?.supported_task_ids ?? []).map(
        (taskId) => taskLabels.get(taskId) ?? taskId,
      );
      const chainPurposes = chainLabelsByViewId.get(view.id) ?? [];
      const purposes = [...new Set([...taskPurposes, ...chainPurposes])];
      const purposeLabel = purposes.length > 0 ? purposes.join("・") : view.name;
      const projectNames = activeProjects
        .filter((project) => (
          project.dataset_view_revision_id === view.id
          || datasetViewIdsByProjectId.get(project.id)?.includes(view.id)
        ))
        .map((project) => project.name)
        .sort((left, right) => left.localeCompare(right, "ja"));
      return {
        id: view.id,
        baseLabel: `${purposeLabel} — ${sourceLabel}`,
        purposeLabel,
        sourceLabel,
        createdAt: view.created_at,
        usageCount: projectNames.length,
        projectNames,
        group: projectNames.length > 0 ? "used" as const : "unused" as const,
      };
    });
  const duplicateCounts = new Map<string, number>();
  for (const choice of provisional) {
    duplicateCounts.set(choice.baseLabel, (duplicateCounts.get(choice.baseLabel) ?? 0) + 1);
  }
  const uniqueSuffix = (choiceId: string, duplicateIds: string[]): string => {
    let length = Math.min(6, choiceId.length);
    while (
      length < choiceId.length
      && duplicateIds.some((otherId) => otherId !== choiceId && otherId.endsWith(choiceId.slice(-length)))
    ) {
      length = Math.min(length + 2, choiceId.length);
    }
    return choiceId.slice(-length);
  };
  return provisional
    .map(({ baseLabel, ...choice }) => {
      const useLabel = choice.usageCount > 0 ? `利用中${choice.usageCount}件` : "未使用";
      const duplicateIds = provisional.filter((item) => item.baseLabel === baseLabel).map((item) => item.id);
      const duplicateLabel = duplicateIds.length > 1
        ? `・${registrationLabel(choice.createdAt)}・…${uniqueSuffix(choice.id, duplicateIds)}`
        : "";
      return {
        ...choice,
        label: `${baseLabel}（${useLabel}${duplicateLabel}）`,
      };
    })
    .sort((left, right) => (
      right.usageCount - left.usageCount
      || Date.parse(right.createdAt) - Date.parse(left.createdAt)
      || left.label.localeCompare(right.label, "ja")
      || left.id.localeCompare(right.id)
    ));
}

export function modelPackageDisplayName(modelPackage: ApiModelPackageRef | undefined): string {
  if (!modelPackage) return "—";
  const manifest = asRecord(modelPackage.manifest_json);
  const predictors = Array.isArray(manifest?.predictors) ? manifest.predictors : [];
  const records = predictors.map(asRecord).filter((item): item is Record<string, unknown> => item != null);
  const estimatorIds = new Set(modelPackageEstimatorIds(modelPackage));
  const runtimeTypes = new Set(records.map((item) => item.runtime_type).filter((item): item is string => typeof item === "string"));
  const architectureIds = new Set(records.map((item) => item.architecture_id).filter((item): item is string => typeof item === "string"));
  const family = estimatorIds.has("lightgbm-binary.v1")
    ? "LightGBM（二値分類）"
    : estimatorIds.has("lightgbm-regression.v1")
      ? "LightGBM回帰"
      : estimatorIds.has("bayesian-additive-spline.v1")
        ? "Bayesian加法スプライン"
      : estimatorIds.has("quantile-linear-regression.v1")
        ? "線形分位点回帰"
      : estimatorIds.has("exact-gp-rbf.v1")
        ? "GP（Exact RBF）"
        : estimatorIds.has("ridge.v1")
          ? "Ridge回帰"
          : records.some((item) => item.runtime_type === "builtin.heteroscedastic_exact_gp.v1")
    ? "異分散GP（試験・個々値）"
    : records.some((item) => item.architecture_id === "hierarchical_parent_random_intercept_v1")
      ? "階層線形モデル（試験・個々値）"
      : [...architectureIds].some((item) => item.toLowerCase().includes("lightgbm"))
        ? "LightGBM"
        : records.some((item) => asRecord(item.config)?.kernel === "ARD-RBF")
          ? "GP（安定ARD）"
          : runtimeTypes.has("builtin.exact_gp.v1")
            ? "GP"
            : runtimeTypes.has("builtin.posterior_linear.v1")
              ? "Bayes線形回帰"
              : runtimeTypes.has("builtin.linear.v1")
                ? "線形回帰"
                : modelPackage.package_id;
  const version = typeof manifest?.package_version === "string"
    ? manifest.package_version.replace(/^v/i, "")
    : "";
  return version ? `${family} · v${version}` : family;
}

export function modelPackageEstimatorIds(modelPackage: ApiModelPackageRef | undefined): string[] {
  if (!modelPackage) return [];
  const manifest = asRecord(modelPackage.manifest_json);
  const predictors = Array.isArray(manifest?.predictors) ? manifest.predictors : [];
  return [...new Set(predictors.map(asRecord)
    .filter((item): item is Record<string, unknown> => item != null)
    .map((item) => standardTrainingMetadata(item)?.estimator_id)
    .filter((item): item is string => typeof item === "string"))];
}

export function modelPackageDisplayNames(
  modelPackages: ApiModelPackageRef[],
): Map<string, string> {
  const baseNames = new Map(
    modelPackages.map((modelPackage) => [
      modelPackage.id,
      modelPackageDisplayName(modelPackage),
    ]),
  );
  const counts = new Map<string, number>();
  for (const name of baseNames.values()) {
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  return new Map(modelPackages.map((modelPackage) => {
    const base = baseNames.get(modelPackage.id) ?? modelPackage.package_id;
    return [
      modelPackage.id,
      counts.get(base) === 1 ? base : `${base} · ${modelPackage.package_id}`,
    ];
  }));
}

export type ModelPackageDecisionSummary = {
  label: string;
  useCase: string;
  trainingUnit: string;
  uncertainty: string;
  experimental: boolean;
  caution: string;
};

export function modelPackageDecisionSummary(
  modelPackage: ApiModelPackageRef | undefined,
): ModelPackageDecisionSummary | null {
  if (!modelPackage) return null;
  const manifest = asRecord(modelPackage.manifest_json);
  const predictors = Array.isArray(manifest?.predictors) ? manifest.predictors : [];
  const records = predictors.map(asRecord).filter((item): item is Record<string, unknown> => item != null);
  const configs = records.map((item) => asRecord(item.config)).filter(
    (item): item is Record<string, unknown> => item != null,
  );
  const standardTraining = records.map(standardTrainingMetadata).filter(
    (item): item is Record<string, unknown> => item != null,
  );
  const estimatorIds = new Set(standardTraining.map((item) => item.estimator_id).filter(
    (item): item is string => typeof item === "string",
  ));
  const runtimeTypes = new Set(records.map((item) => item.runtime_type).filter(
    (item): item is string => typeof item === "string",
  ));
  const architectures = new Set(records.map((item) => item.architecture_id).filter(
    (item): item is string => typeof item === "string",
  ));
  const trainingUnits = new Set([
    ...standardTraining.map((item) => item.training_unit),
    ...configs.map((item) => item.training_unit),
  ].filter(
    (item): item is string => typeof item === "string",
  ));
  const experimental = configs.some((item) => item.experimental === true)
    || (typeof manifest?.package_version === "string" && manifest.package_version.includes("experimental"));
  const trainingUnit = trainingUnits.has("individual_observation")
    ? "個々の測定値"
    : trainingUnits.has("parent_condition_mean")
      ? "条件ごとの平均値"
      : trainingUnits.has("replicate_context_mean")
        ? "同一条件の反復平均"
      : "Package定義を確認";
  const declaredUncertainty = standardTraining
    .map((item) => item.uncertainty)
    .find((item): item is string => typeof item === "string");

  if (estimatorIds.has("lightgbm-binary.v1")) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "事象確率を非線形モデルで比較したいとき",
      trainingUnit,
      uncertainty: declaredUncertainty ?? "交差検証外予測で確率を校正",
      experimental,
      caution: "同じcohort・foldの評価と科学的妥当性を確認して採用します。",
    };
  }
  if (estimatorIds.has("lightgbm-regression.v1")) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "非線形な関係をRidgeなどと比較したいとき",
      trainingUnit,
      uncertainty: declaredUncertainty ?? "交差検証外残差に基づく区間",
      experimental,
      caution: "同じFeatureDataset・cohort・foldの結果だけを比較します。",
    };
  }
  if (estimatorIds.has("bayesian-additive-spline.v1")) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "入力ごとの滑らかな非線形主効果を読みながら比較したいとき",
      trainingUnit,
      uncertainty: declaredUncertainty
        ?? "平均関数の信用区間と新しい観測値の予測区間",
      experimental,
      caution: "interactionは学習しません。相関した入力のterm形状は不安定になり得て、因果効果でも独立介入効果でもありません。",
    };
  }
  if (estimatorIds.has("quantile-linear-regression.v1")) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "中央値と非対称・入力依存の予測幅を直接比較したいとき",
      trainingUnit,
      uncertainty: declaredUncertainty
        ?? "条件付きq05／q50／q95（実測coverageは別評価）",
      experimental,
      caution: "q05–q95は正規分布の90%区間ではありません。分位点交差は補正せず、その入力を利用不能として扱います。",
    };
  }
  if (estimatorIds.has("exact-gp-rbf.v1")) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "滑らかな傾向と予測分布を確認したいとき",
      trainingUnit,
      uncertainty: declaredUncertainty ?? "正規予測分布",
      experimental,
      caution: "学習件数上限と学習範囲の支持を確認して使います。",
    };
  }
  if (estimatorIds.has("ridge.v1")) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "解釈しやすい線形基準と比較したいとき",
      trainingUnit,
      uncertainty: declaredUncertainty ?? "交差検証外残差に基づく区間",
      experimental,
      caution: "自動winnerは選ばず、同一評価条件で候補モデルを比較します。",
    };
  }

  if (runtimeTypes.has("builtin.heteroscedastic_exact_gp.v1")) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "反復測定のばらつきも判断したいとき",
      trainingUnit,
      uncertainty: "条件間の傾向と条件内ばらつきを分けて近似",
      experimental,
      caution: "試験実装です。標準GPと評価対象・区間の意味が同じとは限りません。",
    };
  }
  if (architectures.has("hierarchical_parent_random_intercept_v1")) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "親条件と反復測定の階層を試したいとき",
      trainingUnit,
      uncertainty: "親条件差と個々値ばらつきを階層線形モデルで近似",
      experimental,
      caution: "階層線形の試験モデルです。汎用的な階層Bayesモデルを意味しません。",
    };
  }
  if ([...architectures].some((item) => item.toLowerCase().includes("lightgbm"))) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "非線形な点予測を比較したいとき",
      trainingUnit,
      uncertainty: "残差に基づく区間。確率モデルの事後分布ではありません",
      experimental,
      caution: "GPとは評価法が異なるため、RMSEだけで自動的に優劣を決めません。",
    };
  }
  if (runtimeTypes.has("builtin.exact_gp.v1")) {
    return {
      label: modelPackageDisplayName(modelPackage),
      useCase: "まず候補の傾向と不確かさを比較するとき",
      trainingUnit,
      uncertainty: "条件平均についての予測分布",
      experimental,
      caution: "反復測定の個々のばらつきではなく、主に条件平均を扱います。",
    };
  }
  return {
    label: modelPackageDisplayName(modelPackage),
    useCase: "手法の違いを比較するとき",
    trainingUnit,
    uncertainty: "Packageの技術情報を確認",
    experimental,
    caution: "同じDataset・Prediction Task・評価法の結果だけを比較してください。",
  };
}

export function modelPackageTrainedOnDataset(
  modelPackage: ApiModelPackageRef,
  dataset: ApiDataLibraryDataset | undefined,
): boolean {
  if (!dataset) return false;
  return (
    trainingDataSha(modelPackage) === dataset.data_asset.sha256
    && trainingProfileDigest(modelPackage) === dataset.profile_revision.profile_digest
  );
}

export function compatiblePackagesForDatasetTask(
  dataset: ApiDataLibraryDataset | undefined,
  taskId: string,
  options: Pick<ApiProjectCreationOptions, "model_packages" | "task_contract_digests">,
): ApiModelPackageRef[] {
  const currentDigest = options.task_contract_digests[taskId];
  if (!dataset || !currentDigest) return [];
  return options.model_packages.filter((item) => (
    item.task_id === taskId && item.task_contract_digest === currentDigest
    && modelPackageTrainedOnDataset(item, dataset)
  ));
}

export function compatibleTaskIdsForDataset(
  dataset: ApiDataLibraryDataset | undefined,
  options: Pick<ApiProjectCreationOptions, "model_packages" | "task_contract_digests">,
): string[] {
  return (dataset?.supported_task_ids ?? []).filter((taskId) => (
    compatiblePackagesForDatasetTask(dataset, taskId, options).length > 0
  ));
}
