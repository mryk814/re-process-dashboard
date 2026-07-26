import { useEffect, useRef, useState } from "react";
import type {
  DesktopWorkspaceOperationResult,
  DesktopWorkspaceSummary,
} from "../../shared/api/client";

type Props = {
  open: boolean;
  onClose: () => void;
};

function Summary({ value }: { value: DesktopWorkspaceSummary }) {
  const items = [
    ["プロジェクト", value.projectCount],
    ["候補編集版", value.candidateCount],
    ["予測snapshot", value.snapshotCount],
    ["検討アクティビティ", value.activityCount],
    ["Chain証拠", value.chainCount],
    ["Source lifecycle", value.sourceLifecycleCount],
    ["同梱資産", value.resourceCount],
  ] as const;
  return <div className="workspace-summary">
    <dl>
      {items.map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count}件</dd></div>)}
    </dl>
    <p className="workspace-summary-meta">
      {new Date(value.createdAt).toLocaleString("ja-JP")} · app {value.appVersion}
    </p>
    {value.attentionWarnings.map((warning) => (
      <div className="workspace-summary-attention" key={warning} role="alert">
        <strong>固定参照を確認してください</strong>
        <span>{warning}</span>
      </div>
    ))}
    {value.warnings.length > 0 && (
      <details className="workspace-summary-warnings">
        <summary>{value.warnings.length}件の注意があります</summary>
        <ul>
          {value.warnings.map((warning) => <li key={warning}>{warning}</li>)}
        </ul>
      </details>
    )}
  </div>;
}

export function WorkspaceManagerDialog({ open, onClose }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const [busy, setBusy] = useState<"backup" | "prepare" | "restore" | "cancel">();
  const [result, setResult] = useState<DesktopWorkspaceOperationResult>();
  const [error, setError] = useState("");
  const desktop = window.workbenchDesktop;
  const prepared = result?.status === "prepared" ? result : undefined;

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) void close();
      if (event.key !== "Tab") return;
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])',
      ) ?? [])];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, busy]);

  async function invoke(
    operation: "backup" | "prepare" | "restore",
    action: () => Promise<DesktopWorkspaceOperationResult>,
  ) {
    setBusy(operation);
    setError("");
    try {
      const next = await action();
      if (next.status !== "cancelled") setResult(next);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Workspace処理を完了できませんでした。");
    } finally {
      setBusy(undefined);
    }
  }

  async function close() {
    if (busy) return;
    if (prepared && desktop) {
      setBusy("cancel");
      try {
        await desktop.cancelWorkspaceRestore();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "復元準備を破棄できませんでした。");
        return;
      } finally {
        setBusy(undefined);
      }
    }
    setResult(undefined);
    setError("");
    onClose();
  }

  if (!open) return null;
  return <div className="workspace-dialog-backdrop">
    <section
      ref={dialogRef}
      className="workspace-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="workspace-dialog-title"
      aria-busy={Boolean(busy)}
    >
      <header>
        <div>
          <span className="overline">WORKSPACE</span>
          <h2 id="workspace-dialog-title">ワークスペースの保管と復元</h2>
        </div>
        <button
          ref={closeRef}
          type="button"
          className="icon-button"
          aria-label="閉じる"
          disabled={Boolean(busy)}
          onClick={() => void close()}
        >
          ×
        </button>
      </header>

      {!desktop ? (
        <p className="workspace-desktop-only">バックアップと復元はDesktop版で利用できます。</p>
      ) : prepared ? (
        <div className="workspace-restore-confirm">
          <p><strong>{prepared.fileName}</strong> を検証しました。</p>
          <Summary value={prepared.summary} />
          <p>復元後にAPIを起動できることまで確認してから切り替えます。失敗した場合は現在の内容へ戻します。</p>
          <div className="workspace-dialog-actions">
            <button
              type="button"
              className="outline-button"
              disabled={Boolean(busy)}
              onClick={() => void close()}
            >
              やめる
            </button>
            <button
              type="button"
              className="primary-button"
              disabled={Boolean(busy)}
              onClick={() => void invoke("restore", desktop.confirmWorkspaceRestore)}
            >
              {busy === "restore" ? "検証して切替中…" : "この内容へ復元"}
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className="workspace-dialog-lead">
            Project、候補、固定snapshot、実測、Chain証拠と参照資産をひとつにまとめます。
          </p>
          <div className="workspace-action-grid">
            <article>
              <h3>バックアップを作る</h3>
              <p>動作中の内容から、整合性を保った保管ファイルを作成します。</p>
              <button
                type="button"
                className="primary-button"
                disabled={Boolean(busy)}
                onClick={() => void invoke("backup", desktop.exportWorkspace)}
              >
                {busy === "backup" ? "作成中…" : "保存先を選ぶ"}
              </button>
            </article>
            <article>
              <h3>バックアップから復元</h3>
              <p>内容・版・digestを検証してから、現在のWorkspaceと切り替えます。</p>
              <button
                type="button"
                className="outline-button"
                disabled={Boolean(busy)}
                onClick={() => void invoke("prepare", desktop.prepareWorkspaceRestore)}
              >
                {busy === "prepare" ? "検証中…" : "ファイルを選ぶ"}
              </button>
            </article>
          </div>
          {result?.status === "created" && (
            <div className="workspace-created" role="status">
              <strong>{result.fileName} を作成しました</strong>
              <span>{(result.sizeBytes / 1024 / 1024).toFixed(1)} MB</span>
              <Summary value={result.summary} />
            </div>
          )}
        </>
      )}
      {error && <div className="workspace-dialog-error" role="alert">{error}</div>}
    </section>
  </div>;
}
