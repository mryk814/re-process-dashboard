export type WorkspaceNoticeKind = "success" | "error";

export type WorkspaceNotice = Readonly<{
  /** Distinguishes two identical messages so the banner restarts its timer. */
  id: number;
  kind: WorkspaceNoticeKind;
  message: string;
}>;

/**
 * Notices report the result of an operation. A success is a receipt and expires
 * on its own; a failure stays until the user dismisses it, and is announced as
 * an alert. Connection state is not a notice: it belongs to the header badge.
 */
export const SUCCESS_NOTICE_TIMEOUT_MS = 5000;

export function noticeRole(kind: WorkspaceNoticeKind): "status" | "alert" {
  return kind === "error" ? "alert" : "status";
}

export function noticeTimeoutMs(kind: WorkspaceNoticeKind): number | null {
  return kind === "success" ? SUCCESS_NOTICE_TIMEOUT_MS : null;
}
