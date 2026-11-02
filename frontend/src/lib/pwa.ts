// Installable web app: register the service worker that keeps the app shell
// available offline. The server serves the worker at <prefix>/app/sw.js and
// sets window.__CWNG_PWA__ in the shell only when CWNG_PWA is enabled
// (cps/spa.py). When it is disabled the server swaps the worker for one that
// unregisters itself, and browsers pick that up through their normal update
// check, so nothing here needs to unregister.
import { BASE_PREFIX } from './api';

declare global {
  interface Window { __CWNG_PWA__?: boolean; }
}

export function serviceWorkerUrl(prefix: string): { script: string; scope: string } {
  return { script: prefix + '/app/sw.js', scope: prefix + '/app/' };
}

export function registerServiceWorker(): void {
  if (!window.__CWNG_PWA__) return;
  // Service workers exist only in secure contexts (HTTPS or localhost).
  if (!window.isSecureContext || !('serviceWorker' in navigator)) return;
  const { script, scope } = serviceWorkerUrl(BASE_PREFIX);
  const register = () => {
    navigator.serviceWorker.register(script, { scope }).catch((err: unknown) => {
      // Offline support is an enhancement: the app works without it.
      console.warn('CWNG: service worker registration failed', err);
    });
  };
  // Precaching downloads the whole bundle; let the first page render first.
  if (document.readyState === 'complete') register();
  else window.addEventListener('load', register, { once: true });
}
