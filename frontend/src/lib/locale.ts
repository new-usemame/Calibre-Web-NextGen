/** Convert stored Babel locale tags to valid browser language tags. */
export function browserLocale(locale?: string): string | undefined {
  if (!locale) return undefined;
  try { return Intl.getCanonicalLocales(locale.replace(/_/g, '-'))[0]; }
  catch { return undefined; }
}
