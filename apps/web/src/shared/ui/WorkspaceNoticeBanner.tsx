import { useEffect } from "react";
import { noticeRole, noticeTimeoutMs, type WorkspaceNotice } from "../workspaceNotice";

export function WorkspaceNoticeBanner({
  notice,
  onDismiss,
}: {
  notice: WorkspaceNotice;
  onDismiss: () => void;
}) {
  const timeout = noticeTimeoutMs(notice.kind);
  useEffect(() => {
    if (timeout === null) return;
    const timer = window.setTimeout(onDismiss, timeout);
    return () => window.clearTimeout(timer);
  }, [notice.id, timeout, onDismiss]);
  return (
    <div className={`workspace-notice ${notice.kind}`} role={noticeRole(notice.kind)}>
      <span>{notice.message}</span>
      <button type="button" aria-label="通知を閉じる" onClick={onDismiss}>×</button>
    </div>
  );
}
