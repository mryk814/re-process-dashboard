import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import type {
  ApiProject,
  ApiPredictionGraphCatalog,
  ApiPredictionGraphDefinition,
  ApiPredictionGraphPublishResponse,
  ApiPredictionGraphValidation,
} from "../../shared/api/workbench-api";
import { workbenchApi } from "../../shared/api/workbench-api";
import {
  addDecisionOutput,
  addInputAndBind,
  addStage,
  compatibleSources,
  connectSource,
  graphPresentationEdges,
  initializeGraph,
  moveStage,
  removeBinding,
  removeDecisionOutput,
  removeInput,
  removeStage,
  setInputRole,
  sourceKey,
  sourceLabel,
  stageCatalogItem,
  topologicalLayers,
  type BindingSource,
  type DraftSelection,
  type GraphDecisionOutput,
  type GraphPort,
  type SourceOption,
} from "./predictionGraphDraft";

type Props = {
  onProjectCreated: (project: ApiProject) => void;
  registerNavigationGuard: (guard: () => Promise<boolean>) => () => void;
};

function portLabel(port: GraphPort) {
  return `${port.value_kind} · ${port.quantity} · ${port.unit ?? "単位なし"}${port.basis ? ` · ${port.basis}` : ""}`;
}

function allSourceOptions(
  definition: ApiPredictionGraphDefinition,
  catalog: ApiPredictionGraphCatalog,
): SourceOption[] {
  const inputs = definition.inputs.map((input) => ({
    key: `input:${input.input_id}`,
    label: `Input · ${input.label}`,
    source: { source_kind: "external" as const, path: input.input_id },
    port: input.port,
  }));
  const outputs = definition.stages.flatMap((stage) => {
    const surface = stageCatalogItem(catalog, stage)?.surface;
    return (surface?.output_ports ?? []).map((port) => ({
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

function selectionKey(selection: DraftSelection | undefined) {
  if (!selection) return "";
  if (selection.kind === "binding") return `binding:${selection.id}:${selection.port}`;
  return `${selection.kind}:${selection.id}`;
}

export function ChainStudioPage({ onProjectCreated, registerNavigationGuard }: Props) {
  const [catalog, setCatalog] = useState<ApiPredictionGraphCatalog>();
  const [definition, setDefinition] = useState<ApiPredictionGraphDefinition>();
  const [loading, setLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string>();
  const [selection, setSelection] = useState<DraftSelection>();
  const [connecting, setConnecting] = useState<SourceOption>();
  const [portError, setPortError] = useState<{ key: string; message: string }>();
  const [validation, setValidation] = useState<ApiPredictionGraphValidation>();
  const [published, setPublished] = useState<ApiPredictionGraphPublishResponse>();
  const [projectName, setProjectName] = useState("新しい判断Project");
  const [submitting, setSubmitting] = useState<"validate" | "publish" | null>(null);
  const [actionError, setActionError] = useState<string>();
  const [zoom, setZoom] = useState(1);
  const [compact, setCompact] = useState(false);
  const [linearCatalogKey, setLinearCatalogKey] = useState("");
  const focusTargets = useRef(new Map<string, HTMLElement>());
  const canvasRef = useRef<HTMLDivElement>(null);
  const edgeAnchors = useRef(new Map<string, HTMLElement>());
  const [edgePaths, setEdgePaths] = useState<Array<{ key: string; kind: "binding" | "decision_output"; d: string }>>([]);
  const candidatePaths = useRef(new Map<string, string>());
  const mounted = useRef(true);
  const requestGeneration = useRef(0);
  const submissionPending = useRef(false);
  // Draft lifetime stays screen-local in this PR. Cross-screen persistence is tracked by #716.

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void workbenchApi.predictionGraphCatalog(controller.signal).then((response) => {
      if (controller.signal.aborted) return;
      setCatalog(response);
      setDefinition((current) => current ?? initializeGraph(response));
      const first = response.stages.find((item) => item.status === "available" && item.surface);
      if (first) setLinearCatalogKey(`${first.stage_kind}:${first.contract_id}`);
      setLoading(false);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setCatalogError(reason instanceof Error ? reason.message : "Prediction Graph catalogを取得できませんでした。");
      setLoading(false);
    });
    return () => {
      mounted.current = false;
      requestGeneration.current += 1;
      controller.abort();
    };
  }, []);

  useEffect(() => registerNavigationGuard(
    async () => !submissionPending.current,
  ), [registerNavigationGuard]);

  useEffect(() => {
    for (const input of definition?.inputs ?? []) {
      if (input.value_source.source_kind === "candidate") {
        candidatePaths.current.set(input.input_id, input.value_source.candidate_path);
      }
    }
  }, [definition]);

  const stagesByLayer = useMemo(
    () => definition ? topologicalLayers(definition) : [],
    [definition],
  );
  const sourceOptions = useMemo(
    () => definition && catalog ? allSourceOptions(definition, catalog) : [],
    [catalog, definition],
  );
  const presentationEdges = useMemo(
    () => definition ? graphPresentationEdges(definition) : [],
    [definition],
  );

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const canvasRect = canvas.getBoundingClientRect();
      const paths = presentationEdges.flatMap((edge) => {
        const source = edgeAnchors.current.get(edge.sourceKey);
        const target = edgeAnchors.current.get(edge.targetKey);
        if (!source || !target) return [];
        const sourceRect = source.getBoundingClientRect();
        const targetRect = target.getBoundingClientRect();
        const x1 = (sourceRect.right - canvasRect.left) / zoom;
        const y1 = (sourceRect.top + sourceRect.height / 2 - canvasRect.top) / zoom;
        const x2 = (targetRect.left - canvasRect.left) / zoom;
        const y2 = (targetRect.top + targetRect.height / 2 - canvasRect.top) / zoom;
        const bend = Math.max(28, (x2 - x1) * .45);
        return [{
          key: edge.key,
          kind: edge.kind,
          d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
        }];
      });
      setEdgePaths(paths);
    };
    const frame = window.requestAnimationFrame(draw);
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    window.addEventListener("resize", draw);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", draw);
    };
  }, [compact, presentationEdges, zoom]);

  function registerFocus(key: string) {
    return (element: HTMLElement | null) => {
      if (element) focusTargets.current.set(key, element);
      else focusTargets.current.delete(key);
    };
  }

  function registerEdgeAnchor(key: string) {
    return (element: HTMLElement | null) => {
      if (element) edgeAnchors.current.set(key, element);
      else edgeAnchors.current.delete(key);
    };
  }

  function registerFocusAndEdge(focusKey: string, edgeKey: string) {
    return (element: HTMLElement | null) => {
      registerFocus(focusKey)(element);
      registerEdgeAnchor(edgeKey)(element);
    };
  }

  function change(next: ApiPredictionGraphDefinition) {
    setDefinition(next);
    setValidation(undefined);
    setPublished(undefined);
    setActionError(undefined);
  }

  function selectSource(option: SourceOption) {
    setConnecting((current) => current?.key === option.key ? undefined : option);
    setSelection(option.source.source_kind === "external"
      ? { kind: "input", id: option.source.path }
      : { kind: "stage", id: option.source.stage_id, port: option.source.output_key });
    setPortError(undefined);
  }

  function connect(targetStageId: string, targetPort: GraphPort, source: BindingSource) {
    if (!definition || !catalog) return;
    const result = connectSource(definition, catalog, targetStageId, targetPort, source);
    if (result.error) {
      setPortError({ key: `${targetStageId}:${targetPort.path}`, message: result.error });
      focusTargets.current.get(`binding:${targetStageId}:${targetPort.path}`)?.focus();
      return;
    }
    change(result.definition);
    setConnecting(undefined);
    setPortError(undefined);
    setSelection({ kind: "binding", id: targetStageId, port: targetPort.path });
  }

  function focusFinding(finding: ApiPredictionGraphValidation["findings"][number]) {
    const target = finding.target;
    const key = target.target_kind === "binding"
      ? `binding:${target.target_id}:${target.port_path ?? ""}`
      : target.target_kind === "stage" && target.port_path
        ? `binding:${target.target_id}:${target.port_path}`
      : target.target_kind === "decision_output"
        ? `output:${target.target_id}`
        : `${target.target_kind}:${target.target_id}`;
    if (target.target_kind === "binding") {
      setSelection({ kind: "binding", id: target.target_id, port: target.port_path ?? "" });
    } else if (target.target_kind === "stage" && target.port_path) {
      setSelection({ kind: "binding", id: target.target_id, port: target.port_path });
    } else if (target.target_kind === "stage") {
      setSelection({ kind: "stage", id: target.target_id });
    } else if (target.target_kind === "input") {
      setSelection({ kind: "input", id: target.target_id });
    } else if (target.target_kind === "decision_output") {
      setSelection({ kind: "output", id: target.target_id });
    }
    window.requestAnimationFrame(() => {
      const targetElement = focusTargets.current.get(key)
        ?? focusTargets.current.get("graph:draft");
      targetElement?.focus();
    });
  }

  async function validateDraft() {
    if (!definition) return undefined;
    const generation = ++requestGeneration.current;
    const isCurrent = () => mounted.current && requestGeneration.current === generation;
    submissionPending.current = true;
    setSubmitting("validate");
    setActionError(undefined);
    try {
      const result = await workbenchApi.validatePredictionGraph(definition);
      if (!isCurrent()) return undefined;
      setValidation(result);
      if (result.findings[0]) focusFinding(result.findings[0]);
      return result;
    } catch (reason) {
      if (!isCurrent()) return undefined;
      setActionError(reason instanceof Error ? reason.message : "Prediction Graphを検証できませんでした。");
      return undefined;
    } finally {
      if (isCurrent()) {
        submissionPending.current = false;
        setSubmitting(null);
      }
    }
  }

  async function publishAndCreateProject() {
    if (!definition) return;
    const generation = ++requestGeneration.current;
    const isCurrent = () => mounted.current && requestGeneration.current === generation;
    submissionPending.current = true;
    setSubmitting("publish");
    setActionError(undefined);
    try {
      let revision = published;
      if (!revision) {
        const checked = await workbenchApi.validatePredictionGraph(definition);
        if (!isCurrent()) return;
        setValidation(checked);
        if (!checked.valid) {
          if (checked.findings[0]) focusFinding(checked.findings[0]);
          return;
        }
        revision = await workbenchApi.publishPredictionGraph(definition);
        if (!isCurrent()) return;
        setPublished(revision);
      }
      const project = await workbenchApi.createPredictionGraphProject({
        project: {
          name: projectName.trim() || `${definition.label} Project`,
          purpose: definition.label,
          description: "",
          notes: "",
          task_id: "",
          task_contract_digest: "",
          model_package_manifest_digest: "",
          response_curve_points: 17,
          continuation_reason: "",
          decision_candidate_id: "",
          decision_snapshot_id: "",
          decision_note: "",
        },
        graph_revision_id: revision.graph_revision_id,
        graph_revision_digest: revision.revision.revision_digest,
        project_binding_revision: 1,
        project_binding_values: {},
      });
      if (!isCurrent()) return;
      submissionPending.current = false;
      setSubmitting(null);
      onProjectCreated(project);
    } catch (reason) {
      if (!isCurrent()) return;
      setActionError(reason instanceof Error ? reason.message : "Revisionの公開またはProject作成に失敗しました。");
    } finally {
      if (isCurrent()) {
        submissionPending.current = false;
        setSubmitting(null);
      }
    }
  }

  function updateOutput(outputId: string, updates: Partial<GraphDecisionOutput>) {
    if (!definition) return;
    change({
      ...definition,
      decision_outputs: definition.decision_outputs.map((output) => (
        output.output_id === outputId ? { ...output, ...updates } : output
      )),
    });
  }

  if (loading) {
    return <section className="chain-studio-state" aria-live="polite">
      <span className="overline">PREDICTION GRAPH STUDIO</span>
      <h2>利用できるNodeを読み込み中です</h2>
    </section>;
  }
  if (!catalog || !definition) {
    return <section className="chain-studio-state" role="alert">
      <span className="overline">PREDICTION GRAPH STUDIO</span>
      <h2>Graph Studioを開始できません</h2>
      <p>{catalogError ?? "利用できるTask／Transformがありません。"}</p>
    </section>;
  }

  const availableCatalog = catalog.stages.filter((item) => item.status === "available" && item.surface);
  const selectedStage = selection?.kind === "stage"
    ? definition.stages.find((stage) => stage.stage_id === selection.id)
    : undefined;
  const selectedCatalog = selectedStage ? stageCatalogItem(catalog, selectedStage) : undefined;
  const selectedOutput = selection?.kind === "output"
    ? definition?.decision_outputs.find((item) => item.output_id === selection.id)
    : undefined;

  return <section className="chain-studio" aria-labelledby="chain-studio-heading">
    <header className="chain-studio-header">
      <div>
        <span className="overline">PREDICTION GRAPH STUDIO</span>
        <h2 id="chain-studio-heading" tabIndex={-1} ref={registerFocus("graph:draft")}>入力・Model・判断出力を直接つなぐ</h2>
        <p>依存関係からlayerを組み、同じdraftをCanvasと一覧のどちらからでも編集できます。固定参照は公開時にサーバが解決します。</p>
      </div>
      <div className="chain-studio-scope">
        <strong>{catalog.candidate_adapter_ids.join(" / ")}</strong>
        <span>allow-list済みTask／Transformのみ。任意codeやclient指定lockは保存しません。</span>
      </div>
    </header>

    <fieldset className="chain-studio-edit-lock" disabled={submitting !== null} aria-busy={submitting !== null}>
      <legend className="sr-only">Prediction Graph draft編集</legend>
    <section className="chain-studio-panel chain-studio-identity" aria-labelledby="graph-identity">
      <h3 id="graph-identity">Graphの目的</h3>
      <div className="chain-studio-fields">
        <label>Graph ID<input value={definition.graph_id} onChange={(event) => change({ ...definition, graph_id: event.target.value })} /></label>
        <label>表示名／目的<input value={definition.label} onChange={(event) => change({ ...definition, label: event.target.value })} /></label>
        <label>作成するProject名<input value={projectName} onChange={(event) => setProjectName(event.target.value)} /></label>
      </div>
    </section>

    <section className="chain-studio-panel" aria-labelledby="graph-catalog">
      <div className="chain-studio-section-title">
        <div><h3 id="graph-catalog">Model／Transformを追加</h3><p>Package版は表示だけ確認し、選択値として送信しません。</p></div>
      </div>
      <div className="chain-studio-catalog">
        {availableCatalog.map((item) => <button
          type="button"
          className="chain-studio-catalog-item"
          key={`${item.stage_kind}:${item.contract_id}`}
          onClick={() => {
            const added = addStage(definition, item);
            change(added.definition);
            setSelection({ kind: "stage", id: added.stageId });
          }}
        >
          <b>{item.stage_kind === "task" ? "Model / Task" : "Transform"}</b>
          <span>{item.label}</span>
          <small>{item.surface?.input_ports.length} input · {item.surface?.output_ports.length} output</small>
        </button>)}
      </div>
    </section>

    <section className="chain-studio-panel" aria-labelledby="graph-canvas-heading">
      <div className="chain-studio-section-title">
        <div><h3 id="graph-canvas-heading">Dependency Canvas</h3><p>source portを選び、互換targetへ接続します。drag/dropとkeyboard clickは同じ操作です。</p></div>
        <div className="chain-studio-canvas-tools" aria-label="Canvas表示">
          <button type="button" onClick={() => setZoom((value) => Math.max(.7, value - .1))}>縮小</button>
          <button type="button" onClick={() => setZoom(1)}>fit</button>
          <button type="button" onClick={() => { setZoom(1); setCompact(false); setSelection(undefined); setConnecting(undefined); }}>reset</button>
          <button type="button" aria-pressed={compact} onClick={() => setCompact((value) => !value)}>compact</button>
        </div>
      </div>
      {connecting && <p className="chain-studio-connect-status" role="status">{connecting.label} の接続先を選択中。互換portだけが有効です。</p>}
      <div className="chain-studio-canvas-viewport">
        <div
          ref={canvasRef}
          className={`chain-studio-canvas${compact ? " compact" : ""}`}
          style={{ transform: `scale(${zoom})` }}
          data-presentation-zoom={zoom}
        >
          <svg className="chain-studio-edges" aria-hidden="true">
            {edgePaths.map((edge) => <path
              key={edge.key}
              d={edge.d}
              data-edge-kind={edge.kind}
              data-edge-key={edge.key}
            />)}
          </svg>
          <div className="chain-studio-layer">
            <span className="chain-studio-layer-label">INPUT</span>
            {definition.inputs.map((input) => {
              const option = sourceOptions.find((item) => item.key === `input:${input.input_id}`)!;
              return <article
                key={input.input_id}
                className={`chain-studio-node input-node${selectionKey(selection) === `input:${input.input_id}` ? " selected" : ""}`}
              >
                <button type="button" className="chain-studio-node-title" ref={registerFocus(`input:${input.input_id}`)} onClick={() => setSelection({ kind: "input", id: input.input_id })}>
                  <b>{input.label}</b><small>{input.role.replaceAll("_", " ")}</small>
                </button>
                <button ref={registerEdgeAnchor(`source:${option.key}`)} type="button" draggable className={`chain-studio-port output${connecting?.key === option.key ? " active" : ""}`} onDragStart={(event) => {
                  event.dataTransfer.setData("text/plain", option.key);
                  selectSource(option);
                }} onClick={() => selectSource(option)}>
                  {input.port.path}<small>{input.port.unit ?? input.port.value_kind}</small>
                </button>
              </article>;
            })}
          </div>
          {stagesByLayer.map((layer, layerIndex) => <div className="chain-studio-layer" key={`layer-${layerIndex}`}>
            <span className="chain-studio-layer-label">LAYER {layerIndex + 1}</span>
            {layer.map((stageId) => {
              const stage = definition.stages.find((item) => item.stage_id === stageId)!;
              const item = stageCatalogItem(catalog, stage);
              const surface = item?.surface;
              return <article
                key={stageId}
                className={`chain-studio-node ${stage.stage_kind === "task" ? "model-node" : "transform-node"}${selection?.kind === "stage" && selection.id === stageId ? " selected" : ""}`}
              >
                <button type="button" className="chain-studio-node-title" ref={registerFocus(`stage:${stageId}`)} onClick={() => setSelection({ kind: "stage", id: stageId })}>
                  <b>{stageId}</b><small>{stage.stage_kind === "task" ? "Model / Task" : "Deterministic Transform"}</small>
                </button>
                <div className="chain-studio-node-ports inputs">
                  {(surface?.input_ports ?? []).map((port) => {
                    const key = `${stageId}:${port.path}`;
                    const current = definition.bindings.find((binding) => binding.target_stage_id === stageId && binding.target_input_path === port.path);
                    const compatible = connecting && compatibleSources(definition, catalog, stageId, port).some((option) => option.key === connecting.key);
                    return <div key={key}>
                      <button
                        type="button"
                        ref={registerFocusAndEdge(`binding:${stageId}:${port.path}`, `target:${stageId}:${port.path}`)}
                        className={`chain-studio-port input${compatible ? " compatible" : ""}`}
                        aria-label={`${stageId}.${port.path} input。${current ? `${sourceLabel(current.source)}から接続済み` : "未接続"}`}
                        onDragOver={(event) => { if (compatible) event.preventDefault(); }}
                        onDrop={() => { if (connecting) connect(stageId, port, connecting.source); }}
                        onClick={() => connecting ? connect(stageId, port, connecting.source) : setSelection({ kind: "binding", id: stageId, port: port.path })}
                      >
                        {port.path}<small>{current ? `← ${sourceLabel(current.source)}` : "未接続"}</small>
                      </button>
                      {portError?.key === key && <small className="chain-studio-port-error" role="alert">{portError.message}</small>}
                    </div>;
                  })}
                </div>
                <div className="chain-studio-node-ports outputs">
                  {(surface?.output_ports ?? []).map((port) => {
                    const option = sourceOptions.find((entry) => entry.key === `stage:${stageId}:${port.path}`)!;
                    return <button ref={registerEdgeAnchor(`source:${option.key}`)} type="button" draggable className={`chain-studio-port output${connecting?.key === option.key ? " active" : ""}`} key={port.path} onDragStart={(event) => {
                      event.dataTransfer.setData("text/plain", option.key);
                      selectSource(option);
                    }} onClick={() => selectSource(option)}>
                      {port.path}<small>{port.unit ?? port.value_kind}</small>
                    </button>;
                  })}
                </div>
              </article>;
            })}
          </div>)}
          <div className="chain-studio-layer">
            <span className="chain-studio-layer-label">DECISION OUTPUT</span>
            {definition.decision_outputs.map((output) => <article className={`chain-studio-node decision-node${selectionKey(selection) === `output:${output.output_id}` ? " selected" : ""}`} key={output.output_id}>
              <button type="button" className="chain-studio-node-title" ref={registerFocusAndEdge(`output:${output.output_id}`, `decision:${output.output_id}`)} onClick={() => setSelection({ kind: "output", id: output.output_id })}>
                <b>{output.label}</b><small>{output.role.replaceAll("_", " ")} · {output.required_for_complete_result ? "required" : "optional"}</small>
              </button>
              <span className="chain-studio-terminal-source">← {output.source_stage_id}.{output.source_output_key}</span>
              {output.evidence?.evidence_kind === "synthetic_demonstration"
                && <span className="sample-source-kind synthetic">synthetic demonstration · production不可</span>}
            </article>)}
          </div>
        </div>
      </div>
    </section>

    <section className="chain-studio-workspace">
      <section className="chain-studio-panel chain-studio-linear" aria-labelledby="linear-editor-heading">
        <div className="chain-studio-section-title"><div><h3 id="linear-editor-heading">Linear editor</h3><p>この一覧だけでnode追加、接続、削除、並べ替え、公開まで完了できます。</p></div></div>

        <fieldset><legend>Input一覧</legend>
          {definition.inputs.length === 0 && <p className="chain-studio-empty">Binding一覧からInputを作成してください。</p>}
          {definition.inputs.map((input) => <div className="chain-studio-linear-row" key={input.input_id}>
            <label>label<input value={input.label} onFocus={() => setSelection({ kind: "input", id: input.input_id })} onChange={(event) => change({ ...definition, inputs: definition.inputs.map((item) => item.input_id === input.input_id ? { ...item, label: event.target.value } : item) })} /></label>
            <label>role<select value={input.role} onFocus={() => setSelection({ kind: "input", id: input.input_id })} onChange={(event) => change(setInputRole(
              definition,
              input.input_id,
              event.target.value as typeof input.role,
              candidatePaths.current.get(input.input_id),
            ))}>
              <option value="design_variable">Design Input</option><option value="scenario_context">Context Input</option>
              {input.port.value_kind !== "sparse_blend" && <option value="fixed_parameter">Fixed parameter</option>}
            </select></label>
            <span>{portLabel(input.port)}</span>
            <button type="button" className="text-button" onClick={() => change(removeInput(definition, input.input_id))}>削除</button>
          </div>)}
        </fieldset>

        <fieldset><legend>Node一覧</legend>
          <div className="chain-studio-linear-add">
            <label>catalog<select value={linearCatalogKey} onChange={(event) => setLinearCatalogKey(event.target.value)}>{availableCatalog.map((item) => <option key={`${item.stage_kind}:${item.contract_id}`} value={`${item.stage_kind}:${item.contract_id}`}>{item.stage_kind === "task" ? "Model" : "Transform"} · {item.label}</option>)}</select></label>
            <button type="button" onClick={() => {
              const item = availableCatalog.find((entry) => `${entry.stage_kind}:${entry.contract_id}` === linearCatalogKey);
              if (!item) return;
              const added = addStage(definition, item);
              change(added.definition);
              setSelection({ kind: "stage", id: added.stageId });
            }}>Nodeを追加</button>
          </div>
          {definition.stages.map((stage, index) => <div className="chain-studio-linear-row node-row" key={stage.stage_id}>
            <button type="button" className="chain-studio-row-select" onClick={() => setSelection({ kind: "stage", id: stage.stage_id })}>{stage.stage_id} · {stageCatalogItem(catalog, stage)?.label}</button>
            <span>{stage.stage_kind === "task" ? "Model / Task" : "Transform"}</span>
            <div><button type="button" disabled={index === 0} onClick={() => change(moveStage(definition, stage.stage_id, -1))}>上へ</button><button type="button" disabled={index === definition.stages.length - 1} onClick={() => change(moveStage(definition, stage.stage_id, 1))}>下へ</button></div>
            <button type="button" className="text-button" onClick={() => change(removeStage(definition, stage.stage_id))}>削除</button>
          </div>)}
        </fieldset>

        <fieldset><legend>Binding一覧</legend>
          {definition.stages.flatMap((stage) => {
            const surface = stageCatalogItem(catalog, stage)?.surface;
            return (surface?.input_ports ?? []).map((port) => {
              const current = definition.bindings.find((binding) => binding.target_stage_id === stage.stage_id && binding.target_input_path === port.path);
              const options = compatibleSources(definition, catalog, stage.stage_id, port);
              return <div className="chain-studio-linear-row binding-row" key={`${stage.stage_id}:${port.path}`}>
                <button type="button" className="chain-studio-row-select" onClick={() => setSelection({ kind: "binding", id: stage.stage_id, port: port.path })}>{stage.stage_id}.{port.path}</button>
                <label><span className="sr-only">{stage.stage_id}.{port.path} source</span><select
                  value={current ? sourceKey(current.source) : ""}
                  onFocus={() => setSelection({ kind: "binding", id: stage.stage_id, port: port.path })}
                  onChange={(event) => {
                    if (!event.target.value) change(removeBinding(definition, stage.stage_id, port.path));
                    else {
                      const next = options.find((option) => option.key === event.target.value);
                      if (next) connect(stage.stage_id, port, next.source);
                    }
                  }}
                ><option value="">未接続</option>{options.map((option) => <option value={option.key} key={option.key}>{option.label}</option>)}</select></label>
                <span>{portLabel(port)}</span>
                <button type="button" onClick={() => change(addInputAndBind(definition, stage.stage_id, port))}>Inputを作成して接続</button>
              </div>;
            });
          })}
        </fieldset>

        <fieldset><legend>Decision Output一覧</legend>
          {definition.decision_outputs.map((output) => <div className="chain-studio-linear-row output-row" key={output.output_id}>
            <label>label<input value={output.label} onFocus={() => setSelection({ kind: "output", id: output.output_id })} onChange={(event) => updateOutput(output.output_id, { label: event.target.value })} /></label>
            <label>role<select value={output.role} onChange={(event) => updateOutput(output.output_id, { role: event.target.value as GraphDecisionOutput["role"] })}><option value="primary_objective">primary</option><option value="hard_constraint">constraint</option><option value="secondary_outcome">secondary</option><option value="diagnostic">diagnostic</option></select></label>
            <label>group<input value={output.group} onChange={(event) => updateOutput(output.output_id, { group: event.target.value })} /></label>
            <label className="chain-studio-check"><input type="checkbox" checked={output.required_for_complete_result} onChange={(event) => updateOutput(output.output_id, { required_for_complete_result: event.target.checked })} />required</label>
            <button type="button" className="text-button" onClick={() => change(removeDecisionOutput(definition, output.output_id))}>削除</button>
          </div>)}
          <div className="chain-studio-output-add">
            {definition.stages.flatMap((stage) => {
              const surface = stageCatalogItem(catalog, stage)?.surface;
              return (surface?.output_ports ?? []).map((port) => {
                const added = definition.decision_outputs.some((output) => output.source_stage_id === stage.stage_id && output.source_output_key === port.path);
                return <button type="button" disabled={added} key={`${stage.stage_id}:${port.path}`} onClick={() => change(addDecisionOutput(definition, stage.stage_id, port))}>{added ? "追加済み" : "Decision Outputへ追加"} · {stage.stage_id}.{port.path}</button>;
              });
            })}
          </div>
        </fieldset>
      </section>

      <aside className="chain-studio-panel chain-studio-inspector" aria-labelledby="graph-inspector-heading">
        <h3 id="graph-inspector-heading">選択中の詳細</h3>
        {!selection && <p>Canvasまたは一覧からnode／portを選択してください。</p>}
        {selectedStage && selectedCatalog && <dl>
          <div><dt>node</dt><dd>{selectedStage.stage_id}</dd></div>
          <div><dt>contract</dt><dd>{selectedStage.contract_id}</dd></div>
          <div><dt>contract digest</dt><dd>{selectedCatalog.surface?.contract_digest}</dd></div>
          <div><dt>Package digest</dt><dd>{selectedCatalog.stage_lock?.package_manifest_digest}</dd></div>
          <div><dt>Dataset View</dt><dd>{selectedCatalog.stage_lock?.dataset_view_revision_id ?? "Transformは対象外"}</dd></div>
          <div><dt>Profile digest</dt><dd>{selectedCatalog.stage_lock?.dataset_profile_digest ?? "Transformは対象外"}</dd></div>
        </dl>}
        {selectedOutput?.evidence && <dl>
          <div><dt>evidence</dt><dd>{selectedOutput.evidence.evidence_kind}</dd></div>
          <div><dt>unit / scale</dt><dd>{selectedOutput.evidence.unit_or_scale}</dd></div>
          <div><dt>goal</dt><dd>{selectedOutput.evidence.goal_direction}</dd></div>
          <div><dt>source variables</dt><dd>{selectedOutput.evidence.source_variables.join(", ")}</dd></div>
          <div><dt>causal claim</dt><dd>{selectedOutput.evidence.causal_claim}</dd></div>
          <div><dt>production use</dt><dd>{selectedOutput.evidence.production_use}</dd></div>
          <div><dt>limitation</dt><dd>{selectedOutput.evidence.limitation}</dd></div>
        </dl>}
        {selection?.kind === "binding" && (() => {
          const stage = definition.stages.find((item) => item.stage_id === selection.id);
          const port = stage
            ? stageCatalogItem(catalog, stage)?.surface?.input_ports.find((item) => item.path === selection.port)
            : undefined;
          const binding = definition.bindings.find((item) => item.target_stage_id === selection.id && item.target_input_path === selection.port);
          return <dl><div><dt>target</dt><dd>{selection.id}.{selection.port}</dd></div><div><dt>source</dt><dd>{binding ? sourceLabel(binding.source) : "未接続"}</dd></div>{port && <><div><dt>quantity / basis</dt><dd>{port.quantity} / {port.basis ?? "—"}</dd></div><div><dt>unit</dt><dd>{port.unit ?? "—"}</dd></div></>}</dl>;
        })()}
      </aside>
    </section>
    </fieldset>

    {validation && <section className={`chain-studio-findings ${validation.valid ? "valid" : "invalid"}`} aria-live="polite">
      <strong>{validation.valid ? `公開可能 · ${validation.candidate_adapter_id}` : `${validation.findings.length}件の修正が必要です`}</strong>
      {!validation.valid && <ul>{validation.findings.map((finding, index) => <li key={`${finding.code}:${index}`}><button type="button" onClick={() => focusFinding(finding)}>{finding.message}</button><span>{finding.suggested_action}</span></li>)}</ul>}
      <details><summary>Definition digest</summary><code>{validation.definition_digest}</code></details>
    </section>}
    {published && <p className="chain-studio-success" role="status">Revision r{published.revision.revision} は公開済みです。Project作成に失敗した場合も再公開せず再試行します。</p>}
    {actionError && <p className="chain-studio-error" role="alert">{actionError}</p>}
    <footer className="chain-studio-actions">
      <button type="button" className="outline-button" disabled={submitting !== null} onClick={() => void validateDraft()}>{submitting === "validate" ? "検証中…" : "Graphを検証"}</button>
      <button type="button" className="primary-button" disabled={submitting !== null} onClick={() => void publishAndCreateProject()}>{submitting === "publish" ? "公開・作成中…" : published ? "Project作成を再試行" : "Revisionを公開してProjectを作成"}</button>
    </footer>
  </section>;
}
