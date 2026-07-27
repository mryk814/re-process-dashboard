import { useState } from "react";
import type { ApiChangeGuideEntry, ApiDeveloperCommand } from "../../shared/api/workbench-api";

const riskLabel = {
  safe: "比較的安全",
  review: "ガイドとレビューが必要",
  specialist: "専門的レビューが必要",
} as const;

export function CopyCommand({ command }: { command: ApiDeveloperCommand }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(command.display_text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return <div className="developer-command"><code>{command.display_text}</code><small>{command.platform}</small><button type="button" onClick={() => void copy()}>{copied ? "コピー済み" : "コピー"}</button></div>;
}

export function ChangeGuideCard({
  entry,
  onOpenProfileWorkbench,
}: {
  entry: ApiChangeGuideEntry;
  onOpenProfileWorkbench: () => void;
}) {
  return <article className={`developer-guide-card risk-${entry.risk}`}>
    <div className="developer-risk">{riskLabel[entry.risk]}</div>
    <div className="developer-guide-grid">
      <section><h3>主に変更する</h3><ul>{entry.changes.map((item) => <li key={item}>{item}</li>)}</ul></section>
      <section><h3>原則変更しない</h3><ul>{entry.unchanged.map((item) => <li key={item}>{item}</li>)}</ul></section>
      <section><h3>必要な成果物</h3><ul>{entry.artifacts.length ? entry.artifacts.map((item) => <li key={item}>{item}</li>) : <li>分類後に決定</li>}</ul></section>
      <section><h3>関連文書</h3><ul>{entry.documents.map((item) => <li key={item}><code>{item}</code></li>)}</ul></section>
    </div>
    {entry.steps.length > 0 && <section className="developer-guide-workflow" aria-label="実装順序">
      <h3>実装順序</h3>
      <ol>
        {entry.steps.map((step) => <li key={step.label}>
          <strong>{step.label}</strong>
          <span>{step.outcome}</span>
          <div>{step.paths.map((path) => <code key={path}>{path}</code>)}</div>
        </li>)}
      </ol>
    </section>}
    {entry.warnings.length > 0 && <aside className="developer-guide-warnings" aria-label="変更時の注意">
      <h3>変更時の注意</h3>
      <ul>{entry.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
    </aside>}
    {entry.human_review && <p className="developer-review">人の判断: {entry.human_review}</p>}
    {(entry.id === "new-excel" || entry.id === "workbook-shape") && <button type="button" className="primary-button developer-open-profile" onClick={onOpenProfileWorkbench}>Profile WorkbenchでExcelを確認</button>}
    <h3>推奨コマンド</h3>
    {entry.commands.map((command) => <CopyCommand command={command} key={command.display_text} />)}
  </article>;
}
