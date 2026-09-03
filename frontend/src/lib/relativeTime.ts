/*
 * Relative "last seen" formatting shared by the device pages. Kept tiny and
 * locale-aware: under 48h it speaks in hours, then switches to days. A missing
 * timestamp renders an em dash — the honest "we do not know" marker.
 */
const API_ISO_INSTANT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})?$/;

/**
 * Parse the ISO shape emitted by CWNG APIs. Older SQLite-backed responses may
 * omit a timezone; those values have always represented UTC, never browser
 * local time. Calendar fields are round-tripped before Date parsing so values
 * such as February 30 cannot silently normalize into March.
 */
export function parseApiTimestamp(value: string): number | null {
  const match = API_ISO_INSTANT.exec(value);
  if (!match) return null;

  const [, yearPart, monthPart, dayPart, hourPart, minutePart, secondPart,
    fractionPart = '', zonePart = ''] = match;
  const year = Number(yearPart);
  const month = Number(monthPart);
  const day = Number(dayPart);
  const hour = Number(hourPart);
  const minute = Number(minutePart);
  const second = Number(secondPart);
  const milliseconds = Number((fractionPart + '000').slice(0, 3));

  if (zonePart && zonePart !== 'Z') {
    const offsetHour = Number(zonePart.slice(1, 3));
    const offsetMinute = Number(zonePart.slice(4, 6));
    if (offsetHour > 23 || offsetMinute > 59) return null;
  }

  const roundTrip = new Date(0);
  roundTrip.setUTCHours(hour, minute, second, milliseconds);
  roundTrip.setUTCFullYear(year, month - 1, day);
  if (roundTrip.getUTCFullYear() !== year
      || roundTrip.getUTCMonth() !== month - 1
      || roundTrip.getUTCDate() !== day
      || roundTrip.getUTCHours() !== hour
      || roundTrip.getUTCMinutes() !== minute
      || roundTrip.getUTCSeconds() !== second) {
    return null;
  }

  const parsed = new Date(zonePart ? value : `${value}Z`).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

export function relativeWhen(value: string | null): string {
  if (!value) return '—';
  const timestamp = parseApiTimestamp(value);
  if (timestamp === null) return '—';
  const elapsed = timestamp - Date.now();
  const formatter = new Intl.RelativeTimeFormat(document.documentElement.lang || undefined, { numeric: 'auto' });
  const hours = Math.round(elapsed / 3_600_000);
  if (Math.abs(hours) < 48) return formatter.format(hours, 'hour');
  return formatter.format(Math.round(hours / 24), 'day');
}
