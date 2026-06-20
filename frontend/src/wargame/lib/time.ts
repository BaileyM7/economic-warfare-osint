/**
 * Relative timestamp formatter — mono-spaced friendly output.
 * Produces short strings suitable for Geist Mono display.
 */

/**
 * Returns a relative time string like "3s ago", "2m ago", "1h ago".
 * For very recent events (< 5 s), returns "just now".
 */
export function relativeTime(isoTimestamp: string): string {
  const now = Date.now();
  const then = new Date(isoTimestamp).getTime();
  const diffMs = now - then;
  const diffS = Math.floor(diffMs / 1000);

  if (diffS < 5) return 'just now';
  if (diffS < 60) return `${diffS}s ago`;
  const diffM = Math.floor(diffS / 60);
  if (diffM < 60) return `${diffM}m ago`;
  const diffH = Math.floor(diffM / 60);
  if (diffH < 24) return `${diffH}h ago`;
  const diffD = Math.floor(diffH / 24);
  return `${diffD}d ago`;
}

/**
 * Formats an ISO timestamp as a compact mono string: "2027-03-14 09:42Z"
 */
export function formatTimestamp(isoTimestamp: string): string {
  try {
    const d = new Date(isoTimestamp);
    const yy = d.getUTCFullYear();
    const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(d.getUTCDate()).padStart(2, '0');
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    return `${yy}-${mo}-${dd} ${hh}:${mm}Z`;
  } catch {
    return isoTimestamp;
  }
}

/**
 * Formats an ISO timestamp as a short time string: "09:42:11Z"
 */
export function formatShortTime(isoTimestamp: string): string {
  try {
    const d = new Date(isoTimestamp);
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    const ss = String(d.getUTCSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss}Z`;
  } catch {
    return isoTimestamp;
  }
}
