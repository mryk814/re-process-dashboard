import { useEffect, useMemo, useState } from "react";

import type { ApiChainStudioCatalog, ApiChainStudioDraft } from "../../shared/api/workbench-api";
import { workbenchApi } from "../../shared/api/workbench-api";

type CatalogStage = ApiChainStudioCatalog["stages"][number];
type SurfacePort = NonNullable<CatalogStage["surface"]>["input_ports"][number];
type SelectedStage = { id: string; contractId: string };
type SourceChoice = { key: string; label: string; source: ApiChainStudioDraft["definition"]["bindings"][number]["source"] };

function samePort(left: SurfacePort, right: SurfacePort) {
  return left.value_kind === right.value_kind
    && left.quantity === right.quantity
    && left.unit === right.unit
    && left.basis === right.basis;
}

function stageFor(catalog: CatalogStage[], contractId: string) {
  return catalog.find((item) => item.contract_id === contractId && item.status === "available" && item.surface);
}

function externalPath(port: SurfacePort) {
  return `candidate.${port.path}`;
}

function sourceChoices(
  stages: SelectedStage[],
  catalog: CatalogStage[],
  targetIndex: number,
  port: SurfacePort,
): SourceChoice[] {
  const direct: SourceChoice = {
    key: `external:${externalPath(port)}`,
    label: `外部入力 · ${externalPath(port)}`,
    source: { source_kind: "external", path: externalPath(port) },
  };
  return [direct, ...stages.slice(0, targetIndex).flatMap((stage) => {
    const surface = stageFor(catalog, stage.contractId)?.surface;
    return (surface?.output_ports ?? []).filter((output) => samePort(output, port)).map((output) => ({
      key: `stage:${stage.id}:${output.path}`,
      label: `${stage.id}.${output.path}`,
      source: { source_kind: "stage_output" as const, stage_id: stage.id, output_key: output.path },
    }));
  })];
}

function buildDraft(
  chainId: string,
  label: string,
  stages: SelectedStage[],
  catalog: CatalogStage[],
  selectedSources: Record<string, string>,
): ApiChainStudioDraft | null {
  if (stages.length < 2 || stages.some((stage) => !stageFor(catalog, stage.contractId)?.surface)) return null;
  const external = new Map<string, SurfacePort>();
  const bindings: ApiChainStudioDraft["definition"]["bindings"] = [];
  for (const [index, stage] of stages.entries()) {
    const surface = stageFor(catalog, stage.contractId)!.surface!;
    for (const target of surface.input_ports) {
      const key = `${stage.id}:${target.path}`;
      const options = sourceChoices(stages, catalog, index, target);
      const chosen = options.find((item) => item.key === selectedSources[key]) ?? options[0];
      if (chosen.source.source_kind === "external") external.set(chosen.source.path, { ...target, path: chosen.source.path });
      bindings.push({ target_stage_id: stage.id, target_input_path: target.path, source: chosen.source });
    }
  }
  return {
    definition: {
      schema_version: "chain-definition/v1",
      chain_id: chainId.trim() || "scalar-chain-draft",
      label: label.trim() || "名称未設定のscalar Chain",
      stages: stages.map((stage) => ({ stage_id: stage.id, stage_kind: "task", contract_id: stage.contractId })),
      external_inputs: [...external.values()],
      bindings,
    },
  };
}

function newStageId(stages: SelectedStage[]) {
  let index = stages.length + 1;
  while (stages.some((stage) => stage.id === `S${index}`)) index += 1;
  return `S${index}`;
}

export function ChainStudioPage() {
  const [catalog, setCatalog] = useState<CatalogStage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [chainId, setChainId] = useState("scalar-chain-draft");
  const [label, setLabel] = useState("新しい scalar Chain");
  const [stages, setStages] = useState<SelectedStage[]>([]);
  const [selectedSources, setSelectedSources] = useState<Record<string, string>>({});
  const [validation, setValidation] = useState<string>();
  const [published, setPublished] = useState<string>();
  const [submitting, setSubmitting] = useState<"validate" | "publish" | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void workbenchApi.chainStudioCatalog(controller.signal).then((response) => {
      if (controller.signal.aborted) return;
      const available = response.stages.filter((item) => item.status === "available" && item.surface);
      setCatalog(response.stages);
      setStages((current) => current.length ? current : available.slice(0, 1).map((item, index) => ({ id: `S${index + 1}`, contractId: item.contract_id })).concat(
        available[0] ? [{ id: "S2", contractId: available[0].contract_id }] : [],
      ));
      setLoading(false);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "Task catalogを取得できませんでした。"); setLoading(false); }
    });
    return () => controller.abort();
  }, []);

  const available = useMemo(() => catalog.filter((item) => item.status === "available" && item.surface), [catalog]);
  const draft = useMemo(() => buildDraft(chainId, label, stages, catalog, selectedSources), [chainId, label, stages, catalog, selectedSources]);

  function replaceStage(index: number, contractId: string) {
    setStages((current) => current.map((stage, itemIndex) => itemIndex === index ? { ...stage, contractId } : stage));
    setSelectedSources({});
    setValidation(undefined);
    setPublished(undefined);
  }

  function cycleGraphBinding(stage: SelectedStage, index: number, target: SurfacePort) {
    const key = `${stage.id}:${target.path}`;
    const options = sourceChoices(stages, catalog, index, target);
    const current = selectedSources[key] ?? options[0].key;
    const next = options[(options.findIndex((item) => item.key === current) + 1) % options.length];
    setSelectedSources((sources) => ({ ...sources, [key]: next.key }));
    setValidation(undefined);
    setPublished(undefined);
  }

  async function submit(kind: "validate" | "publish") {
    if (!draft) return;
    setSubmitting(kind);
    setError(undefined);
    setPublished(undefined);
    try {
      if (kind === "validate") {
        const result = await workbenchApi.validateChainStudioDraft(draft);
        setValidation(`${result.message} Definition: ${result.definition_digest.slice(0, 15)}…`);
      } else {
        const result = await workbenchApi.publishChainStudioDraft(draft);
        const revision = result.revisions[0];
        setPublished(`${result.definition.label} を r${revision.revision} として公開しました。Revision digest: ${revision.revision_digest.slice(0, 15)}…`);
        setValidation(undefined);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Chainを処理できませんでした。");
    } finally {
      setSubmitting(null);
    }
  }

  if (loading) return <section className="chain-studio-state" aria-live="polite"><span className="overline">CHAIN STUDIO · SCALAR/V1</span><h2>Task catalogを読み込み中です</h2></section>;
  if (error && !catalog.length) return <section className="chain-studio-state" role="alert"><span className="overline">CHAIN STUDIO · SCALAR/V1</span><h2>Chain Studioを開始できません</h2><p>{error}</p></section>;

  return <section className="chain-studio" aria-labelledby="chain-studio-heading">
    <header className="chain-studio-header">
      <div><span className="overline">CHAIN STUDIO · SCALAR/V1</span><h2 id="chain-studio-heading">予測Taskを固定したChainとして公開する</h2><p>ここではTask、接続、外部入力を決めます。Package／Datasetの版はサーバが現在の利用可能な参照から固定し、配置情報は保存しません。</p></div>
      <div className="chain-studio-scope"><strong>今回扱わないもの</strong><span>任意code、Transform、疎配合、未登録の単位変換</span></div>
    </header>

    <section className="chain-studio-panel" aria-labelledby="chain-studio-identity"><h3 id="chain-studio-identity">draftの名前</h3><div className="chain-studio-fields"><label>Chain ID<input value={chainId} onChange={(event) => { setChainId(event.target.value); setValidation(undefined); setPublished(undefined); }} /></label><label>表示名<input value={label} onChange={(event) => { setLabel(event.target.value); setValidation(undefined); setPublished(undefined); }} /></label></div></section>

    <section className="chain-studio-panel" aria-labelledby="chain-studio-stages"><div className="chain-studio-section-title"><div><h3 id="chain-studio-stages">Stage順序</h3><p>同じTaskを複数回使えます。後段は前段の互換portだけを入力として選べます。</p></div><button type="button" className="outline-button" disabled={stages.length >= 4 || !available.length} onClick={() => { setStages((current) => [...current, { id: newStageId(current), contractId: available[0].contract_id }]); setSelectedSources({}); }}>Stageを追加</button></div><ol className="chain-studio-stages">{stages.map((stage, index) => <li key={stage.id}><span>{stage.id}</span><label><span className="sr-only">{stage.id}のTask</span><select value={stage.contractId} onChange={(event) => replaceStage(index, event.target.value)}>{available.map((item) => <option key={item.contract_id} value={item.contract_id}>{item.label}（{item.contract_id}）</option>)}</select></label><button type="button" className="text-button" disabled={stages.length <= 2} onClick={() => { setStages((current) => current.filter((_, itemIndex) => itemIndex !== index)); setSelectedSources({}); }}>外す</button></li>)}</ol></section>

    <section className="chain-studio-panel" aria-labelledby="chain-studio-map"><div className="chain-studio-section-title"><div><h3 id="chain-studio-map">binding map</h3><p>railを押すと、そのtargetに接続できるsourceを順に切り替えます。下の表と同じdraftを編集します。</p></div></div><div className="chain-studio-authoring-map" aria-label="graph操作でbindingを編集">{stages.map((stage, index) => <article key={stage.id}><header><b>{stage.id}</b><span>{stageFor(catalog, stage.contractId)?.label}</span></header><div>{(stageFor(catalog, stage.contractId)?.surface?.input_ports ?? []).map((target) => {
      const key = `${stage.id}:${target.path}`;
      const options = sourceChoices(stages, catalog, index, target);
      const current = options.find((item) => item.key === selectedSources[key]) ?? options[0];
      return <button type="button" key={key} className="chain-studio-binding-rail" onClick={() => cycleGraphBinding(stage, index, target)}><span>{current.label}</span><i aria-hidden="true">→</i><b>{stage.id}.{target.path}</b><small>{options.length > 1 ? `${options.length}候補 · 押して切替` : "外部入力のみ"}</small></button>;
    })}</div></article>)}</div></section>

    <section className="chain-studio-panel" aria-labelledby="chain-studio-bindings"><div className="chain-studio-section-title"><div><h3 id="chain-studio-bindings">binding</h3><p>外部入力か、前段の完全に互換なoutputを明示します。basisが違うものは選べません。</p></div></div><div className="chain-studio-table-wrap"><table><thead><tr><th scope="col">target</th><th scope="col">port</th><th scope="col">source</th><th scope="col">型／quantity／unit／basis</th></tr></thead><tbody>{stages.flatMap((stage, index) => {
      const surface = stageFor(catalog, stage.contractId)?.surface;
      return (surface?.input_ports ?? []).map((target) => {
        const key = `${stage.id}:${target.path}`;
        const options = sourceChoices(stages, catalog, index, target);
        const selected = selectedSources[key] ?? options[0].key;
        return <tr key={key}><th scope="row">{stage.id}</th><td>{target.path}</td><td><select aria-label={`${stage.id} ${target.path} のsource`} value={selected} onChange={(event) => { setSelectedSources((current) => ({ ...current, [key]: event.target.value })); setValidation(undefined); setPublished(undefined); }}>{options.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}</select></td><td>{target.value_kind} · {target.quantity} · {target.unit ?? "—"} · {target.basis ?? "—"}</td></tr>;
      });
    })}</tbody></table></div></section>

    <section className="chain-studio-summary" aria-live="polite"><div><strong>公開前の固定参照</strong><span>選んだTaskのcontract、Package manifest、Dataset View／Profile digestをサーバで解決します。</span></div><div><strong>外部入力</strong><span>{draft?.definition.external_inputs.length ?? 0} 件。candidateの既定namespace以外は拒否します。</span></div></section>
    {error && <p className="chain-studio-error" role="alert">{error}</p>}
    {validation && <p className="chain-studio-success" role="status">{validation}</p>}
    {published && <p className="chain-studio-success" role="status">{published}</p>}
    <footer className="chain-studio-actions"><button type="button" className="outline-button" disabled={!draft || submitting !== null} onClick={() => void submit("validate")}>{submitting === "validate" ? "検証中…" : "draftを検証"}</button><button type="button" className="primary-button" disabled={!draft || submitting !== null} onClick={() => void submit("publish")}>{submitting === "publish" ? "公開中…" : "immutable Revisionを公開"}</button></footer>
  </section>;
}
