import { usePersistentChoice } from './usePersistentChoice';

/** The Library's "Rows per load" view setting. The Library's gear menu writes it;
 * shelves read it, so one choice means the same thing on every book grid (#980).
 * Values are strings because they are stored as such; Number() them to use. */
export const CATALOG_ROWS_PER_LOAD = ['1', '2', '3', '4', '5', '6'] as const;
export type CatalogRowsPerLoad = (typeof CATALOG_ROWS_PER_LOAD)[number];

export function useCatalogRowsPerLoad() {
  return usePersistentChoice<CatalogRowsPerLoad>('cwng:catalog-rows-v1', CATALOG_ROWS_PER_LOAD, '2');
}
