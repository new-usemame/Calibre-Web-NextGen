// Drives a support pack download and exposes its status to a component
// (fork issue #2728). The protocol itself, and why it works the way it does, is
// in ./packDownload.ts.

import { useCallback, useEffect, useRef, useState } from 'react';

import { browserEnv, DONE_MESSAGE_MS, startPackDownload } from './packDownload';

export type PackStatus = 'idle' | 'preparing' | 'started' | 'busy' | 'failed' | 'timeout';

export function usePackDownload(url: string) {
  const [status, setStatus] = useState<PackStatus>('idle');
  const running = useRef<{ cancel(): void } | null>(null);
  const clearTimer = useRef<number | undefined>(undefined);

  // Leaving the page stops the watching; the browser finishes any download it began.
  useEffect(() => () => {
    running.current?.cancel();
    window.clearTimeout(clearTimer.current);
  }, []);

  const start = useCallback(() => {
    if (running.current) return; // one at a time: a second click does nothing
    window.clearTimeout(clearTimer.current);
    setStatus('preparing');
    running.current = startPackDownload(browserEnv(document, window), url, {
      onDone: () => {
        running.current = null;
        setStatus('started');
        clearTimer.current = window.setTimeout(() => setStatus('idle'), DONE_MESSAGE_MS);
      },
      onError: (kind) => {
        running.current = null;
        setStatus(kind);
      },
    });
  }, [url]);

  return { status, start, preparing: status === 'preparing' };
}
