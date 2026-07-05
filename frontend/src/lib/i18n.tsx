/* Client i18n bridge.
 *
 * The SPA can't use server gettext, so it loads a per-locale catalog
 * ({ msgid: translation }) from GET /api/v1/i18n/<locale>.json — derived from
 * the same .po files the server compiles (single source of truth). The English
 * source strings ARE the keys, so:
 *   - a string already translated for the legacy UI translates here for free,
 *   - a string not yet in the catalog falls back to its English source key.
 * No missing-key placeholders, ever. See notes/FRONTEND-REBUILD-DESIGN.md §10.
 */
import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiGet } from './api';

type Catalog = Record<string, string>;

export type TFunction = (key: string, vars?: Record<string, string | number>) => string;

interface I18nValue {
  t: TFunction;
  locale: string;
  /** False only while a non-English catalog is still loading. */
  ready: boolean;
}

// Default value = identity translator, so a component used outside the provider
// (or before the catalog loads) renders its English source strings rather than
// crashing or showing keys.
const I18nContext = createContext<I18nValue>({
  t: (key) => key,
  locale: 'en',
  ready: true,
});

/** Replace {name} placeholders from vars; leaves unknown placeholders intact. */
function interpolate(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match,
  );
}

export function I18nProvider({ locale, children }: { locale: string; children: ReactNode }) {
  // English is the source locale — its catalog is empty by construction, so skip
  // the network round-trip entirely.
  const enabled = !!locale && locale !== 'en';

  // SC 3.1.1 Language of Page — keep <html lang> in sync with the user's locale
  // so screen readers use the right pronunciation rules. Normalize e.g. "de_DE"
  // (server form) to the BCP-47 "de-DE" the lang attribute expects.
  useEffect(() => {
    document.documentElement.lang = (locale || 'en').replace('_', '-');
  }, [locale]);

  const { data, isSuccess } = useQuery({
    queryKey: ['i18n', locale],
    queryFn: () =>
      apiGet<{ locale: string; catalog: Catalog }>(
        `/api/v1/i18n/${encodeURIComponent(locale)}.json`,
      ),
    enabled,
    staleTime: Infinity, // catalogs are immutable for the life of the image
    gcTime: Infinity,
  });

  const value = useMemo<I18nValue>(() => {
    const catalog = data?.catalog ?? {};
    return {
      locale: locale || 'en',
      ready: !enabled || isSuccess,
      t: (key, vars) => interpolate(catalog[key] ?? key, vars),
    };
  }, [data, locale, enabled, isSuccess]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/** The translate function. `t('Books')` -> 'Bücher' (de) or 'Books' (fallback). */
export function useT(): TFunction {
  return useContext(I18nContext).t;
}

export function useI18n(): I18nValue {
  return useContext(I18nContext);
}
