import type { AcquisitionCatalog, AcquisitionConnection } from './acquisition';
export const SEARCH_BATCH_SIZE = 4;
export type CatalogSearchResult = {
  connection: AcquisitionConnection;
  state: 'ready' | 'browse-only' | 'failed';
  catalog?: AcquisitionCatalog;
};
export type CatalogReader = (connection: string, options?: {
  selection?: string; query?: string; signal?: AbortSignal;
}) => Promise<AcquisitionCatalog>;
export async function searchCatalogBatch(
  connections: AcquisitionConnection[], query: string, read: CatalogReader,
  signal: AbortSignal, finished: (result: CatalogSearchResult) => void,
): Promise<void> {
  if (!query.trim() || query.length > 500 || connections.length > SEARCH_BATCH_SIZE
      || new Set(connections.map((c) => c.id)).size !== connections.length) {
    throw new Error('Invalid catalog search batch');
  }
  await Promise.all(connections.map(async (connection) => {
    if (signal.aborted) return;
    let result: CatalogSearchResult;
    try {
      const root = await read(connection.id, { signal });
      if (signal.aborted) return;
      const selection = root.searches[0]?.selection;
      result = selection
        ? { connection, state: 'ready', catalog: await read(connection.id, {
            selection, query: query.trim(), signal,
          }) }
        : { connection, state: 'browse-only' };
    } catch {
      // Dependency errors may contain credentials. No upstream exception text
      // enters component state, logs or an announcement.
      result = { connection, state: 'failed' };
    }
    if (!signal.aborted) finished(result);
  }));
}
