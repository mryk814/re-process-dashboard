import { supportStatusLabel, supportStatusTone } from "../supportPresentation";

/**
 * Support state is shown with a word plus a shape, never with colour alone.
 * `message` keeps the server explanation available without occupying the surface.
 */
export function SupportBadge({
  status,
  message,
  unknownLabel,
}: {
  status: string | null | undefined;
  message?: string | null;
  unknownLabel?: string;
}) {
  const label = supportStatusLabel(status, unknownLabel);
  return (
    <span className={`support-badge-inline ${supportStatusTone(status)}`} title={message ?? undefined}>
      <i aria-hidden="true" />
      <span>適用範囲 {label}</span>
    </span>
  );
}
