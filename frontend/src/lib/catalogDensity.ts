import { usePersistentChoice } from './usePersistentChoice';

/** The Library's "Book density" view setting. The Library's gear menu writes it;
 * the Global Library and shelves read it, so one choice means the same thing on
 * every book grid (#980). The CSS steps for each value live in the page modules. */
export const CATALOG_DENSITIES = ['comfortable', 'compact', 'dense'] as const;
export type CatalogDensity = (typeof CATALOG_DENSITIES)[number];

export function useCatalogDensity() {
  return usePersistentChoice<CatalogDensity>('cwng:catalog-density-v1', CATALOG_DENSITIES, 'compact');
}
