/**
 * ローカルAPIはExcelとModel Packageを読むため起動に十数秒かかる。その間の失敗を
 * 「接続できません」と出すと、待てばよいのか復旧が要るのか区別できない。
 * 起動待ちとして自動再試行する時間と、その間隔をここで決める。
 */
export const apiStartupWaitMs = 20_000;

const firstRetryDelayMs = 300;
const maxRetryDelayMs = 3_000;

export function apiStartupRetryDelayMs(attempt: number): number {
  return Math.min(firstRetryDelayMs * 2 ** Math.max(0, attempt - 1), maxRetryDelayMs);
}

/** 接続そのものが確立できない失敗だけを「起動待ち」として扱う。 */
export function isApiUnreachable(error: unknown): boolean {
  const kind = (error as { kind?: string } | null)?.kind;
  if (kind === "network") return true;
  const status = (error as { status?: number } | null)?.status;
  // devプロキシがAPIへ届かないときのゲートウェイ応答。
  return status === 502 || status === 503 || status === 504;
}

export function shouldKeepWaitingForApi(error: unknown, elapsedMs: number): boolean {
  return isApiUnreachable(error) && elapsedMs < apiStartupWaitMs;
}

export function apiStartupWaitText(elapsedMs: number): string {
  const seconds = Math.max(0, Math.floor(elapsedMs / 1000));
  return `ローカルAPIの起動を待っています（経過 ${seconds}秒 / 最大 ${apiStartupWaitMs / 1000}秒）`;
}
