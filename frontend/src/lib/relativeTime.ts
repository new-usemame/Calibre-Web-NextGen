/*
 * Relative "last seen" formatting shared by the device pages. Kept tiny and
 * locale-aware: under 48h it speaks in hours, then switches to days. A missing
 * timestamp renders an em dash — the honest "we do not know" marker.
 */
export function relativeWhen(value: string | null): string {
  if (!value) return '—';
  const elapsed = new Date(value).getTime() - Date.now();
  const formatter = new Intl.RelativeTimeFormat(document.documentElement.lang || undefined, { numeric: 'auto' });
  const hours = Math.round(elapsed / 3_600_000);
  if (Math.abs(hours) < 48) return formatter.format(hours, 'hour');
  return formatter.format(Math.round(hours / 24), 'day');
}
