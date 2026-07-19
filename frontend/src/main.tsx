import './styles/tokens.css';
import './styles/global.css';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider, QueryCache, MutationCache } from '@tanstack/react-query';
import { App } from './App';
import { AnnouncerProvider } from './lib/a11y/announcer';
import { ErrorBoundary } from './components/ErrorBoundary';
import { AuthTransitionError, navigateToLogout, BASE_PREFIX } from './lib/api';

// Protected wrappers normalize every auth-loss shape and start the canonical
// top-level logout navigation. Keep the cache transition here so no stale
// authenticated data remains visible while that navigation is pending.
function onUnauthorized(err: unknown) {
  if (err instanceof AuthTransitionError) {
    queryClient.setQueryData(['me'], null);
    navigateToLogout();
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onUnauthorized }),
  mutationCache: new MutationCache({ onError: onUnauthorized }),
});

// #855: last-resort render/lifecycle boundary for failures in the providers or
// in App before the router-level boundary (App.tsx, which also resets on
// navigation) mounts. React boundaries do not catch event-handler, async, or
// bootstrap/module-load errors — this covers the render-time class that unmounts
// the tree and empties #root.
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary homeHref={BASE_PREFIX + '/app'}>
      <QueryClientProvider client={queryClient}>
        <AnnouncerProvider>
          <App />
        </AnnouncerProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
