import { useState } from "react";
import {
  getCandidateInputValue,
  formatInputNumber,
  orderedInputGroups,
  type DisplayDecimalOverrides,
  type CandidateViewModel as Candidate,
  type TaskDefinitionContract,
} from "../candidates";

type HeatField = "time" | "temperature" | "stageName";

export function ScreeningBaseEditor({
  candidate,
  taskDefinition,
  onInput,
  onHeat,
  displayDecimalOverrides,
}: {
  candidate: Candidate;
  taskDefinition: TaskDefinitionContract;
  onInput: (path: string, value: number | string) => void;
  onHeat: (index: number, field: HeatField, value: number | string) => void;
  displayDecimalOverrides?: DisplayDecimalOverrides;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const groups = orderedInputGroups(taskDefinition).filter((group) => group.key !== "heat_pattern");
  const setDraft = (key: string, value: string) => setDrafts((current) => ({ ...current, [key]: value }));
  const clearDraft = (key: string) => setDrafts((current) => {
    const { [key]: _, ...rest } = current;
    return rest;
  });

  return (
    <details className="screening-base-editor">
      <summary className="screening-base-editor-heading">
        <div>
          <h3>基準条件</h3>
          <small>{candidate.label} · {groups.length}入力グループ{candidate.heat.length > 0 ? ` · ヒートパターン${candidate.heat.length}点` : ""}</small>
        </div>
        <span>確認・調整</span>
      </summary>
      <div className="screening-base-groups">
        <p className="screening-base-note">{candidate.label}を元にした探索用の固定値です。ここでの変更は候補自体へ反映しません。</p>
        {groups.map((group) => (
          <section className="screening-base-group" key={group.key}>
            <h4>{group.label}</h4>
            <div className="screening-base-group-scroll">
              <table className="screening-base-input-table">
                <thead>
                  <tr>{group.fields.map((field) => <th key={field.path}>{field.label}{field.unit && <small>{field.unit}</small>}</th>)}</tr>
                </thead>
                <tbody>
                  <tr>{group.fields.map((field) => {
                    const value = getCandidateInputValue(candidate.raw.inputs, field.path);
                    const key = `${candidate.id}:${field.path}`;
                    return <td key={field.path}>
                      {field.kind === "categorical" ? (
                        <select
                          aria-label={`${field.label}の基準値`}
                          disabled={!field.editable}
                          value={String(value ?? "")}
                          onChange={(event) => onInput(field.path, event.target.value)}
                        >
                          {field.choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
                        </select>
                      ) : (
                        <input
                          type="number"
                          step="any"
                          aria-label={`${field.label}の基準値`}
                          disabled={!field.editable}
                          value={drafts[key] ?? (typeof value === "number" ? formatInputNumber(value, taskDefinition, field.path, displayDecimalOverrides) : "")}
                          onFocus={() => setDraft(key, typeof value === "number" ? String(value) : "")}
                          onChange={(event) => setDraft(key, event.target.value)}
                          onBlur={(event) => {
                            clearDraft(key);
                            const next = Number(event.target.value);
                            if (Number.isFinite(next) && next !== value) onInput(field.path, next);
                          }}
                        />
                      )}
                    </td>;
                  })}</tr>
                </tbody>
              </table>
            </div>
          </section>
        ))}
        {candidate.heat.length > 0 && (
          <section className="screening-base-group screening-base-heat">
            <h4>ヒートパターン <small>{candidate.heat.length}点</small></h4>
            <div className="screening-heat-scroll">
              <table className="screening-heat-horizontal">
                <thead><tr><th>項目</th>{candidate.heat.map((_, index) => <th key={`heat-head:${index}`}>点{index + 1}</th>)}</tr></thead>
                <tbody>
                  <tr><th>工程名</th>{candidate.heat.map((point, index) => {
                    const key = `${candidate.id}:heat:${index}:stageName`;
                    return <td key={key}><input aria-label={`点${index + 1}の工程名`} value={drafts[key] ?? point.stageName ?? ""} onChange={(event) => setDraft(key, event.target.value)} onBlur={(event) => { clearDraft(key); if (event.target.value !== (point.stageName ?? "")) onHeat(index, "stageName", event.target.value); }} /></td>;
                  })}</tr>
                  <tr><th>時間 <small>min</small></th>{candidate.heat.map((point, index) => {
                    const key = `${candidate.id}:heat:${index}:time`;
                    return <td key={key}><input type="number" step="any" aria-label={`点${index + 1}の時間`} value={drafts[key] ?? String(point.time)} onChange={(event) => setDraft(key, event.target.value)} onBlur={(event) => { clearDraft(key); const next = Number(event.target.value); if (Number.isFinite(next) && next !== point.time) onHeat(index, "time", next); }} /></td>;
                  })}</tr>
                  <tr><th>温度 <small>°C</small></th>{candidate.heat.map((point, index) => {
                    const key = `${candidate.id}:heat:${index}:temperature`;
                    return <td key={key}><input type="number" step="any" aria-label={`点${index + 1}の温度`} value={drafts[key] ?? String(point.temperature)} onChange={(event) => setDraft(key, event.target.value)} onBlur={(event) => { clearDraft(key); const next = Number(event.target.value); if (Number.isFinite(next) && next !== point.temperature) onHeat(index, "temperature", next); }} /></td>;
                  })}</tr>
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </details>
  );
}
