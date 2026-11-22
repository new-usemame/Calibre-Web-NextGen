import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiGet } from '../lib/api';
import type { BooksPage } from '../lib/api';
import { refreshLibraryViews } from '../lib/queries';
import { getImportWatch, recordLibraryTotal, useImportWatch } from '../lib/uploadImportStore';
import { MAX_WATCH_MS, STALL_MS, pollIntervalMs } from '../lib/uploadImportWatch';

/** Follows uploaded files into the library (#1044) and refreshes the library
 *  views as they arrive, so a book shows up without a manual reload. Renders
 *  nothing; mounted once in the app shell so it outlives the Upload page.
 *
 *  Each probe asks for a single book, only to read the library's total, so
 *  following a very large batch costs almost nothing. The tab being hidden
 *  pauses the probes (react-query's default), and coming back to it reads the
 *  total straight away. */
export function UploadImportWatcher() {
  const qc = useQueryClient();
  const watch = useImportWatch();
  const watching = watch?.status === 'watching';

  const probe = useQuery<BooksPage>({
    queryKey: ['upload-import-probe'],
    queryFn: ({ signal }) => apiGet<BooksPage>('/api/v1/books?page=1&per_page=1&sort=new', { signal }),
    enabled: watching,
    refetchInterval: () => {
      const current = getImportWatch();
      return current && current.status === 'watching' ? pollIntervalMs(current, Date.now()) : false;
    },
    staleTime: 0,
    gcTime: 0,
    // The application's default is false; this probe must resume immediately.
    refetchOnWindowFocus: true,
  });

  // dataUpdatedAt, not data: an unchanged total is still a fresh reading.
  useEffect(() => {
    if (!probe.data) return;
    if (recordLibraryTotal(probe.data.total)) void refreshLibraryViews(qc);
  }, [probe.dataUpdatedAt]);

  // Disabling the query stops its interval but not a retry it already
  // scheduled: react-query's retryer runs a failed probe again (three times by
  // default) even after the watch ended. Cancel it, so a watch that stalled on
  // a failing server makes no request after its deadline.
  useEffect(() => {
    if (!watching) void qc.cancelQueries({ queryKey: ['upload-import-probe'] });
  }, [watching, qc]);

  // A deadline must not depend on a successful HTTP response. Otherwise a
  // disconnected server leaves the watcher (and its polling) alive forever.
  useEffect(() => {
    if (!watching || !watch) return;
    const deadline = Math.min(watch.lastGrowthAt + STALL_MS, watch.startedAt + MAX_WATCH_MS);
    const timeout = window.setTimeout(() => {
      if (recordLibraryTotal(null)) void refreshLibraryViews(qc);
    }, Math.max(0, deadline - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [watching, watch?.lastGrowthAt, watch?.startedAt, qc]);

  return null;
}
