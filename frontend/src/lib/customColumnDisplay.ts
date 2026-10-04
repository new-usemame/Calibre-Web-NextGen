import type { ListCustomColumnDefinition, Me } from './api';
import { browserLocale } from './locale.ts';

export const GUEST_CUSTOM_FIELDS_KEY = 'cwng:catalog-custom-fields-v1';
export const GUEST_CUSTOM_LABELS_KEY = 'cwng:catalog-custom-field-labels-v1';

export function readGuestCustomFields(): number[] | null {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(GUEST_CUSTOM_FIELDS_KEY) ?? 'null');
    return Array.isArray(parsed) && parsed.every(id => Number.isInteger(id)) ? parsed : null;
  } catch { return null; }
}

export function readGuestCustomLabels(): Record<string, string> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(GUEST_CUSTOM_LABELS_KEY) ?? '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter(([id, label]) =>
      /^[0-9]+$/.test(id) && typeof label === 'string' && label.length <= 80));
  } catch { return {}; }
}

/** Prune removed fields before saving without weakening endpoint validation. */
export function customFieldsForSave(
  definitions: ListCustomColumnDefinition[], ids: number[], labels: Record<string, string>,
) {
  const allowed = new Set(definitions.map(field => field.id));
  return {
    custom_column_ids: ids.filter(id => allowed.has(id)),
    custom_column_labels: Object.fromEntries(Object.entries(labels).filter(([id]) => allowed.has(Number(id)))),
  };
}

/** Apply the reader's View-settings selection to a page's server-owned fields.
 * A missing selection is the first-run default: show every enabled field. */
export function selectedCustomColumns(
  definitions: ListCustomColumnDefinition[] | undefined,
  me: Me | null | undefined,
): ListCustomColumnDefinition[] {
  const fields = definitions ?? [];
  const guest = me === null || me?.role?.anonymous === true;
  const selected = guest ? readGuestCustomFields() : me?.catalog?.custom_field_ids;
  const labels = guest ? readGuestCustomLabels() : me?.catalog?.custom_field_labels ?? {};
  const visible = Array.isArray(selected)
    ? fields.filter((field) => selected.includes(field.id))
    : fields;
  return visible.map((field) => ({
    ...field,
    name: labels[String(field.id)]?.trim() || field.name,
  }));
}

/** Custom Calibre dates are calendar values, not browser-local instants. */
export function formatCustomColumnDate(value: string, locale?: string, options?: Intl.DateTimeFormatOptions): string {
  const calendar = /^(\d{4})-(\d{2})-(\d{2})(?:$|T| )/.exec(value);
  if (!calendar) return value;
  const [year, month, day] = calendar.slice(1).map(Number);
  if (year <= 101) return '';
  const parsed = new Date(0);
  parsed.setUTCFullYear(year, month - 1, day);
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return value;
  return parsed.toLocaleDateString(browserLocale(locale), { ...options, timeZone: 'UTC' });
}
