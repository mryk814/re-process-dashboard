import { PointerEvent, useEffect, useRef, useState } from "react";

type Tab = "project" | "candidates" | "quality" | "lineage" | "explore";
type Candidate = {
  raw: ApiCandidate;
  id: string;
  label: string;
  c: number;
  mn: number;
  si: number;
  thickness: number;
  lineSpeed: number;
  coating: string;
  annealTemperature: number;
  holdMinutes: number;
  heat: Array<{ time: number; temperature: number; segmentStart?: boolean }>;
};

type Metric = {
  key: string;
  unit: string;
  value: number;
  low: number;
  high: number;
  status: string;
  goalValue?: number | null;
  goalProbability?: number | null;
};

type ApiPreview = {
  predictions?: Record<
    string,
    {
      value: number;
      lower: number;
      upper: number;
      unit: string;
      goal_value?: number | null;
      goal_probability?: number | null;
      goal_direction?: "at_least" | "at_most" | null;
    }
  >;
  support?: {
    status?: "supported" | "caution" | "extrapolated";
    message?: string;
    distance?: number;
    percentile?: number;
    components?: Record<string, number>;
  };
  warnings?: string[];
  model_meta?: {
    package?: {
      id?: string;
      version?: string;
      manifest_sha256?: string;
      runtime_types?: string[];
    };
    model?: { id?: string; version?: string; method?: string };
    feature_pipeline?: {
      id?: string;
      version?: string;
      input_schema_version?: string;
      features?: string[];
    };
    training_data?: {
      source_path?: string;
      source_sha256?: string;
      records?: Record<string, number>;
    };
    prediction_interval?: {
      method?: string;
      coverage?: number;
      grouping?: string;
      folds?: number;
      note?: string;
    };
    similarity?: { version?: string; method?: string };
  };
  similar?: Array<{
    observation_id: string;
    parent_key: string;
    source: string;
    layer?: "training" | "historical";
    distance: number;
    components?: Record<string, number>;
    outputs: Record<string, number>;
    repeat_summary?: Record<string, { mean: number; std: number; n: number }>;
  }>;
  response_curve?: Array<{
    temperature_c: number;
    value: number;
    lower: number;
    upper: number;
  }>;
};

type ApiCandidate = {
  id: string;
  project_id?: string;
  name: string;
  composition: Record<string, number> & { C: number; Si: number; Mn: number };
  thickness_mm: number;
  line_speed_m_min: number;
  coating: string;
  heat_pattern: Array<{
    time_s: number;
    temperature_c: number;
    segment_start?: boolean;
  }>;
};

type ApiProject = {
  id: string;
  name: string;
  description: string;
  purpose: string;
  task_id: string;
  target_values: Record<string, number>;
  notes: string;
  decision_candidate_id: string;
  decision_snapshot_id: string;
  decision_note: string;
  created_at: string;
  updated_at: string;
};

type ApiModelPackage = {
  id: string;
  version: string;
  task_id: string;
  manifest_sha256: string;
  active_runtimes: string[];
  supported_runtimes: Array<{ runtime_type: string; available: boolean }>;
  predictors: Array<{
    target: string;
    runtime_type: string;
    predictive_family: string;
  }>;
};

type ApiSnapshot = {
  id: string;
  created_at: string;
  payload: {
    prediction?: ApiPreview;
    provenance?: ApiPreview["model_meta"];
  };
};

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8765";
const COMPOSITION_ELEMENTS = [
  "C",
  "Si",
  "Mn",
  "P",
  "S",
  "Cr",
  "Mo",
  "Ni",
  "Al",
  "Ti",
  "B",
  "N",
  "O",
  "Ca",
] as const;

const STARTER_CANDIDATE: Omit<ApiCandidate, "id"> = {
  name: "基準候補",
  composition: {
    C: 0.08,
    Si: 0.3,
    Mn: 1.5,
    P: 0.012,
    S: 0.004,
    Cr: 0.2,
    Mo: 0.03,
    Ni: 0.1,
    Al: 0.04,
    Ti: 0.02,
    B: 0.002,
    N: 0.005,
    O: 0.002,
    Ca: 0.001,
  },
  thickness_mm: 1.4,
  line_speed_m_min: 103,
  coating: "GI",
  heat_pattern: [
    { time_s: 0, temperature_c: 25 },
    { time_s: 280, temperature_c: 800 },
    { time_s: 340, temperature_c: 810 },
    { time_s: 650, temperature_c: 120 },
  ],
};

const navItems: Array<{ id: Tab; label: string }> = [
  { id: "project", label: "プロジェクト" },
  { id: "candidates", label: "候補比較" },
  { id: "quality", label: "データ品質" },
  { id: "lineage", label: "工程系譜" },
  { id: "explore", label: "範囲探索" },
];

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

async function apiError(response: Response, fallback: string): Promise<Error> {
  try {
    const body = (await response.json()) as { detail?: string };
    return new Error(body.detail || fallback);
  } catch {
    return new Error(fallback);
  }
}

function fieldValue(candidate: Candidate, field: keyof Candidate) {
  const value = candidate[field];
  return typeof value === "number" ? value : "";
}

function fromApiCandidate(candidate: ApiCandidate): Candidate {
  const heat = candidate.heat_pattern.map((point) => ({
    time: point.time_s / 60,
    temperature: point.temperature_c,
    segmentStart: point.segment_start,
  }));
  const max = heat.length
    ? Math.max(...heat.map((point) => point.temperature))
    : 0;
  const plateau = heat.filter((point) => point.temperature >= max * 0.95);
  const holdMinutes =
    plateau.length > 1 ? plateau.at(-1)!.time - plateau[0].time : 0;
  return {
    raw: candidate,
    id: candidate.id,
    label: candidate.name,
    c: candidate.composition.C,
    mn: candidate.composition.Mn,
    si: candidate.composition.Si,
    thickness: candidate.thickness_mm,
    lineSpeed: candidate.line_speed_m_min,
    coating: candidate.coating,
    annealTemperature: max,
    holdMinutes,
    heat,
  };
}

function toApiCandidate(
  candidate: Candidate,
): Omit<ApiCandidate, "id"> & { id?: string } {
  return {
    ...candidate.raw,
    id: candidate.id,
    name: candidate.label,
    composition: {
      ...candidate.raw.composition,
      C: candidate.c,
      Si: candidate.si,
      Mn: candidate.mn,
    },
    thickness_mm: candidate.thickness,
    line_speed_m_min: candidate.lineSpeed,
    coating: candidate.coating,
    heat_pattern: candidate.heat.map((point) => ({
      time_s: point.time * 60,
      temperature_c: point.temperature,
      segment_start: point.segmentStart ?? false,
    })),
  };
}

function metricsFromPreview(preview: ApiPreview): Metric[] {
  return Object.entries(preview.predictions ?? {}).map(([key, prediction]) => ({
    key: key === "lambda" ? "λ" : key,
    unit: prediction.unit,
    value: prediction.value,
    low: prediction.lower,
    high: prediction.upper,
    status: preview.support?.status ?? "supported",
    goalValue: prediction.goal_value,
    goalProbability: prediction.goal_probability,
  }));
}

function Icon({
  name,
}: {
  name:
    | "copy"
    | "trash"
    | "plus"
    | "eye"
    | "play"
    | "chevron"
    | "save"
    | "settings";
}) {
  const common = {
    width: 18,
    height: 18,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  const paths = {
    copy: (
      <>
        <rect x="8" y="8" width="12" height="12" rx="1" />
        <path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" />
      </>
    ),
    trash: (
      <>
        <path d="M4 7h16M10 11v6m4-6v6M9 7l1-3h4l1 3m-9 0 1 13h10l1-13" />
      </>
    ),
    plus: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v8m-4-4h8" />
      </>
    ),
    eye: (
      <>
        <path d="M2.5 12s3.3-5.5 9.5-5.5 9.5 5.5 9.5 5.5-3.3 5.5-9.5 5.5S2.5 12 2.5 12Z" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
    play: <path d="m9 5 10 7-10 7V5Z" />,
    chevron: <path d="m9 18 6-6-6-6" />,
    save: (
      <>
        <path d="M5 4h12l3 3v13H4V5a1 1 0 0 1 1-1Z" />
        <path d="M8 4v6h8V4M8 20v-6h8v6" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.2 2.2-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.1h-3.2v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L6.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H5v-3.2h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1 2.2-2.2.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6V4h3.2v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1 2.2 2.2-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.1V14h-.1a1.7 1.7 0 0 0-1.6 1Z" />
      </>
    ),
  };
  return <svg {...common}>{paths[name]}</svg>;
}

function App() {
  const [tab, setTab] = useState<Tab>("candidates");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [preview, setPreview] = useState<ApiPreview | null>(null);
  const [previewsByCandidate, setPreviewsByCandidate] = useState<
    Record<string, ApiPreview>
  >({});
  const [apiState, setApiState] = useState<"ready" | "loading" | "offline">(
    "loading",
  );
  const [notice, setNotice] = useState("候補を読み込んでいます");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ApiProject[]>([]);
  const [activeProjectId, setActiveProjectId] = useState("default");
  const loadSequence = useRef(0);
  const decisionSequence = useRef(0);
  const [decisionSaving, setDecisionSaving] = useState(false);
  const selected = candidates.find((candidate) => candidate.id === selectedId);
  const activeProject = projects.find(
    (project) => project.id === activeProjectId,
  );

  async function loadProject(projectId: string) {
    const sequence = ++loadSequence.current;
    decisionSequence.current += 1;
    setDecisionSaving(false);
    setApiState("loading");
    const response = await fetch(
      `${API_URL}/api/candidates?project_id=${encodeURIComponent(projectId)}`,
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const imported = ((await response.json()) as ApiCandidate[]).map(
      fromApiCandidate,
    );
    if (sequence !== loadSequence.current) return;
    setActiveProjectId(projectId);
    window.localStorage.setItem("material-workbench-project", projectId);
    setCandidates(imported);
    setSelectedId(imported[0]?.id ?? "");
    setMetrics([]);
    setPreview(null);
    setPreviewsByCandidate({});
    setApiState("ready");
    setNotice(
      imported.length
        ? "プロジェクトを切り替えました"
        : "候補がありません。過去条件または新規入力から追加できます",
    );
    if (!imported.length) return;
    const previewEntries = await Promise.all(
      imported.map(async (candidate) => {
        try {
          const prediction = await fetch(
            `${API_URL}/api/candidates/${candidate.id}/preview`,
            { method: "POST" },
          );
          if (!prediction.ok) return null;
          return [
            candidate.id,
            (await prediction.json()) as ApiPreview,
          ] as const;
        } catch {
          return null;
        }
      }),
    );
    if (sequence !== loadSequence.current) return;
    const loaded = Object.fromEntries(
      previewEntries.filter(
        (entry): entry is readonly [string, ApiPreview] => entry !== null,
      ),
    );
    setPreviewsByCandidate(loaded);
    const first = loaded[imported[0].id];
    if (first) {
      setPreview(first);
      setMetrics(metricsFromPreview(first));
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        const response = await fetch(`${API_URL}/api/projects`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const available = (await response.json()) as ApiProject[];
        const remembered = window.localStorage.getItem(
          "material-workbench-project",
        );
        const projectId = available.some((project) => project.id === remembered)
          ? remembered!
          : (available[0]?.id ?? "default");
        if (cancelled) return;
        setProjects(available);
        await loadProject(projectId);
      } catch (error) {
        if (cancelled) return;
        setApiState("offline");
        setLoadError(
          `APIから候補を読み込めませんでした（${error instanceof Error ? error.message : "不明なエラー"}）。`,
        );
        setNotice("API未接続: 予測結果は表示できません");
      }
    }
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    const candidateId = selected.id;
    setApiState("loading");
    setPreview(null);
    setMetrics([]);
    setPreviewsByCandidate((current) => {
      if (!(candidateId in current)) return current;
      const next = { ...current };
      delete next[candidateId];
      return next;
    });
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(
          `${API_URL}/api/candidates/${selected.id}/preview`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            signal: controller.signal,
          },
        );
        if (!response.ok) throw new Error("preview unavailable");
        const preview = (await response.json()) as ApiPreview;
        if (controller.signal.aborted) return;
        setMetrics(metricsFromPreview(preview));
        setPreview(preview);
        setPreviewsByCandidate((current) => ({
          ...current,
          [candidateId]: preview,
        }));
        setNotice(preview.warnings?.[0] ?? "プレビューを更新しました");
        setApiState("ready");
      } catch {
        if (controller.signal.aborted) return;
        setApiState("offline");
        setMetrics([]);
        setPreview(null);
        setNotice("API未接続: 予測結果は表示できません");
      }
    }, 420);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [selected]);

  const updateCandidate = (id: string, field: keyof Candidate, raw: number) => {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    let next = { ...current, [field]: raw } as Candidate;
    if (field === "annealTemperature") {
      const peak = Math.max(...current.heat.map((point) => point.temperature));
      next = {
        ...next,
        heat: current.heat.map((point) =>
          point.temperature === peak ? { ...point, temperature: raw } : point,
        ),
      };
    }
    if (field === "holdMinutes") {
      const peak = Math.max(...current.heat.map((point) => point.temperature));
      const plateau = current.heat
        .map((point, index) => (point.temperature >= peak * 0.95 ? index : -1))
        .filter((index) => index >= 0);
      const start = plateau[0];
      const end = plateau.at(-1);
      if (start !== undefined && end !== undefined && end > start) {
        const delta = raw - (current.heat[end].time - current.heat[start].time);
        next = {
          ...next,
          heat: current.heat.map((point, index) =>
            index >= end
              ? {
                  ...point,
                  time: Math.max(current.heat[start].time, point.time + delta),
                }
              : point,
          ),
        };
      }
    }
    setCandidates((items) =>
      items.map((candidate) => (candidate.id === id ? next : candidate)),
    );
    void persistCandidate(next, current);
  };

  const updateComposition = (id: string, element: string, raw: number) => {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const aliases: Partial<Record<string, keyof Candidate>> = {
      C: "c",
      Si: "si",
      Mn: "mn",
    };
    const next = {
      ...current,
      ...(aliases[element] ? { [aliases[element]!]: raw } : {}),
      raw: {
        ...current.raw,
        composition: { ...current.raw.composition, [element]: raw },
      },
    } as Candidate;
    setCandidates((items) =>
      items.map((candidate) => (candidate.id === id ? next : candidate)),
    );
    void persistCandidate(next, current);
  };

  const updateCandidateText = (
    id: string,
    field: "label" | "coating",
    value: string,
  ) => {
    const current = candidates.find((candidate) => candidate.id === id);
    if (!current) return;
    const next = { ...current, [field]: value };
    setCandidates((items) =>
      items.map((candidate) => (candidate.id === id ? next : candidate)),
    );
    void persistCandidate(next, current);
  };

  const updateHeat = (
    index: number,
    field: "time" | "temperature",
    raw: number,
  ) => {
    if (!selected) return;
    const next = {
      ...selected,
      heat: selected.heat.map((point, pointIndex) =>
        pointIndex === index ? { ...point, [field]: raw } : point,
      ),
    };
    setCandidates((items) =>
      items.map((candidate) =>
        candidate.id === selectedId ? next : candidate,
      ),
    );
    void persistCandidate(next, selected);
  };

  const addHeatPoint = () => {
    if (!selected || selected.heat.length >= 30) return;
    const heat = [...selected.heat];
    const insertAt = Math.max(1, heat.length - 1);
    const before = heat[insertAt - 1];
    const after = heat[insertAt];
    heat.splice(insertAt, 0, {
      time: (before.time + after.time) / 2,
      temperature: (before.temperature + after.temperature) / 2,
    });
    const next = { ...selected, heat };
    setCandidates((items) =>
      items.map((candidate) =>
        candidate.id === selected.id ? next : candidate,
      ),
    );
    void persistCandidate(next, selected);
  };

  const deleteHeatPoint = (index: number) => {
    if (!selected || selected.heat.length <= 2) return;
    const next = {
      ...selected,
      heat: selected.heat.filter((_, pointIndex) => pointIndex !== index),
    };
    setCandidates((items) =>
      items.map((candidate) =>
        candidate.id === selected.id ? next : candidate,
      ),
    );
    void persistCandidate(next, selected);
  };

  async function persistCandidate(candidate: Candidate, previous: Candidate) {
    try {
      const response = await fetch(
        `${API_URL}/api/candidates/${candidate.id}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(toApiCandidate(candidate)),
        },
      );
      if (!response.ok) throw new Error();
    } catch {
      setCandidates((items) =>
        items.map((item) => (item.id === previous.id ? previous : item)),
      );
      setSelectedId(previous.id);
      setApiState("ready");
      setNotice("入力が妥当でないため、直前の値へ戻しました");
    }
  }

  const saveDecision = async (candidateId: string, decisionNote: string) => {
    if (!activeProject) return;
    const sequence = ++decisionSequence.current;
    setDecisionSaving(true);
    try {
      let snapshotId = "";
      if (candidateId) {
        const predictionResponse = await fetch(
          `${API_URL}/api/candidates/${candidateId}/predict`,
          { method: "POST" },
        );
        if (!predictionResponse.ok) {
          throw await apiError(predictionResponse, "判断時点の予測を保存できませんでした。");
        }
        const predictionPayload = (await predictionResponse.json()) as {
          snapshot: { id: string };
        };
        snapshotId = predictionPayload.snapshot.id;
      }
      if (sequence !== decisionSequence.current) return;
      const response = await fetch(`${API_URL}/api/projects/${activeProject.id}/decision`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          candidate_id: candidateId,
          snapshot_id: snapshotId,
          note: decisionNote,
        }),
      });
      if (!response.ok) throw await apiError(response, "判断を保存できませんでした。");
      const saved = (await response.json()) as ApiProject;
      if (sequence !== decisionSequence.current) return;
      setProjects((items) =>
        items.map((project) => (project.id === saved.id ? saved : project)),
      );
      setNotice(
        candidateId
          ? "判断時点の予測を固定し、次実験の候補と理由を保存しました"
          : "次実験の判断を解除しました",
      );
    } catch (error) {
      if (sequence !== decisionSequence.current) return;
      setNotice(error instanceof Error ? error.message : "判断を保存できませんでした。");
    } finally {
      if (sequence === decisionSequence.current) setDecisionSaving(false);
    }
  };

  const addCandidate = async () => {
    if (!selected) return;
    if (candidates.length >= 10) {
      setNotice(
        "比較候補は最大10件です。不要な候補を削除してから追加してください",
      );
      return;
    }
    try {
      const request = toApiCandidate({
        ...selected,
        label: `候補 ${candidates.length + 1}`,
        heat: selected.heat.map((point) => ({ ...point })),
      });
      delete request.id;
      const response = await fetch(
        `${API_URL}/api/candidates?project_id=${encodeURIComponent(activeProjectId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
        },
      );
      if (!response.ok) throw new Error();
      const created = fromApiCandidate((await response.json()) as ApiCandidate);
      setCandidates((items) => [...items, created]);
      setSelectedId(created.id);
      setNotice("候補を追加しました");
    } catch {
      setApiState("offline");
      setNotice("候補を追加できませんでした。API接続を確認してください。");
    }
  };

  const createStarterCandidate = async () => {
    try {
      const response = await fetch(
        `${API_URL}/api/candidates?project_id=${encodeURIComponent(activeProjectId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(STARTER_CANDIDATE),
        },
      );
      if (!response.ok) throw new Error();
      const created = fromApiCandidate((await response.json()) as ApiCandidate);
      setCandidates([created]);
      setSelectedId(created.id);
      setNotice("基準候補を作成しました");
      setApiState("ready");
    } catch {
      setNotice("基準候補を作成できませんでした。API接続を確認してください。");
    }
  };

  const copyCandidate = async () => {
    if (!selected) return;
    await addCandidate();
  };

  const deleteCandidate = async () => {
    if (!selected || candidates.length === 1) return;
    try {
      const response = await fetch(`${API_URL}/api/candidates/${selectedId}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error();
      const remaining = candidates.filter(
        (candidate) => candidate.id !== selectedId,
      );
      setCandidates(remaining);
      setSelectedId(remaining[0].id);
      if (activeProject?.decision_candidate_id === selectedId) {
        setProjects((items) =>
          items.map((project) =>
            project.id === activeProject.id
              ? { ...project, decision_candidate_id: "", decision_note: "" }
              : project,
          ),
        );
      }
      setNotice("候補を削除しました");
    } catch {
      setNotice("候補を削除できませんでした。API接続を確認してください。");
    }
  };

  const runDetailedPrediction = async () => {
    if (!selected) return;
    setApiState("loading");
    try {
      const response = await fetch(
        `${API_URL}/api/candidates/${selected.id}/predict`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error();
      const payload = (await response.json()) as {
        prediction: ApiPreview;
        snapshot: { id: string };
      };
      const result = payload.prediction;
      setMetrics(metricsFromPreview(result));
      setPreview(result);
      setPreviewsByCandidate((current) => ({
        ...current,
        [selected.id]: result,
      }));
      setNotice("詳細予測を実行し、スナップショットを保存しました。");
      setApiState("ready");
    } catch {
      setApiState("offline");
      setNotice(
        "詳細予測または保存に失敗しました。API接続を確認してください。",
      );
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">Material Decision Workbench</div>
        <nav aria-label="画面">
          {navItems.map((item) => (
            <button
              className={tab === item.id ? "nav-button active" : "nav-button"}
              onClick={() => setTab(item.id)}
              key={item.id}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        <div className="context-bar">
          <div>
            <span className="overline">プロジェクト</span>
            <h1>{activeProject?.name ?? "プロジェクトを読み込んでいます"}</h1>
          </div>
          <div className="run-actions">
            <span className={`api-state ${apiState}`}>
              {apiState === "loading"
                ? "プレビュー更新中"
                : apiState === "offline"
                  ? "API 未接続"
                  : "同期済み"}
            </span>
            {tab === "candidates" && selected && (
              <button
                className="primary-button"
                onClick={() => {
                  void runDetailedPrediction();
                }}
              >
                <Icon name="play" />
                {selected.label}の詳細予測を保存
              </button>
            )}
          </div>
        </div>
        {tab === "project" && (
          <LiveProjectPage
            projects={projects}
            activeProjectId={activeProjectId}
            candidate={selected}
            onProjectChanged={(project) => {
              setProjects((items) =>
                items.some((item) => item.id === project.id)
                  ? items.map((item) =>
                      item.id === project.id ? project : item,
                    )
                  : [...items, project],
              );
              if (project.id === activeProjectId) void loadProject(project.id);
            }}
            onSwitch={(projectId) => {
              void loadProject(projectId);
            }}
            onRestore={(candidate) => {
              if (candidate.raw.project_id !== activeProjectId) {
                setNotice(
                  "別プロジェクトの保存結果は現在の候補へ混在できません",
                );
                return;
              }
              setCandidates((items) => [...items, candidate]);
              setSelectedId(candidate.id);
              setTab("candidates");
            }}
          />
        )}
        {tab === "candidates" &&
          (selected ? (
            <CandidateWorkbench
              candidates={candidates}
              projectId={activeProjectId}
              targetValues={activeProject?.target_values ?? {}}
              decisionCandidateId={activeProject?.decision_candidate_id ?? ""}
              decisionNote={activeProject?.decision_note ?? ""}
              decisionSaving={decisionSaving}
              selected={selected}
              selectedId={selectedId}
              metrics={metrics}
              preview={preview}
              previewsByCandidate={previewsByCandidate}
              notice={notice}
              onSelect={setSelectedId}
              onUpdate={updateCandidate}
              onComposition={updateComposition}
              onText={updateCandidateText}
              onHeat={updateHeat}
              onAddHeat={addHeatPoint}
              onDeleteHeat={deleteHeatPoint}
              onCopy={copyCandidate}
              onDelete={() => {
                void deleteCandidate();
              }}
              onAdd={() => {
                void addCandidate();
              }}
              onImported={(imported) => {
                if (imported.length) void loadProject(activeProjectId);
              }}
              onOpenProject={() => setTab("project")}
              onSaveDecision={(candidateId, decisionNote) => {
                return saveDecision(candidateId, decisionNote);
              }}
            />
          ) : (
            <ApiEmptyState
              loading={apiState === "loading"}
              error={loadError}
              onCreate={() => void createStarterCandidate()}
            />
          ))}
        {tab === "quality" && <LiveDataQualityPage />}
        {tab === "lineage" && (
          <LiveLineagePage
            projectId={activeProjectId}
            onCandidate={(candidate) => {
              setCandidates((items) => [...items, candidate]);
              setSelectedId(candidate.id);
              setTab("candidates");
            }}
          />
        )}
        {tab === "explore" && (
          <LiveScreeningPage
            projectId={activeProjectId}
            onCandidate={(candidate) => {
              setCandidates((items) => [...items, candidate]);
              setSelectedId(candidate.id);
              setTab("candidates");
            }}
          />
        )}
      </main>
    </div>
  );
}

function ApiEmptyState({
  loading,
  error,
  onCreate,
}: {
  loading: boolean;
  error: string | null;
  onCreate: () => void;
}) {
  return (
    <div className="api-empty-state" role={error ? "alert" : "status"}>
      <h2>{loading ? "候補を読み込んでいます" : "候補を表示できません"}</h2>
      <p>{error ?? "データと予測モデルを準備しています。"}</p>
      {error && (
        <p className="api-hint">
          FastAPI を <code>{API_URL}</code> で起動後、再読み込みしてください。
        </p>
      )}
      {!loading && !error && (
        <button className="primary-button" onClick={onCreate}>
          <Icon name="plus" />
          最初の候補を作る
        </button>
      )}
    </div>
  );
}

type WorkbenchProps = {
  candidates: Candidate[];
  projectId: string;
  targetValues: Record<string, number>;
  decisionCandidateId: string;
  decisionNote: string;
  decisionSaving: boolean;
  selected: Candidate;
  selectedId: string;
  metrics: Metric[];
  preview: ApiPreview | null;
  previewsByCandidate: Record<string, ApiPreview>;
  notice: string;
  onSelect: (id: string) => void;
  onUpdate: (id: string, field: keyof Candidate, raw: number) => void;
  onHeat: (index: number, field: "time" | "temperature", raw: number) => void;
  onComposition: (id: string, element: string, raw: number) => void;
  onText: (id: string, field: "label" | "coating", value: string) => void;
  onAddHeat: () => void;
  onDeleteHeat: (index: number) => void;
  onCopy: () => void;
  onDelete: () => void;
  onAdd: () => void;
  onImported: (items: Candidate[]) => void;
  onOpenProject: () => void;
  onSaveDecision: (candidateId: string, decisionNote: string) => Promise<void>;
};

function CandidateWorkbench(props: WorkbenchProps) {
  const {
    candidates,
    projectId,
    targetValues,
    decisionCandidateId,
    decisionNote,
    decisionSaving,
    selected,
    selectedId,
    metrics,
    preview,
    previewsByCandidate,
    notice,
    onSelect,
    onUpdate,
    onComposition,
    onText,
    onHeat,
    onAddHeat,
    onDeleteHeat,
    onCopy,
    onDelete,
    onAdd,
    onImported,
    onOpenProject,
    onSaveDecision,
  } = props;
  return (
    <div className="workbench-grid">
      <section className="central-workspace">
        <DecisionSummary
          candidates={candidates}
          previewsByCandidate={previewsByCandidate}
          targetValues={targetValues}
          selectedId={selectedId}
          decisionCandidateId={decisionCandidateId}
          decisionNote={decisionNote}
          decisionSaving={decisionSaving}
          onSelect={onSelect}
          onOpenProject={onOpenProject}
          onSaveDecision={onSaveDecision}
        />
        <div className="table-heading">
          <div>
            <h2>
              候補比較表 <span>（セルを直接編集）</span>
            </h2>
            <span className="notice" role="status">{notice}</span>
          </div>
          <div className="comparison-actions" aria-label="候補操作">
            <button className="outline-button" onClick={onCopy}>
              <Icon name="copy" />選択候補を複製
            </button>
            <button
              className="outline-button"
              onClick={onDelete}
              disabled={
                candidates.length <= 1 ||
                decisionSaving ||
                decisionCandidateId === selectedId
              }
              title={
                decisionCandidateId === selectedId
                  ? "採用判断を解除してから削除してください"
                  : undefined
              }
            >
              <Icon name="trash" />削除
            </button>
            <CandidateFileControls projectId={projectId} onImported={onImported} />
            <button className="primary-button" onClick={onAdd}>
              <Icon name="plus" />候補を追加
            </button>
          </div>
        </div>
        <ComparisonTable
          candidates={candidates}
          selectedId={selectedId}
          previewsByCandidate={previewsByCandidate}
          targetValues={targetValues}
          onSelect={onSelect}
          onUpdate={onUpdate}
          onComposition={onComposition}
          onText={onText}
        />
        <div className="chart-grid">
          <HeatPattern
            candidates={candidates}
            candidate={selected}
            onUpdate={onHeat}
            onAdd={onAddHeat}
            onDelete={onDeleteHeat}
          />
          <LiveResponseCurve
            candidate={selected}
            preview={preview}
            targetValues={targetValues}
          />
        </div>
        <ActualsPanel candidate={selected} />
      </section>
      <EvidencePanel metrics={metrics} preview={preview} candidateLabel={selected.label} />
    </div>
  );
}

type CandidateDecision = {
  candidate: Candidate;
  support: string;
  weakest?: { target: string; probability: number };
  probabilities: Record<string, number>;
};

function DecisionSummary({
  candidates,
  previewsByCandidate,
  targetValues,
  selectedId,
  decisionCandidateId,
  decisionNote,
  decisionSaving,
  onSelect,
  onOpenProject,
  onSaveDecision,
}: {
  candidates: Candidate[];
  previewsByCandidate: Record<string, ApiPreview>;
  targetValues: Record<string, number>;
  selectedId: string;
  decisionCandidateId: string;
  decisionNote: string;
  decisionSaving: boolean;
  onSelect: (id: string) => void;
  onOpenProject: () => void;
  onSaveDecision: (candidateId: string, decisionNote: string) => Promise<void>;
}) {
  const [note, setNote] = useState(
    decisionCandidateId === selectedId ? decisionNote : "",
  );
  useEffect(
    () => setNote(decisionCandidateId === selectedId ? decisionNote : ""),
    [decisionCandidateId, decisionNote, selectedId],
  );
  const targetKeys = Object.keys(targetValues).filter((key) =>
    Number.isFinite(targetValues[key]),
  );
  const decisions: CandidateDecision[] = candidates.map((candidate) => {
    const preview = previewsByCandidate[candidate.id];
    const probabilities = targetKeys
      .map((target) => ({
        target,
        probability: preview?.predictions?.[target]?.goal_probability,
      }))
      .filter(
        (item): item is { target: string; probability: number } =>
          typeof item.probability === "number",
      );
    const complete = probabilities.length === targetKeys.length;
    const weakest = complete
      ? [...probabilities].sort((a, b) => a.probability - b.probability)[0]
      : undefined;
    return {
      candidate,
      support: preview?.support?.status ?? "pending",
      weakest,
      probabilities: Object.fromEntries(
        probabilities.map((item) => [item.target, item.probability]),
      ),
    };
  });
  const sortByMaximin = (a: CandidateDecision, b: CandidateDecision) =>
    (b.weakest?.probability ?? -1) - (a.weakest?.probability ?? -1);
  const complete = decisions.filter(
    (item) => item.weakest && item.support !== "pending",
  );
  const incompleteCount = candidates.length - complete.length;
  const nonDominated = (items: CandidateDecision[]) =>
    items.filter(
      (candidate) =>
        !items.some(
          (other) =>
            other.candidate.id !== candidate.candidate.id &&
            targetKeys.every(
              (target) =>
                other.probabilities[target] >= candidate.probabilities[target],
            ) &&
            targetKeys.some(
              (target) =>
                other.probabilities[target] > candidate.probabilities[target],
            ),
        ),
    );
  const regularAll = complete.filter(
    (item) => item.support !== "extrapolated",
  );
  const extrapolatedAll = complete.filter(
    (item) => item.support === "extrapolated",
  );
  const regularPareto = nonDominated(regularAll).sort(sortByMaximin);
  const extrapolatedPareto = nonDominated(extrapolatedAll).sort(sortByMaximin);
  const paretoIds = new Set(
    [...regularPareto, ...extrapolatedPareto].map((item) => item.candidate.id),
  );
  const regular = [
    ...regularPareto,
    ...regularAll.filter((item) => !paretoIds.has(item.candidate.id)).sort(sortByMaximin),
  ];
  const extrapolated = [
    ...extrapolatedPareto,
    ...extrapolatedAll.filter((item) => !paretoIds.has(item.candidate.id)).sort(sortByMaximin),
  ];
  const ranked = [...regular, ...extrapolated];
  const leader = ranked[0];
  const allExtrapolated = ranked.length > 0 && regular.length === 0;
  const paretoCandidates = regularPareto.length
    ? regularPareto
    : extrapolatedPareto;
  const supportLabel = (value: string) =>
    value === "supported"
      ? "範囲内"
      : value === "caution"
        ? "要確認"
        : value === "extrapolated"
          ? "外挿"
          : "計算中";

  if (!targetKeys.length) {
    return (
      <section className="decision-summary decision-empty">
        <div>
          <span className="overline">DECISION</span>
          <h2>比較の基準になる目標値を設定してください</h2>
          <p>目標を設定すると、各候補の弱点と現在の第一候補をここに表示します。</p>
        </div>
        <button className="outline-button" onClick={onOpenProject}>
          目標値を設定
        </button>
      </section>
    );
  }

  return (
    <section className="decision-summary" aria-label="候補の判断サマリー">
      <div className="decision-lead">
        <span className="overline">CURRENT DECISION</span>
        {leader ? (
          <>
            <h2>
              {leader.candidate.label}
              <small>
                {allExtrapolated
                  ? "参考首位"
                  : incompleteCount
                    ? "暫定の保守基準先頭"
                    : leader.support === "caution"
                      ? "要確認の保守基準先頭"
                      : "保守基準の先頭"}
              </small>
            </h2>
            <div className="decision-facts">
              <span className={`support-pill ${leader.support}`}>
                {supportLabel(leader.support)}
              </span>
              <span>
                最低の個別達成確率 <b>{number((leader.weakest?.probability ?? 0) * 100)}%</b>
              </span>
              <span>
                ボトルネック <b>{leader.weakest?.target === "lambda" ? "λ" : leader.weakest?.target}</b>
              </span>
              {paretoCandidates.length > 1 && (
                <span>非劣候補 <b>{paretoCandidates.length}件</b></span>
              )}
            </div>
          </>
        ) : (
          <h2>
            {Object.keys(previewsByCandidate).length
              ? "全目標の達成確率を比較できません"
              : "候補の予測を計算しています"}
          </h2>
        )}
        <p>
          {allExtrapolated
            ? "全候補が学習範囲外です。順位は探索の手掛かりとしてのみ利用してください。"
            : "範囲内と要確認の候補では非劣候補を優先し、その中で最も低い個別達成確率が高い順に比較しています。外挿は後置します。"}
          <small> 複数目標の同時達成確率ではありません。</small>
          {incompleteCount > 0 && (
            <small> {incompleteCount}件は達成確率不足または計算中のため順位対象外です（評価済み {complete.length}/{candidates.length}件）。</small>
          )}
        </p>
      </div>
      {ranked.length > 0 && (
        <ol className="decision-ranking">
          {ranked.map((item, index) => (
            <li key={item.candidate.id}>
              <button
                className={item.candidate.id === selectedId ? "active" : ""}
                onClick={() => onSelect(item.candidate.id)}
              >
                <span className="rank">{index + 1}</span>
                <span className="rank-name">{item.candidate.label}</span>
                <b>{number((item.weakest?.probability ?? 0) * 100)}%</b>
                <small>
                  {supportLabel(item.support)}
                  {paretoIds.has(item.candidate.id)
                    ? item.support === "extrapolated"
                      ? "・外挿群内非劣"
                      : "・非劣"
                    : ""}
                </small>
              </button>
            </li>
          ))}
        </ol>
      )}
      <div className="decision-commit">
        <div>
          <span className="overline">NEXT EXPERIMENT</span>
          <b>
            {decisionCandidateId
              ? `採用済み: ${candidates.find((item) => item.id === decisionCandidateId)?.label ?? "候補"} / 選択中: ${candidates.find((item) => item.id === selectedId)?.label ?? "候補"}`
              : `選択中: ${candidates.find((item) => item.id === selectedId)?.label ?? "候補"}`}
          </b>
          {decisionCandidateId && (
            <small className="decision-saved-note" title={decisionNote}>
              理由: {decisionNote}（判断時点の予測を固定済み）
            </small>
          )}
        </div>
        <input
          value={note}
          maxLength={500}
          aria-label="次実験に選ぶ理由"
          placeholder="選ぶ理由を一行で残す（必須）"
          onChange={(event) => setNote(event.target.value)}
        />
        <button
          className="primary-button"
          disabled={decisionSaving || !note.trim()}
          onClick={() => {
            void onSaveDecision(selectedId, note.trim());
          }}
        >
          {decisionSaving
            ? "判断を保存中…"
            : decisionCandidateId === selectedId
              ? "判断を更新"
              : "選択候補を次実験に決定"}
        </button>
        {decisionCandidateId && (
          <button
            className="text-button decision-view"
            disabled={decisionSaving}
            onClick={() => {
              onSelect(decisionCandidateId);
              onOpenProject();
            }}
          >
            判断時点を見る
          </button>
        )}
        {decisionCandidateId && (
          <button
            className="text-button decision-clear"
            disabled={decisionSaving}
            onClick={() => {
              void onSaveDecision("", "");
            }}
          >
            決定を解除
          </button>
        )}
      </div>
    </section>
  );
}

function ComparisonTable({
  candidates,
  selectedId,
  previewsByCandidate,
  targetValues,
  onSelect,
  onUpdate,
  onComposition,
  onText,
}: {
  candidates: Candidate[];
  selectedId: string;
  previewsByCandidate: Record<string, ApiPreview>;
  targetValues: Record<string, number>;
  onSelect: (id: string) => void;
  onUpdate: (id: string, field: keyof Candidate, raw: number) => void;
  onComposition: (id: string, element: string, raw: number) => void;
  onText: (id: string, field: "label" | "coating", value: string) => void;
}) {
  const processInputs: Array<{
    label: string;
    unit: string;
    field: keyof Candidate;
    min: number;
    max?: number;
  }> = [
    { label: "板厚", unit: "mm", field: "thickness", min: 0.001, max: 100 },
    { label: "ライン速度", unit: "m/min", field: "lineSpeed", min: 0.001, max: 2000 },
    { label: "焼鈍温度", unit: "°C", field: "annealTemperature", min: -273.15, max: 1800 },
    { label: "保持時間", unit: "min", field: "holdMinutes", min: 0 },
  ];
  const targets = ["TS", "YS", "EL", "lambda"];
  const selectedPreview = previewsByCandidate[selectedId];
  const status = (value?: string) =>
    value === "supported"
      ? "範囲内"
      : value === "caution"
        ? "要確認"
        : value === "extrapolated"
          ? "外挿"
          : "未計算";
  return (
    <div className="table-scroll candidate-row-table">
      <table className="comparison-table">
        <thead>
          <tr>
            <th className="sticky-candidate" rowSpan={2}>
              候補
            </th>
            <th className="prediction-group" colSpan={4}>判断 / 予測値</th>
            <th className="support-cell" rowSpan={2}>支持度</th>
            <th colSpan={COMPOSITION_ELEMENTS.length}>組成</th>
            <th colSpan={processInputs.length + 1}>工程条件</th>
          </tr>
          <tr>
            {targets.map((target, index) => {
              const direction = Object.values(previewsByCandidate).find(
                (item) => item.predictions?.[target]?.goal_direction,
              )?.predictions?.[target]?.goal_direction;
              return (
                <th className={`prediction-cell prediction-col-${index}`} key={target}>
                  {target === "lambda" ? "λ" : target}
                  {Number.isFinite(targetValues[target]) && (
                    <small>
                      目標 {direction === "at_most" ? "≤" : "≥"}{" "}
                      {number(
                        targetValues[target],
                        target === "EL" || target === "lambda" ? 1 : 0,
                      )}
                    </small>
                  )}
                </th>
              );
            })}
            {COMPOSITION_ELEMENTS.map((element) => (
              <th key={element} title={`${element}: 0〜100 mass%`}>
                {element}
                <small>mass%</small>
              </th>
            ))}
            {processInputs.map((input) => (
              <th
                key={input.field}
                title={`許容範囲: ${input.min}〜${input.max ?? "上限なし"} ${input.unit}`}
              >
                {input.label}
                <small>{input.unit}</small>
              </th>
            ))}
            <th>めっき</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((candidate) => {
            const prediction = previewsByCandidate[candidate.id];
            return (
              <tr
                key={candidate.id}
                className={candidate.id === selectedId ? "selected-row" : ""}
                onClick={() => onSelect(candidate.id)}
              >
                <th className="sticky-candidate">
                  <input
                    aria-label={`${candidate.label}の候補名`}
                    maxLength={80}
                    value={candidate.label}
                    onFocus={() => onSelect(candidate.id)}
                    onChange={(event) =>
                      onText(candidate.id, "label", event.target.value)
                    }
                  />
                </th>
                {targets.map((target, index) => {
                  const value = prediction?.predictions?.[target];
                  const selectedValue = selectedPreview?.predictions?.[target]?.value;
                  const delta = value && typeof selectedValue === "number"
                    ? value.value - selectedValue
                    : undefined;
                  return (
                    <td className={`prediction-cell prediction-col-${index}`} key={target}>
                      {value ? (
                        <span className="metric-value">
                          {number(
                            value.value,
                            target === "EL" || target === "lambda" ? 1 : 0,
                          )}
                          <small>{value.unit}</small>
                          {typeof value.goal_probability === "number" && (
                            <em>
                              達成 {number(value.goal_probability * 100)}%
                              {delta !== undefined && Math.abs(delta) > 1e-9 && (
                                <>
                                  {" · 選択との差 "}
                                  {delta > 0 ? "+" : ""}
                                  {number(
                                    delta,
                                    target === "EL" || target === "lambda" ? 1 : 0,
                                  )}
                                </>
                              )}
                            </em>
                          )}
                        </span>
                      ) : (
                        <span className="empty-cell">—</span>
                      )}
                    </td>
                  );
                })}
                <td className="support-cell">
                  <span
                    className={`status-dot ${prediction?.support?.status === "supported" ? "success" : prediction?.support ? "caution" : ""}`}
                  />
                  {status(prediction?.support?.status)}
                </td>
                {COMPOSITION_ELEMENTS.map((element) => (
                  <td key={element}>
                    <input
                      type="number"
                      step="any"
                      min={0}
                      max={100}
                      value={candidate.raw.composition[element] ?? 0}
                      aria-label={`${candidate.label} ${element}`}
                      onFocus={() => onSelect(candidate.id)}
                      onChange={(event) =>
                        onComposition(
                          candidate.id,
                          element,
                          Number(event.target.value),
                        )
                      }
                    />
                  </td>
                ))}
                {processInputs.map((input) => (
                  <td key={input.field}>
                    <input
                      type="number"
                      step="any"
                      min={input.min}
                      max={input.max}
                      value={fieldValue(candidate, input.field)}
                      aria-label={`${candidate.label} ${input.label}`}
                      onFocus={() => onSelect(candidate.id)}
                      onChange={(event) =>
                        onUpdate(
                          candidate.id,
                          input.field,
                          Number(event.target.value),
                        )
                      }
                    />
                  </td>
                ))}
                <td>
                  <select
                    aria-label={`${candidate.label}のめっき`}
                    value={candidate.coating}
                    onChange={(event) =>
                      onText(candidate.id, "coating", event.target.value)
                    }
                  >
                    <option value="なし">なし</option>
                    <option value="GI">GI</option>
                    <option value="GA">GA</option>
                  </select>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CandidateFileControls({
  projectId,
  onImported,
}: {
  projectId: string;
  onImported: (items: Candidate[]) => void;
}) {
  const [message, setMessage] = useState("");
  const upload = async (file?: File) => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const r = await fetch(
        `${API_URL}/api/candidates/import?project_id=${encodeURIComponent(projectId)}`,
        {
          method: "POST",
          body: form,
        },
      );
      if (!r.ok) throw await apiError(r, "XLSXを取り込めませんでした。");
      const body = (await r.json()) as {
        candidates?: ApiCandidate[];
        created?: number;
        errors?: unknown[];
      };
      const imported = (body.candidates ?? []).map(fromApiCandidate);
      onImported(imported);
      setMessage(
        `${body.created ?? imported.length}件を取り込みました${body.errors?.length ? `（${body.errors.length}件は確認が必要）` : ""}`,
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "XLSXを取り込めませんでした。",
      );
    }
  };
  const download = () => {
    window.location.assign(
      `${API_URL}/api/candidates/export.xlsx?project_id=${encodeURIComponent(projectId)}`,
    );
  };
  return (
    <div className="file-controls">
      <label className="outline-button">
        XLSXを読込
        <input
          type="file"
          accept=".xlsx"
          onChange={(e) => {
            void upload(e.target.files?.[0]);
          }}
          hidden
        />
      </label>
      <button className="outline-button" onClick={download}>
        結果をXLSX出力
      </button>
      {message && <small>{message}</small>}
    </div>
  );
}

function ActualsPanel({ candidate }: { candidate: Candidate }) {
  type Actual = {
    id: string;
    property: string;
    mean: number;
    std: number;
    replicates: number;
    unit: string;
    snapshot_id: string;
    experiment_no: string;
    measured_at?: string | null;
    note: string;
  };
  type Comparison = {
    actual: Actual;
    snapshot_id: string;
    prediction: {
      predictions: Record<
        string,
        { value: number; lower: number; upper: number; unit: string }
      >;
      model_meta?: ApiPreview["model_meta"];
    };
  };
  const [property, setProperty] = useState("TS");
  const [mean, setMean] = useState("");
  const [std, setStd] = useState("0");
  const [replicates, setReplicates] = useState("1");
  const [experimentNo, setExperimentNo] = useState("");
  const [measuredAt, setMeasuredAt] = useState("");
  const [note, setNote] = useState("");
  const [comparison, setComparison] = useState<{
    comparisons: Comparison[];
  } | null>(null);
  const [error, setError] = useState("");
  const refresh = async () => {
    try {
      const response = await fetch(
        `${API_URL}/api/candidates/${candidate.id}/prediction-vs-actual`,
      );
      if (!response.ok) throw new Error();
      setComparison(await response.json());
    } catch {
      setError("実測値を取得できませんでした。");
    }
  };
  useEffect(() => {
    void refresh();
  }, [candidate.id]);
  const add = async () => {
    if (mean.trim() === "") return setError("実測平均を入力してください。");
    try {
      setError("");
      const r = await fetch(
        `${API_URL}/api/candidates/${candidate.id}/actuals`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            property,
            mean: Number(mean),
            std: Number(std),
            replicates: Number(replicates),
            unit: property === "TS" || property === "YS" ? "MPa" : "%",
            experiment_no: experimentNo.trim(),
            measured_at: measuredAt || null,
            note: note.trim(),
          }),
        },
      );
      if (!r.ok) throw new Error();
      setMean("");
      setExperimentNo("");
      setMeasuredAt("");
      setNote("");
      await refresh();
    } catch {
      setError("実測値を保存できませんでした。");
    }
  };
  const remove = async (id: string) => {
    const response = await fetch(`${API_URL}/api/actuals/${id}`, {
      method: "DELETE",
    });
    if (response.ok) await refresh();
    else setError("実測値を削除できませんでした。");
  };
  const rows = comparison?.comparisons ?? [];
  return (
    <section className="actuals-panel">
      <div className="panel-title">
        <h2>予測と実測</h2>
        <span>
          {rows.length
            ? "登録時点の予測スナップショットと比較"
            : "実測を登録すると予測を固定保存します"}
        </span>
      </div>
      <div className="actual-form">
        <select
          aria-label="実測特性"
          value={property}
          onChange={(e) => setProperty(e.target.value)}
        >
          <option>TS</option>
          <option>YS</option>
          <option>EL</option>
          <option value="lambda">λ</option>
        </select>
        <input
          aria-label="実測平均"
          type="number"
          placeholder="実測平均"
          value={mean}
          onChange={(e) => setMean(e.target.value)}
        />
        <input
          aria-label="標準偏差"
          type="number"
          min="0"
          placeholder="標準偏差"
          value={std}
          onChange={(e) => setStd(e.target.value)}
        />
        <input
          aria-label="反復数"
          type="number"
          min="1"
          placeholder="反復数"
          value={replicates}
          onChange={(e) => setReplicates(e.target.value)}
        />
        <button
          className="outline-button"
          onClick={() => {
            void add();
          }}
        >
          実測を追加
        </button>
      </div>
      <details className="actual-meta-fields">
        <summary>実験情報を追加</summary>
        <div>
          <label>
            実験番号
            <input
              value={experimentNo}
              onChange={(e) => setExperimentNo(e.target.value)}
              placeholder="例: EXP-2026-014"
            />
          </label>
          <label>
            測定日
            <input
              type="date"
              value={measuredAt}
              onChange={(e) => setMeasuredAt(e.target.value)}
            />
          </label>
          <label>
            メモ
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="試験片・測定条件など"
            />
          </label>
        </div>
      </details>
      {error && <p className="empty-evidence">{error}</p>}
      <table className="quality-table actual-table">
        <thead>
          <tr>
            <th>特性 / 実験</th>
            <th>固定予測</th>
            <th>実測平均 ± SD</th>
            <th>差（実測−予測）</th>
            <th>予測区間</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const prediction = row.prediction.predictions[row.actual.property];
            const delta = row.actual.mean - prediction.value;
            const inside =
              row.actual.mean >= prediction.lower &&
              row.actual.mean <= prediction.upper;
            return (
              <tr key={row.actual.id}>
                <td>
                  <b>{row.actual.property}</b>
                  {(row.actual.experiment_no ||
                    row.actual.measured_at ||
                    row.actual.note) && (
                    <small className="actual-meta">
                      {[
                        row.actual.experiment_no,
                        row.actual.measured_at,
                        row.actual.note,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </small>
                  )}
                </td>
                <td>
                  {number(prediction.value, 1)} {prediction.unit}
                </td>
                <td>
                  {number(row.actual.mean, 1)} ± {number(row.actual.std, 1)}{" "}
                  {row.actual.unit}
                  <small> n={row.actual.replicates}</small>
                </td>
                <td className={Math.abs(delta) > 0 ? "metric-value" : ""}>
                  {delta >= 0 ? "+" : ""}
                  {number(delta, 1)}
                </td>
                <td>
                  <span
                    className={`status-dot ${inside ? "success" : "caution"}`}
                  />
                  {inside ? "区間内" : "区間外"}
                  <small>
                    {" "}
                    [{number(prediction.lower, 1)}–{number(prediction.upper, 1)}
                    ]
                  </small>
                </td>
                <td>
                  <button
                    className="icon-delete"
                    aria-label={`${row.actual.property}実測を削除`}
                    onClick={() => {
                      void remove(row.actual.id);
                    }}
                  >
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
          {!rows.length && (
            <tr>
              <td colSpan={6} className="empty-evidence">
                実測はまだありません。
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function HeatPattern({
  candidates,
  candidate,
  onUpdate,
  onAdd,
  onDelete,
}: {
  candidates: Candidate[];
  candidate: Candidate;
  onUpdate: (index: number, field: "time" | "temperature", raw: number) => void;
  onAdd: () => void;
  onDelete: (index: number) => void;
}) {
  const width = 440;
  const height = 210;
  const pad = { x: 42, y: 18 };
  const maxTime = Math.max(
    3,
    ...candidates.flatMap((item) => item.heat.map((point) => point.time)),
  );
  const maxTemp = Math.max(
    1000,
    ...candidates.flatMap((item) =>
      item.heat.map((point) => point.temperature),
    ),
  );
  const x = (time: number) => pad.x + (time / maxTime) * (width - pad.x - 18);
  const y = (temp: number) =>
    height - 31 - (temp / maxTemp) * (height - pad.y - 31);
  const points = candidate.heat
    .map((point) => `${x(point.time)},${y(point.temperature)}`)
    .join(" ");
  const dragPoint = (event: PointerEvent<SVGCircleElement>, index: number) => {
    const svg = event.currentTarget.ownerSVGElement;
    if (!svg) return;
    const bounds = svg.getBoundingClientRect();
    const temperature = Math.round(
      Math.max(
        0,
        Math.min(
          maxTemp,
          ((height -
            31 -
            ((event.clientY - bounds.top) / bounds.height) * height) /
            (height - pad.y - 31)) *
            maxTemp,
        ),
      ),
    );
    onUpdate(index, "temperature", temperature);
  };
  return (
    <section className="chart-panel heat-panel">
      <div className="panel-title">
        <h2>
          ヒートパターン <span>（焼鈍温度・時間）</span>
        </h2>
        <div className="legend">
          <span className="blue-line" />
          選択候補 <span className="orange-line" />
          比較候補
        </div>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="heat-chart"
        role="img"
        aria-label="候補を重ねたヒートパターン。選択候補の温度点をドラッグして編集できます。"
      >
        <g className="grid-lines">
          {[0, 200, 400, 600, 800, 1000].map((value) => (
            <g key={value}>
              <line x1={pad.x} x2={width - 18} y1={y(value)} y2={y(value)} />
              <text x="3" y={y(value) + 4}>
                {value}
              </text>
            </g>
          ))}
        </g>
        {candidates
          .filter((item) => item.id !== candidate.id)
          .map((item) => (
            <polyline
              key={item.id}
              points={item.heat
                .map((point) => `${x(point.time)},${y(point.temperature)}`)
                .join(" ")}
              fill="none"
              stroke="#DF7703"
              strokeWidth="1.5"
              opacity=".38"
            />
          ))}
        <polyline
          points={points}
          fill="none"
          stroke="#1F5FC4"
          strokeWidth="3"
        />
        {candidate.heat.map((point, index) => (
          <circle
            tabIndex={0}
            aria-label={`${number(point.time, 2)}分, ${point.temperature}度`}
            key={`${point.time}-${index}`}
            cx={x(point.time)}
            cy={y(point.temperature)}
            r="5"
            fill="#1F5FC4"
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              dragPoint(event, index);
            }}
            onPointerMove={(event) =>
              event.currentTarget.hasPointerCapture(event.pointerId) &&
              dragPoint(event, index)
            }
          />
        ))}
        <text className="axis-title" x="3" y="13">
          温度 (°C)
        </text>
        <text
          className="axis-title"
          x={width - 15}
          y={height - 1}
          textAnchor="end"
        >
          時間 (min)
        </text>
      </svg>
      <div className="heat-edit">
        <div>
          <b>ヒートパターン編集</b>
          <span>点をドラッグ、または数値を編集</span>
          <button className="text-button" onClick={onAdd}>
            点を追加
          </button>
        </div>
        <div className="heat-point-list">
          {candidate.heat.map((point, index) => (
            <div className="heat-point-row" key={`${point.time}-${index}`}>
              <b>{index + 1}</b>
              <label>
                時間 (min)
                <input
                  type="number"
                  step="0.01"
                  value={Number(point.time.toFixed(3))}
                  onChange={(event) =>
                    onUpdate(index, "time", Number(event.target.value))
                  }
                />
              </label>
              <label>
                温度 (°C)
                <input
                  type="number"
                  value={point.temperature}
                  onChange={(event) =>
                    onUpdate(index, "temperature", Number(event.target.value))
                  }
                />
              </label>
              <button
                className="icon-delete"
                aria-label={`点${index + 1}を削除`}
                disabled={candidate.heat.length <= 2}
                onClick={() => onDelete(index)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <small>RT = 室温（25°C）</small>
      </div>
    </section>
  );
}

type CurvePoint = {
  temperature_c: number;
  value: number;
  lower: number;
  upper: number;
};
function LiveResponseCurve({
  candidate,
  preview,
  targetValues,
}: {
  candidate: Candidate;
  preview: ApiPreview | null;
  targetValues: Record<string, number>;
}) {
  const [points, setPoints] = useState<CurvePoint[]>([]);
  const [error, setError] = useState(false);
  const [target, setTarget] = useState("TS");
  useEffect(() => {
    const controller = new AbortController();
    setPoints([]);
    setError(false);
    const timer = window.setTimeout(() => {
      fetch(
        `${API_URL}/api/candidates/${candidate.id}/response-curve?target=${encodeURIComponent(target)}`,
        { signal: controller.signal },
      )
        .then(async (response) => {
          if (!response.ok) throw new Error();
          const body = (await response.json()) as CurvePoint[];
          if (!controller.signal.aborted) setPoints(body);
        })
        .catch(() => {
          if (!controller.signal.aborted) setError(true);
        });
    }, 320);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [candidate, target]);
  const minTemp = Math.min(
    ...points.map((point) => point.temperature_c),
    candidate.annealTemperature,
    500,
  );
  const maxTemp = Math.max(
    ...points.map((point) => point.temperature_c),
    candidate.annealTemperature,
    900,
  );
  const goalValue = targetValues[target];
  const currentPrediction = preview?.predictions?.[target]?.value;
  const goalDirection = preview?.predictions?.[target]?.goal_direction;
  const rawMinValue = points.length
    ? Math.min(
        ...points.map((point) => point.lower),
        typeof currentPrediction === "number" ? currentPrediction : Infinity,
        Number.isFinite(goalValue) ? goalValue : Infinity,
      )
    : 0;
  const rawMaxValue = points.length
    ? Math.max(
        ...points.map((point) => point.upper),
        typeof currentPrediction === "number" ? currentPrediction : -Infinity,
        Number.isFinite(goalValue) ? goalValue : -Infinity,
      )
    : 1;
  const valuePadding = Math.max(1, (rawMaxValue - rawMinValue) * 0.08);
  const minValue = rawMinValue - valuePadding;
  const maxValue = rawMaxValue + valuePadding;
  const x = (value: number) =>
    42 + ((value - minTemp) / Math.max(1, maxTemp - minTemp)) * 376;
  const y = (value: number) =>
    185 - ((value - minValue) / Math.max(1, maxValue - minValue)) * 150;
  const line = points
    .map(
      (point, index) =>
        `${index ? "L" : "M"}${x(point.temperature_c)} ${y(point.value)}`,
    )
    .join(" ");
  const band = points.length
    ? `${points.map((point, index) => `${index ? "L" : "M"}${x(point.temperature_c)} ${y(point.upper)}`).join(" ")} ${[
        ...points,
      ]
        .reverse()
        .map((point) => `L${x(point.temperature_c)} ${y(point.lower)}`)
        .join(" ")} Z`
    : "";
  const xTicks = [minTemp, (minTemp + maxTemp) / 2, maxTemp];
  const yTicks = [minValue, (minValue + maxValue) / 2, maxValue];
  return (
    <section className="chart-panel response-panel">
      <div className="panel-title">
        <h2>
          応答曲線{" "}
          <span>（{target === "lambda" ? "λ" : target} vs 焼鈍温度）</span>
        </h2>
        <select
          aria-label="応答曲線の予測特性"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
        >
          <option value="TS">TS</option>
          <option value="YS">YS</option>
          <option value="EL">EL</option>
          <option value="lambda">λ</option>
        </select>
      </div>
      {error ? (
        <p className="empty-evidence">応答曲線を取得できません。</p>
      ) : points.length ? (
        <svg
          viewBox="0 0 440 210"
          className="response-chart"
          role="img"
          aria-label={`APIから取得した${target}応答曲線`}
        >
          {xTicks.map((tick) => (
            <g key={`x-${tick}`}>
              <line x1={x(tick)} y1="185" x2={x(tick)} y2="190" stroke="#8290a3" />
              <text x={x(tick)} y="203" textAnchor="middle" fontSize="10" fill="#617087">
                {number(tick)}
              </text>
            </g>
          ))}
          {yTicks.map((tick) => (
            <g key={`y-${tick}`}>
              <line x1="37" y1={y(tick)} x2="418" y2={y(tick)} stroke="#e3e9f0" />
              <text x="34" y={y(tick) + 3} textAnchor="end" fontSize="9" fill="#617087">
                {number(tick, target === "EL" || target === "lambda" ? 1 : 0)}
              </text>
            </g>
          ))}
          <path d={band} fill="#1F5FC4" opacity=".12" />
          <path d={line} fill="none" stroke="#1F5FC4" strokeWidth="3" />
          {Number.isFinite(goalValue) && (
            <g>
              <line x1="42" y1={y(goalValue)} x2="418" y2={y(goalValue)} stroke="#c17816" strokeDasharray="5 4" />
              <text x="415" y={y(goalValue) - 5} textAnchor="end" fontSize="10" fill="#9a5f10">
                目標 {goalDirection === "at_most" ? "≤" : "≥"}{" "}
                {number(goalValue, target === "EL" || target === "lambda" ? 1 : 0)}
              </text>
            </g>
          )}
          {typeof currentPrediction === "number" && (
            <g>
              <line x1={x(candidate.annealTemperature)} y1="35" x2={x(candidate.annealTemperature)} y2="185" stroke="#176d52" strokeDasharray="3 3" />
              <circle cx={x(candidate.annealTemperature)} cy={y(currentPrediction)} r="5" fill="#fff" stroke="#176d52" strokeWidth="3" />
              <text x={x(candidate.annealTemperature)} y="27" textAnchor="middle" fontSize="10" fill="#176d52">
                現在 {number(candidate.annealTemperature)}°C
              </text>
            </g>
          )}
          {points.map((point) => (
            <circle
              key={point.temperature_c}
              cx={x(point.temperature_c)}
              cy={y(point.value)}
              r="3"
              fill="#1F5FC4"
            />
          ))}
          <text x="230" y="209" textAnchor="middle" fontSize="10" fill="#617087">焼鈍温度 (°C)</text>
        </svg>
      ) : (
        <p className="empty-evidence">応答曲線を読み込んでいます。</p>
      )}
      <p className="curve-note">
        現在候補の他条件を固定し、焼鈍温度だけを動かしたモデル応答です。
      </p>
    </section>
  );
}

function EvidencePanel({
  metrics,
  preview,
  candidateLabel,
}: {
  metrics: Metric[];
  preview: ApiPreview | null;
  candidateLabel: string;
}) {
  const similar = preview?.similar ?? [];
  const nearest = similar.slice(0, 3);
  const status = preview?.support?.status;
  const training = preview?.model_meta?.training_data?.records;
  const warnings = (preview?.warnings ?? []).filter(
    (warning) => warning !== preview?.support?.message,
  );
  return (
    <aside className="evidence-panel">
      <section>
        <div className="evidence-title">
          <h2>予測特性 <span>— {candidateLabel}</span></h2>
        </div>
        {metrics.length ? (
          <table className="metric-table">
            <thead>
              <tr>
                <th>特性</th>
                <th>予測値</th>
                <th>検証残差区間 (90%)</th>
                <th>目標達成</th>
                <th>学習範囲</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((metric) => (
                <tr key={metric.key}>
                  <th>
                    {metric.key} <small>({metric.unit})</small>
                  </th>
                  <td>
                    {number(
                      metric.value,
                      metric.key === "EL" || metric.key === "λ" ? 1 : 0,
                    )}
                  </td>
                  <td>
                    {number(metric.low, 1)}{" "}
                    <span className="whisker">
                      <i style={{ left: "48%" }} />
                    </span>{" "}
                    {number(metric.high, 1)}
                  </td>
                  <td>
                    {metric.goalProbability === null ||
                    metric.goalProbability === undefined ? (
                      "—"
                    ) : (
                      <>
                        <b>{number(metric.goalProbability * 100, 0)}%</b>
                        <small> ≥ {number(metric.goalValue ?? 0, 1)}</small>
                      </>
                    )}
                  </td>
                  <td>
                    <span
                      className={`status-dot ${metric.status === "supported" ? "success" : "caution"}`}
                    />
                    {metric.status === "extrapolated"
                      ? "外挿"
                      : metric.status === "caution"
                        ? "要確認"
                        : "範囲内"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty-evidence">プレビュー結果を待っています。</p>
        )}
        <p className="interval-note">
          区間と目標達成率は、親工程単位の交差検証残差から求めた経験的な範囲です。
        </p>
      </section>
      {warnings.map((warning) => (
        <div className="warning" key={warning}>
          <span>⚠</span>
          <p>{warning}</p>
        </div>
      ))}
      {preview?.support?.message && (
        <div className={status === "supported" ? "support-note" : "warning"}>
          <span>{status === "supported" ? "✓" : "⚠"}</span>
          <p>{preview.support.message}</p>
        </div>
      )}
      <section>
        <div className="evidence-title">
          <h2>近い過去実験</h2>
          <span>成分・工程・熱履歴を分けて確認</span>
        </div>
        {nearest.length ? (
          <>
            <table className="similar-table detailed-distance">
              <thead>
                <tr>
                  <th>親条件</th>
                  <th>層</th>
                  <th>成分</th>
                  <th>冶金</th>
                  <th>工程</th>
                  <th>熱履歴</th>
                  <th>総合</th>
                  <th>実測</th>
                </tr>
              </thead>
              <tbody>
                {nearest.map((item) => (
                  <tr
                    key={`${item.layer ?? "training"}-${item.observation_id}`}
                  >
                    <td>{item.parent_key}</td>
                    <td>
                      <span
                        className={`layer-chip ${item.layer ?? "training"}`}
                      >
                        {item.layer === "historical" ? "学習外" : "学習内"}
                      </span>
                    </td>
                    <td>{item.components?.composition?.toFixed(2) ?? "—"}</td>
                    <td>{item.components?.metallurgy?.toFixed(2) ?? "—"}</td>
                    <td>{item.components?.process?.toFixed(2) ?? "—"}</td>
                    <td>{item.components?.heat_pattern?.toFixed(2) ?? "—"}</td>
                    <td>{item.distance.toFixed(2)}</td>
                    <td>
                      {Object.entries(item.repeat_summary ?? {})
                        .map(
                          ([key, value]) =>
                            `${key === "lambda" ? "λ" : key} ${number(value.mean, 1)} ± ${number(value.std, 1)} (n=${value.n})`,
                        )
                        .join(" / ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {similar.length > 3 && (
              <details className="similar-more">
                <summary>ほかの近傍を表示</summary>
                {similar.slice(3).map((item) => (
                  <p key={`${item.layer ?? "training"}-${item.observation_id}`}>
                    {item.parent_key} ·{" "}
                    {item.layer === "historical" ? "学習外" : "学習内"} · 距離{" "}
                    {item.distance.toFixed(2)} · {Object.entries(item.repeat_summary ?? {}).map(([key, value]) => `${key} ${number(value.mean, 1)} ± ${number(value.std, 1)} (n=${value.n})`).join(" / ")}
                  </p>
                ))}
              </details>
            )}
          </>
        ) : (
          <p className="empty-evidence">類似実験を取得しています。</p>
        )}
      </section>
      <section className="evidence-card">
        <h2>予測の根拠</h2>
        <dl>
          <dt>計算方法</dt>
          <dd>
            {preview?.model_meta?.model?.method ?? "—"} ·{" "}
            {preview?.model_meta?.model?.id ?? "—"} v
            {preview?.model_meta?.model?.version ?? "—"}
          </dd>
          <dt>Package</dt>
          <dd>
            {preview?.model_meta?.package
              ? `${preview.model_meta.package.id} v${preview.model_meta.package.version} / ${preview.model_meta.package.manifest_sha256?.slice(0, 12)}`
              : "—"}
          </dd>
          <dt>特徴量</dt>
          <dd>
            {preview?.model_meta?.feature_pipeline?.id ?? "—"} v
            {preview?.model_meta?.feature_pipeline?.version ?? "—"}
          </dd>
          <dt>学習観測</dt>
          <dd>
            {training
              ? Object.entries(training)
                  .map(([key, value]) => `${key}: ${value}`)
                  .join(" / ")
              : "—"}
          </dd>
          <dt>検証方法</dt>
          <dd>{preview?.model_meta?.prediction_interval?.method ?? "—"}</dd>
          <dt>学習データ</dt>
          <dd>
            {preview?.model_meta?.training_data?.source_sha256?.slice(0, 12) ??
              "—"}
          </dd>
          <dt>支持度</dt>
          <dd>
            {status ?? "—"}
            {preview?.support?.percentile !== undefined
              ? `（距離百分位 ${preview.support.percentile.toFixed(0)}%）`
              : ""}
          </dd>
        </dl>
      </section>
    </aside>
  );
}

function LiveDataQualityPage() {
  type DetectedIssue = {
    issue_id: string;
    issue_type:
      | "missing_key"
      | "orphan_entity"
      | "duplicate_key"
      | "invalid_reference";
    source_sheet: string;
    entity_key: string;
    detail: string;
  };
  type Scenario = {
    scenario_id: string;
    分類: string;
    対象キー: string;
    対象シート: string;
    期待する気づき: string;
  };
  type Quality = {
    detected_total: number;
    detected_by_type: Record<string, number>;
    detected_issues: DetectedIssue[];
    reference_scenarios: Scenario[];
  };
  const [data, setData] = useState<Quality | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    fetch(`${API_URL}/api/quality`)
      .then(async (response) => {
        if (!response.ok) throw new Error();
        setData((await response.json()) as Quality);
      })
      .catch(() => setError(true));
  }, []);
  const labels: Record<DetectedIssue["issue_type"], string> = {
    missing_key: "キー欠損",
    orphan_entity: "孤立",
    duplicate_key: "重複",
    invalid_reference: "不正参照",
  };
  return (
    <div className="page-panel quality-page">
      <div className="page-intro">
        <div>
          <h2>データ品質</h2>
          <p>
            元Excelを変更せず、関係と各工程シートを照合して実際の問題を検出します。
          </p>
        </div>
        <button
          className="outline-button"
          onClick={() =>
            window.location.assign(`${API_URL}/api/quality/export.csv`)
          }
        >
          検出結果をCSV出力
        </button>
      </div>
      {error ? (
        <p className="empty-evidence">
          データ品質を取得できません。API接続を確認してください。
        </p>
      ) : data ? (
        <>
          <div className="quality-summary">
            <span>
              <b>{data.detected_total}</b>件を実検出
            </span>
            {Object.entries(data.detected_by_type).map(([type, count]) => (
              <span key={type}>
                <b>{count}</b>
                {labels[type as DetectedIssue["issue_type"]] ?? type}
              </span>
            ))}
          </div>
          <div className="table-scroll">
            <table className="quality-table">
              <thead>
                <tr>
                  <th>検出種別</th>
                  <th>対象キー</th>
                  <th>元シート</th>
                  <th>検出内容</th>
                </tr>
              </thead>
              <tbody>
                {data.detected_issues.map((issue) => (
                  <tr key={issue.issue_id}>
                    <td>
                      <span
                        className={`status-tag ${issue.issue_type === "invalid_reference" || issue.issue_type === "duplicate_key" ? "warn" : ""}`}
                      >
                        {labels[issue.issue_type]}
                      </span>
                    </td>
                    <td>{issue.entity_key || "（空）"}</td>
                    <td>{issue.source_sheet}</td>
                    <td>{issue.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details className="reference-scenarios">
            <summary>
              Excelに用意された確認用シナリオ（{data.reference_scenarios.length}
              件）
            </summary>
            <p>
              ここは検出結果ではなく、アプリの気づきを検証するために元データへ用意された参照ケースです。
            </p>
            <table className="quality-table">
              <tbody>
                {data.reference_scenarios.map((scenario) => (
                  <tr key={scenario.scenario_id}>
                    <td>{scenario.分類}</td>
                    <td>{scenario.対象キー}</td>
                    <td>{scenario.期待する気づき}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      ) : (
        <p className="empty-evidence">データ品質を読み込んでいます。</p>
      )}
    </div>
  );
}

function LiveLineagePage({
  projectId,
  onCandidate,
}: {
  projectId: string;
  onCandidate: (candidate: Candidate) => void;
}) {
  type Summary = {
    count: number;
    min: number;
    mean: number;
    std: number;
    median: number;
    max: number;
  };
  type Lineage = {
    key: string;
    relations: Record<string, string[]>;
    quality_issues: Array<{
      issue_id: string;
      issue_type: string;
      source_sheet: string;
      entity_key: string;
      detail: string;
    }>;
    candidate_eligible: boolean;
    candidate_reason: string;
    graph: {
      nodes: Array<{
        key: string;
        entity_type: string;
        source_sheet: string;
        exists: boolean;
        selected: boolean;
        issue_types: string[];
      }>;
      edges: Array<{ source: string; target: string; route_rows: number[] }>;
      relation_row_count: number;
      omitted_node_count: number;
    };
    node: {
      entity_type: string;
      source_sheet: string;
      missing_source: boolean;
      primary_conditions: Record<string, string | number | boolean | null>;
      composition: Record<string, number>;
      heat_pattern: Array<{
        time_s: number;
        temperature_c: number;
        segment_start?: boolean;
      }>;
      connected_observation_count: number;
      connected_observations: Array<{
        id: string;
        source: string;
        parent_key: string;
        outputs: Record<string, number>;
      }>;
      observation_groups: Array<{
        stage: string;
        test_type: string;
        property: string;
        count: number;
        min: number;
        mean: number;
        std: number;
        median: number;
        max: number;
        observations: Array<{
          id: string;
          source: string;
          parent_key: string;
          outputs: Record<string, number>;
        }>;
      }>;
      property_summary: Record<string, Summary>;
    };
  };
  type LineageIndex = {
    items: Array<{ key: string; entity_type: string; has_issue: boolean }>;
    total_entities: number;
    relation_rows: number;
    detected_issues: number;
    counts_by_type: Record<string, number>;
  };
  const [entityKey, setEntityKey] = useState("AN-00001");
  const [query, setQuery] = useState("");
  const [entityType, setEntityType] = useState("焼鈍");
  const [issueOnly, setIssueOnly] = useState(false);
  const [index, setIndex] = useState<LineageIndex | null>(null);
  const [data, setData] = useState<Lineage | null>(null);
  const [error, setError] = useState("");
  const [candidateError, setCandidateError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ limit: "40" });
    if (query.trim()) params.set("query", query.trim());
    if (entityType) params.set("entity_type", entityType);
    if (issueOnly) params.set("issue_only", "true");
    const timer = window.setTimeout(() => {
      fetch(`${API_URL}/api/lineage?${params.toString()}`, {
        signal: controller.signal,
      })
        .then(async (response) => {
          if (response.ok) setIndex((await response.json()) as LineageIndex);
        })
        .catch(() => undefined);
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, entityType, issueOnly]);
  useEffect(() => {
    let cancelled = false;
    setError("");
    setCandidateError("");
    fetch(`${API_URL}/api/lineage/${encodeURIComponent(entityKey)}`)
      .then(async (response) => {
        if (!response.ok)
          throw await apiError(response, "系譜を取得できませんでした。");
        if (!cancelled) setData((await response.json()) as Lineage);
      })
      .catch((cause) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "系譜を取得できませんでした。",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [entityKey]);
  const createCandidate = async () => {
    try {
      const response = await fetch(
        `${API_URL}/api/lineage/${encodeURIComponent(entityKey)}/candidate?project_id=${encodeURIComponent(projectId)}`,
        { method: "POST" },
      );
      if (!response.ok)
        throw await apiError(response, "候補を作成できませんでした。");
      onCandidate(fromApiCandidate((await response.json()) as ApiCandidate));
    } catch (cause) {
      setCandidateError(
        cause instanceof Error ? cause.message : "候補を作成できませんでした。",
      );
    }
  };
  const stageGroups = [
    { label: "材料", types: ["溶製"] },
    { label: "熱延", types: ["熱延", "熱延引張", "熱延組織"] },
    { label: "冷延", types: ["冷延"] },
    { label: "焼鈍", types: ["焼鈍"] },
    { label: "試験・組織", types: ["焼鈍引張", "焼鈍穴広げ", "焼鈍組織"] },
  ];
  const issueLabels: Record<string, string> = {
    missing_key: "キー欠損",
    orphan_entity: "孤立",
    duplicate_key: "重複",
    invalid_reference: "参照切れ",
  };
  const openNode = (key: string) => {
    setEntityKey(key);
  };
  const heat = data?.node.heat_pattern ?? [];
  const maxTime = Math.max(1, ...heat.map((point) => point.time_s));
  const maxTemp = Math.max(1, ...heat.map((point) => point.temperature_c));
  const heatPoints = heat
    .map(
      (point) =>
        `${20 + (point.time_s / maxTime) * 380},${120 - (point.temperature_c / maxTemp) * 100}`,
    )
    .join(" ");
  return (
    <div className="page-panel lineage-page">
      <div className="page-intro lineage-intro">
        <div>
          <span className="overline">DATA UNDERSTANDING</span>
          <h2>工程系譜</h2>
          <p>
            この材料・条件は、どの工程と試験結果につながっているか。
          </p>
        </div>
        <form
          className="lineage-search"
          onSubmit={(event) => {
            event.preventDefault();
            if (query.trim()) {
              setEntityType("");
              openNode(query.trim());
            }
          }}
        >
          <label htmlFor="lineage-query">キーを検索</label>
          <input
            id="lineage-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="例: AN-00001"
          />
          <button type="submit" className="secondary-button">開く</button>
        </form>
      </div>
      <div className="lineage-workspace">
        <aside className="lineage-browser" aria-label="系譜ノード検索">
          {index && (
            <div className="lineage-source-facts">
              <span><b>{number(index.total_entities)}</b> エンティティ</span>
              <span><b>{number(index.relation_rows)}</b> relation行</span>
              <span className={index.detected_issues ? "has-issue" : ""}><b>{index.detected_issues}</b> 検出問題</span>
            </div>
          )}
          <label>
            種別
            <select value={entityType} onChange={(event) => setEntityType(event.target.value)}>
              <option value="">すべて</option>
              {Object.keys(index?.counts_by_type ?? {}).map((type) => (
                <option key={type} value={type}>{type} ({index?.counts_by_type[type]})</option>
              ))}
            </select>
          </label>
          <label className="lineage-issue-filter">
            <input type="checkbox" checked={issueOnly} onChange={(event) => setIssueOnly(event.target.checked)} />
            問題があるノードだけ
          </label>
          <div className="lineage-result-list">
            {(index?.items ?? []).map((item) => (
              <button
                key={`${item.entity_type}-${item.key}`}
                type="button"
                className={item.key === entityKey ? "active" : ""}
                onClick={() => openNode(item.key)}
              >
                <span>{item.key}</span>
                <small>{item.entity_type}{item.has_issue ? " · 要確認" : ""}</small>
              </button>
            ))}
            {index && !index.items.length && <p className="empty-evidence">一致するキーはありません。</p>}
          </div>
        </aside>
        <main className="lineage-main">
      {error ? (
        <div className="lineage-load-error">
          <b>{entityKey}</b>
          <p>{error}</p>
          <span>左の検索結果から存在するキーを選んでください。</span>
        </div>
      ) : data ? (
        <>
          <div
            className="lineage-canvas"
            role="group"
            aria-label={`${data.key} のデータ系譜`}
          >
            <div className="lineage-canvas-header">
              <div>
                <b>{data.graph.relation_row_count} relation行から復元</b>
                <span>行番号を保持した実在経路です。relation行を実験件数として数えていません。</span>
              </div>
              {data.graph.omitted_node_count > 0 && <small>ほか {data.graph.omitted_node_count} ノード省略</small>}
            </div>
            <div className="lineage-stage-grid">
              {stageGroups.map((stage) => {
                const nodes = data.graph.nodes.filter((node) => stage.types.includes(node.entity_type));
                return (
                  <section key={stage.label} className="lineage-stage-column">
                    <h3>{stage.label}<small>{nodes.length}</small></h3>
                    <div>
                      {nodes.map((node) => (
                        <button
                          type="button"
                          key={node.key}
                          className={`lineage-node ${node.selected ? "selected" : ""} ${!node.exists ? "missing" : ""} ${node.issue_types.length ? "issue" : ""}`}
                          onClick={() => openNode(node.key)}
                        >
                          <span>{node.key}</span>
                          <small>{node.entity_type}</small>
                          {node.issue_types.length > 0 && <em>{node.issue_types.map((issue) => issueLabels[issue] ?? issue).join(" / ")}</em>}
                        </button>
                      ))}
                      {!nodes.length && <span className="lineage-empty-stage">接続なし</span>}
                    </div>
                  </section>
                );
              })}
            </div>
            {data.graph.edges.length > 0 && (
              <details className="route-evidence">
                <summary>経路の接続根拠 {data.graph.edges.length}本</summary>
                <div>
                  {data.graph.edges.map((edge) => (
                    <p key={`${edge.source}-${edge.target}`}>
                      <button type="button" onClick={() => openNode(edge.source)}>{edge.source}</button>
                      <span>→</span>
                      <button type="button" onClick={() => openNode(edge.target)}>{edge.target}</button>
                      <small>relation {edge.route_rows.slice(0, 5).join(", ")}{edge.route_rows.length > 5 ? ` +${edge.route_rows.length - 5}` : ""}</small>
                    </p>
                  ))}
                </div>
              </details>
            )}
          </div>
          <div className="lineage-detail-header">
            <div>
              <span className="overline">
                {data.node.source_sheet} / {data.node.entity_type}
              </span>
              <h3>{data.key}</h3>
              <p>
                {Object.values(data.relations).reduce(
                  (sum, values) => sum + values.length,
                  0,
                )}
                件の関係、{data.node.connected_observation_count}件の接続観測
              </p>
            </div>
            <button
              className="primary-button"
              disabled={!data.candidate_eligible}
              title={data.candidate_reason}
              onClick={() => {
                void createCandidate();
              }}
            >
              この実績条件から候補を作成
            </button>
          </div>
          <p className={`lineage-candidate-note ${data.candidate_eligible ? "" : "muted"}`}>{data.candidate_reason}</p>
          {candidateError && <p className="warning">{candidateError}</p>}
          <div className="lineage-detail-grid">
            <section>
              <h3>主要条件</h3>
              <dl>
                {Object.entries(data.node.primary_conditions).map(
                  ([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{value === null ? "—" : String(value)}</dd>
                    </div>
                  ),
                )}
              </dl>
            </section>
            <section>
              <h3>
                上流組成 <small>mass%</small>
              </h3>
              <div className="composition-chips">
                {Object.entries(data.node.composition).map(([key, value]) => (
                  <span key={key}>
                    <b>{key}</b>
                    {number(value, value < 0.01 ? 5 : 3)}
                  </span>
                ))}
              </div>
            </section>
            <section>
              <h3>
                実績ヒートパターン <small>{heat.length}点</small>
              </h3>
              {heat.length ? (
                <svg
                  viewBox="0 0 420 135"
                  className="lineage-heat"
                  role="img"
                  aria-label="実績ヒートパターン"
                >
                  <line x1="20" x2="400" y1="120" y2="120" />
                  <polyline
                    points={heatPoints}
                    fill="none"
                    stroke="#1f5fc4"
                    strokeWidth="3"
                  />
                  {heat.map((point) => (
                    <circle
                      key={point.time_s}
                      cx={20 + (point.time_s / maxTime) * 380}
                      cy={120 - (point.temperature_c / maxTemp) * 100}
                      r="3"
                      fill="#1f5fc4"
                    >
                      <title>{`${point.time_s}s / ${point.temperature_c}°C`}</title>
                    </circle>
                  ))}
                </svg>
              ) : (
                <p className="empty-evidence">
                  このノードに焼鈍履歴は接続されていません。
                </p>
              )}
            </section>
            <section>
              <h3>工程段階別の特性分布</h3>
              {(data.node.observation_groups ?? []).length ? (
                <>
                  <table className="quality-table compact-table">
                    <thead>
                      <tr>
                        <th>段階 / 試験</th>
                        <th>特性</th>
                        <th>n</th>
                        <th>min</th>
                        <th>mean ± SD</th>
                        <th>median</th>
                        <th>max</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(data.node.observation_groups ?? []).map(
                        (group) => (
                          <tr key={`${group.test_type}-${group.property}`}>
                            <td><b>{group.stage}</b><br /><small>{group.test_type}</small></td>
                            <td>{group.property}</td>
                            <td>{group.count}</td>
                            <td>{number(group.min, 1)}</td>
                            <td>
                              {number(group.mean, 1)} ±{" "}
                              {number(group.std, 1)}
                            </td>
                            <td>{number(group.median, 1)}</td>
                            <td>{number(group.max, 1)}</td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                  <details className="similar-more">
                    <summary>観測値を表示</summary>
                    {(data.node.connected_observations ?? []).map((observation) => (
                      <p key={observation.id}>
                        {observation.id} · {observation.source} ·{" "}
                        {Object.entries(observation.outputs)
                          .map(([key, value]) => `${key} ${number(value, 1)}`)
                          .join(" / ")}
                      </p>
                    ))}
                  </details>
                </>
              ) : (
                <p className="empty-evidence">接続観測はありません。</p>
              )}
            </section>
          </div>
          {data.quality_issues.map((issue) => (
            <p
              className="warning"
              key={issue.issue_id}
            >
              <b>{issueLabels[issue.issue_type] ?? issue.issue_type}</b> · {issue.source_sheet} · {issue.entity_key || "キーなし"}: {issue.detail}
            </p>
          ))}
        </>
      ) : (
        <p className="empty-evidence">系譜を読み込んでいます。</p>
      )}
        </main>
      </div>
    </div>
  );
}

function LiveProjectPage({
  projects,
  activeProjectId,
  candidate,
  onProjectChanged,
  onSwitch,
  onRestore,
}: {
  projects: ApiProject[];
  activeProjectId: string;
  candidate?: Candidate;
  onProjectChanged: (project: ApiProject) => void;
  onSwitch: (projectId: string) => void;
  onRestore: (candidate: Candidate) => void;
}) {
  const [project, setProject] = useState<ApiProject | null>(null);
  const [snapshots, setSnapshots] = useState<ApiSnapshot[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState("");
  const [error, setError] = useState("");
  const [modelPackage, setModelPackage] = useState<ApiModelPackage | null>(
    null,
  );
  useEffect(() => {
    setProject(projects.find((item) => item.id === activeProjectId) ?? null);
    setError("");
  }, [projects, activeProjectId]);
  useEffect(() => {
    fetch(`${API_URL}/api/model-package`)
      .then(async (response) =>
        setModelPackage(response.ok ? await response.json() : null),
      )
      .catch(() => setModelPackage(null));
  }, []);
  useEffect(() => {
    setSnapshots([]);
    setSelectedSnapshotId("");
    if (!candidate) return;
    const controller = new AbortController();
    fetch(`${API_URL}/api/candidates/${candidate.id}/snapshots`, {
      signal: controller.signal,
    })
      .then(async (r) => {
        const loaded = r.ok ? ((await r.json()) as ApiSnapshot[]) : [];
        if (!controller.signal.aborted) {
          setSnapshots(loaded);
          if (
            project?.decision_snapshot_id &&
            loaded.some((item) => item.id === project.decision_snapshot_id)
          ) {
            setSelectedSnapshotId(project.decision_snapshot_id);
          }
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setSnapshots([]);
      });
    return () => controller.abort();
  }, [candidate?.id, activeProjectId, project?.decision_snapshot_id]);
  const save = async () => {
    if (!project) return;
    const r = await fetch(`${API_URL}/api/projects/${project.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(project),
    });
    if (!r.ok) return setError("保存できませんでした。");
    const saved = (await r.json()) as ApiProject;
    setProject(saved);
    onProjectChanged(saved);
  };
  const createProject = async () => {
    const r = await fetch(`${API_URL}/api/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: `新しい検討 ${projects.length + 1}`,
        description: "",
        purpose: "",
        task_id: "annealed-properties-v1",
        target_values: {},
        notes: "",
      }),
    });
    if (!r.ok) return setError("新しいプロジェクトを作成できませんでした。");
    const created = (await r.json()) as ApiProject;
    const initial = candidate
      ? (() => {
          const payload = toApiCandidate(candidate);
          delete payload.id;
          return { ...payload, name: "基準候補" };
        })()
      : STARTER_CANDIDATE;
    const candidateResponse = await fetch(
      `${API_URL}/api/candidates?project_id=${encodeURIComponent(created.id)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(initial),
      },
    );
    if (!candidateResponse.ok)
      return setError(
        "プロジェクトは作成しましたが、基準候補を作成できませんでした。",
      );
    onProjectChanged(created);
    onSwitch(created.id);
  };
  const restore = async (id: string) => {
    const r = await fetch(`${API_URL}/api/snapshots/${id}/restore`, {
      method: "POST",
    });
    if (!r.ok) {
      const cause = await apiError(
        r,
        "スナップショットを復元できませんでした。",
      );
      return setError(cause.message);
    }
    onRestore(fromApiCandidate((await r.json()) as ApiCandidate));
  };
  const targetValues = (project?.target_values ?? {}) as Record<string, number>;
  const selectedSnapshot = snapshots.find(
    (item) => item.id === selectedSnapshotId,
  );
  const setTarget = (key: string, value: string) => {
    if (!project) return;
    const next = { ...targetValues };
    if (value === "") delete next[key];
    else next[key] = Number(value);
    setProject({ ...project, target_values: next });
  };
  return (
    <div className="page-panel">
      <div className="page-intro">
        <div>
          <h2>プロジェクト</h2>
          <p>目的、目標、判断メモ、保存済みの予測を管理します。</p>
        </div>
        <div className="project-actions">
          <label>
            表示中
            <select
              value={activeProjectId}
              onChange={(event) => onSwitch(event.target.value)}
            >
              {projects.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <button
            className="outline-button"
            onClick={() => {
              void createProject();
            }}
          >
            新規プロジェクト
          </button>
          <button
            className="primary-button"
            onClick={() => {
              void save();
            }}
          >
            保存
          </button>
        </div>
      </div>
      {error && <p className="empty-evidence">{error}</p>}
      {project ? (
        <div className="project-form">
          <label>
            プロジェクト名
            <input
              value={String(project.name ?? "")}
              onChange={(e) => setProject({ ...project, name: e.target.value })}
            />
          </label>
          <label>
            説明
            <textarea
              value={String(project.description ?? "")}
              onChange={(e) =>
                setProject({ ...project, description: e.target.value })
              }
            />
          </label>
          <label>
            目的
            <textarea
              value={String(project.purpose ?? "")}
              onChange={(e) =>
                setProject({ ...project, purpose: e.target.value })
              }
            />
          </label>
          <label>
            予測タスク
            <select
              value={String(project.task_id ?? "annealed-properties-v1")}
              onChange={(event) =>
                setProject({ ...project, task_id: event.target.value })
              }
            >
              <option value="annealed-properties-v1">
                焼鈍後特性（TS / YS / EL / λ）
              </option>
            </select>
            <small>
              モデルPackageが同じタスク契約を満たす場合に差し替えられます。
            </small>
          </label>
          <fieldset className="target-grid">
            <legend>目標値</legend>
            {["TS", "YS", "EL", "lambda"].map((key) => (
              <label key={key}>
                {key === "lambda" ? "λ" : key}
                <input
                  type="number"
                  value={targetValues[key] ?? ""}
                  placeholder="未設定"
                  onChange={(event) => setTarget(key, event.target.value)}
                />
              </label>
            ))}
          </fieldset>
          <label>
            メモ
            <textarea
              value={String(project.notes ?? "")}
              onChange={(e) =>
                setProject({ ...project, notes: e.target.value })
              }
            />
          </label>
        </div>
      ) : (
        <p className="empty-evidence">プロジェクトを読み込んでいます。</p>
      )}
      <section>
        <h3>モデル実行基盤</h3>
        {modelPackage ? (
          <div className="model-package-card">
            <div>
              <strong>{modelPackage.id}</strong>
              <span>v{modelPackage.version}</span>
              <code>{modelPackage.manifest_sha256.slice(0, 12)}</code>
            </div>
            <p>
              {modelPackage.predictors
                .map((item) => `${item.target}: ${item.runtime_type}`)
                .join(" / ")}
            </p>
            <div className="runtime-list" aria-label="利用可能なモデル実行基盤">
              {modelPackage.supported_runtimes.map((item) => (
                <span
                  className={item.available ? "available" : "optional"}
                  key={item.runtime_type}
                >
                  {item.runtime_type.replace(/\.v1$/, "")}
                  {item.available ? " ✓" : " (追加導入)"}
                </span>
              ))}
            </div>
          </div>
        ) : (
          <p className="empty-evidence">モデルPackage情報を取得できません。</p>
        )}
      </section>
      <section>
        <h3>保存済み予測</h3>
        {snapshots.length ? (
          <table className="quality-table">
            <tbody>
              {snapshots.map((snapshot) => (
                <tr key={snapshot.id}>
                  <td>
                    {new Date(snapshot.created_at).toLocaleString("ja-JP")}
                    {snapshot.id === project?.decision_snapshot_id && (
                      <span className="decision-snapshot-badge">採用判断</span>
                    )}
                  </td>
                  <td>
                    <button
                      className="outline-button"
                      onClick={() => setSelectedSnapshotId(snapshot.id)}
                    >
                      {snapshot.id === project?.decision_snapshot_id
                        ? "採用判断を見る"
                        : "結果を見る"}
                    </button>
                    <button
                      className="outline-button"
                      onClick={() => {
                        void restore(snapshot.id);
                      }}
                    >
                      この時点を候補として復元
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty-evidence">選択候補の保存済み予測はありません。</p>
        )}
        {selectedSnapshot?.payload.prediction && (
          <div className="snapshot-detail">
            <h4>保存時点の結果</h4>
            <table className="quality-table">
              <thead>
                <tr>
                  <th>特性</th>
                  <th>予測</th>
                  <th>90%区間</th>
                  <th>目標達成</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(
                  selectedSnapshot.payload.prediction.predictions ?? {},
                ).map(([target, result]) => (
                  <tr key={target}>
                    <th>{target === "lambda" ? "λ" : target}</th>
                    <td>
                      {number(result.value, 1)} {result.unit}
                    </td>
                    <td>
                      {number(result.lower, 1)}–{number(result.upper, 1)}
                    </td>
                    <td>
                      {result.goal_probability == null
                        ? "—"
                        : `${number(result.goal_probability * 100, 0)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="provenance-line">
              Package {selectedSnapshot.payload.provenance?.package?.id ?? "—"}{" "}
              / pipeline{" "}
              {selectedSnapshot.payload.provenance?.feature_pipeline?.version ??
                "—"}{" "}
              / data{" "}
              {selectedSnapshot.payload.provenance?.training_data?.source_sha256?.slice(
                0,
                12,
              ) ?? "—"}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

function LiveScreeningPage({
  projectId,
  onCandidate,
}: {
  projectId: string;
  onCandidate: (candidate: Candidate) => void;
}) {
  type VariableRow = {
    field: string;
    mode: "fixed" | "range" | "list";
    first: string;
    second: string;
  };
  type ScreenPoint = {
    index: number;
    inputs: Record<string, number | string>;
    prediction: { value: number; unit: string };
    support: { status: string };
  };
  type ScreenResult = {
    id: string;
    created_at?: string;
    target: string;
    target_value: number;
    samples: number;
    variables?: Record<
      string,
      {
        mode: "fixed" | "range" | "list";
        value?: number | string;
        min?: number;
        max?: number;
        values?: Array<number | string>;
      }
    >;
    points: ScreenPoint[];
    representative_points: ScreenPoint[];
  };
  const options = [
    ...COMPOSITION_ELEMENTS.map((element) => ({
      value: `composition.${element}`,
      label: `${element} (mass%)`,
    })),
    { value: "thickness_mm", label: "板厚 (mm)" },
    { value: "line_speed_m_min", label: "ライン速度 (m/min)" },
    { value: "max_temperature_c", label: "最高温度 (°C)" },
    { value: "coating", label: "めっき" },
  ];
  const [variables, setVariables] = useState<VariableRow[]>([
    { field: "composition.C", mode: "range", first: "0.03", second: "0.12" },
    { field: "composition.Mn", mode: "range", first: "1.0", second: "2.0" },
  ]);
  const [samples, setSamples] = useState(64);
  const [target, setTarget] = useState("TS");
  const [targetValue, setTargetValue] = useState("500");
  const [result, setResult] = useState<ScreenResult | null>(null);
  const [savedRuns, setSavedRuns] = useState<ScreenResult[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    fetch(
      `${API_URL}/api/screening?project_id=${encodeURIComponent(projectId)}`,
    )
      .then(async (response) => {
        if (response.ok) setSavedRuns(await response.json());
      })
      .catch(() => undefined);
  }, [projectId]);
  const updateVariable = (index: number, patch: Partial<VariableRow>) =>
    setVariables((rows) =>
      rows.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...patch } : row,
      ),
    );
  const run = async () => {
    try {
      setError("");
      const specs = Object.fromEntries(
        variables.map((row) => {
          const categorical = row.field === "coating";
          if (row.mode === "range")
            return [
              row.field,
              {
                mode: row.mode,
                min: Number(row.first),
                max: Number(row.second),
              },
            ];
          if (row.mode === "list")
            return [
              row.field,
              {
                mode: row.mode,
                values: row.first
                  .split(",")
                  .map((value) =>
                    categorical ? value.trim() : Number(value.trim()),
                  ),
              },
            ];
          return [
            row.field,
            {
              mode: row.mode,
              value: categorical ? row.first.trim() : Number(row.first),
            },
          ];
        }),
      );
      const r = await fetch(
        `${API_URL}/api/screening?project_id=${encodeURIComponent(projectId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            variables: specs,
            samples,
            target,
            target_value: Number(targetValue),
          }),
        },
      );
      if (!r.ok)
        throw new Error(((await r.json()) as { detail?: string }).detail);
      const created = (await r.json()) as ScreenResult;
      setResult(created);
      setSavedRuns((runs) => [created, ...runs]);
    } catch (cause) {
      setError(
        `範囲探索を実行できませんでした。${cause instanceof Error && cause.message ? ` ${cause.message}` : ""}`,
      );
    }
  };
  const loadRun = async (runId: string) => {
    const response = await fetch(
      `${API_URL}/api/screening/${runId}?project_id=${encodeURIComponent(projectId)}`,
    );
    if (!response.ok) return setError("保存済み探索を開けませんでした。");
    const run = (await response.json()) as ScreenResult;
    setResult(run);
    setTarget(run.target);
    setTargetValue(String(run.target_value));
    setSamples(run.samples);
    if (run.variables)
      setVariables(
        Object.entries(run.variables).map(([field, spec]) => ({
          field,
          mode: spec.mode,
          first:
            spec.mode === "fixed"
              ? String(spec.value ?? "")
              : spec.mode === "list"
                ? (spec.values ?? []).join(",")
                : String(spec.min ?? ""),
          second: spec.mode === "range" ? String(spec.max ?? "") : "",
        })),
      );
  };
  const persist = async (index: number) => {
    if (!result) return;
    const r = await fetch(
      `${API_URL}/api/screening/${result.id}/points/${index}/candidate?project_id=${encodeURIComponent(projectId)}`,
      { method: "POST" },
    );
    if (r.ok) onCandidate(fromApiCandidate((await r.json()) as ApiCandidate));
    else setError((await apiError(r, "候補を作成できませんでした。")).message);
  };
  const axes = variables
    .filter((row) => row.mode !== "fixed" && row.field !== "coating")
    .slice(0, 2)
    .map((row) => row.field);
  const numeric = (axis: string) =>
    result?.points
      .map((point) => Number(point.inputs[axis]))
      .filter(Number.isFinite) ?? [];
  const xValues = numeric(axes[0]);
  const yValues = numeric(axes[1] ?? axes[0]);
  const scale = (
    value: number,
    values: number[],
    start: number,
    span: number,
  ) =>
    start +
    ((value - Math.min(...values)) /
      Math.max(1e-9, Math.max(...values) - Math.min(...values))) *
      span;
  const errors =
    result?.points.map((point) =>
      Math.abs(point.prediction.value - result.target_value),
    ) ?? [];
  const opportunity = (point: ScreenPoint) => {
    const error = Math.abs(
      point.prediction.value - (result?.target_value ?? Number(targetValue)),
    );
    const closeness =
      1 -
      (error - Math.min(...errors)) /
        Math.max(1e-9, Math.max(...errors) - Math.min(...errors));
    return `hsl(215 78% ${82 - closeness * 42}%)`;
  };
  const supportStroke = (status: string) =>
    status === "supported"
      ? "#15936a"
      : status === "caution"
        ? "#ee9200"
        : "#c43d3d";
  return (
    <div className="page-panel explore-page">
      <div className="page-intro">
        <div>
          <h2>範囲探索</h2>
          <p>
            固定・範囲・列挙条件を組み合わせ、Latin
            Hypercubeで48〜128点を評価します。
          </p>
        </div>
        <button
          className="primary-button"
          onClick={() => {
            void run();
          }}
        >
          探索を実行
        </button>
      </div>
      {savedRuns.length > 0 && (
        <section className="saved-runs">
          <h3>保存済み探索</h3>
          <div>
            {savedRuns.slice(0, 8).map((run) => (
              <button
                className={result?.id === run.id ? "active" : ""}
                key={run.id}
                onClick={() => {
                  void loadRun(run.id);
                }}
              >
                <b>{run.target}</b> → {number(run.target_value, 1)} /{" "}
                {run.samples}点{" "}
                <small>
                  {run.created_at
                    ? new Date(run.created_at).toLocaleString("ja-JP")
                    : "保存済み"}
                </small>
              </button>
            ))}
          </div>
        </section>
      )}
      <div className="screening-settings">
        <div className="screening-target">
          <label>
            評価点数
            <input
              type="number"
              min="48"
              max="128"
              value={samples}
              onChange={(event) => setSamples(Number(event.target.value))}
            />
          </label>
          <label>
            目標特性
            <select
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            >
              <option>TS</option>
              <option>YS</option>
              <option>EL</option>
              <option value="lambda">λ</option>
            </select>
          </label>
          <label>
            目標値
            <input
              type="number"
              value={targetValue}
              onChange={(event) => setTargetValue(event.target.value)}
            />
          </label>
        </div>
        <table className="quality-table variable-table">
          <thead>
            <tr>
              <th>変数</th>
              <th>指定</th>
              <th>値 / 最小</th>
              <th>最大</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {variables.map((row, index) => (
              <tr key={`${row.field}-${index}`}>
                <td>
                  <select
                    value={row.field}
                    onChange={(event) =>
                      updateVariable(index, { field: event.target.value })
                    }
                  >
                    {options.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    value={row.mode}
                    onChange={(event) =>
                      updateVariable(index, {
                        mode: event.target.value as VariableRow["mode"],
                      })
                    }
                  >
                    <option value="fixed">固定</option>
                    <option value="range">範囲</option>
                    <option value="list">列挙</option>
                  </select>
                </td>
                <td>
                  <input
                    value={row.first}
                    placeholder={row.mode === "list" ? "例: GI,GA" : "値"}
                    onChange={(event) =>
                      updateVariable(index, { first: event.target.value })
                    }
                  />
                </td>
                <td>
                  {row.mode === "range" ? (
                    <input
                      value={row.second}
                      onChange={(event) =>
                        updateVariable(index, { second: event.target.value })
                      }
                    />
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  <button
                    className="icon-delete"
                    disabled={variables.length === 1}
                    onClick={() =>
                      setVariables((rows) =>
                        rows.filter((_, rowIndex) => rowIndex !== index),
                      )
                    }
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button
          className="outline-button"
          onClick={() =>
            setVariables((rows) => [
              ...rows,
              {
                field: "thickness_mm",
                mode: "fixed",
                first: "1.4",
                second: "",
              },
            ])
          }
        >
          変数を追加
        </button>
      </div>
      {error && <p className="warning">{error}</p>}
      {result && (
        <>
          <div className="screen-legend">
            <span className="opportunity-scale" />
            目標に近い <span className="support-key supported" />
            範囲内 <span className="support-key caution" />
            要確認 <span className="support-key extrapolated" />
            外挿
          </div>
          <svg
            className="screen-map"
            viewBox="0 0 600 300"
            role="img"
            aria-label={`${axes.join(" × ")} の探索結果。色が濃いほど目標に近く、枠線が学習範囲を示します。`}
          >
            {result.points.map((point, index) => {
              const cx = axes.length
                ? scale(Number(point.inputs[axes[0]]), xValues, 35, 530)
                : 35 + (index % 12) * 46;
              const cy =
                axes.length > 1
                  ? 270 - scale(Number(point.inputs[axes[1]]), yValues, 0, 235)
                  : 35 + Math.floor(index / 12) * 50;
              return (
                <circle
                  key={point.index}
                  cx={cx}
                  cy={cy}
                  r="7"
                  fill={opportunity(point)}
                  stroke={supportStroke(point.support.status)}
                  strokeWidth="3"
                  opacity={
                    point.support.status === "extrapolated" ? ".55" : ".9"
                  }
                  onClick={() => {
                    void persist(point.index);
                  }}
                >
                  <title>{`${axes.map((axis) => `${axis}: ${point.inputs[axis]}`).join(" / ")} / ${point.prediction.value.toFixed(1)} ${point.prediction.unit} / ${point.support.status}`}</title>
                </circle>
              );
            })}
            <text x="300" y="296" textAnchor="middle">
              {axes[0]}
            </text>
            <text x="8" y="16">
              {axes[1]}
            </text>
          </svg>
          <table className="quality-table">
            <thead>
              <tr>
                <th>代表点</th>
                <th>条件</th>
                <th>予測</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {result.representative_points.map((point) => (
                <tr key={point.index}>
                  <td>{point.index + 1}</td>
                  <td>
                    {Object.entries(point.inputs)
                      .map(
                        ([key, value]) =>
                          `${key}: ${typeof value === "number" ? number(value, 3) : value}`,
                      )
                      .join(" / ")}
                  </td>
                  <td>
                    {number(point.prediction.value, 1)} {point.prediction.unit}
                  </td>
                  <td>
                    <button
                      className="outline-button"
                      onClick={() => {
                        void persist(point.index);
                      }}
                    >
                      候補化
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

export default App;
