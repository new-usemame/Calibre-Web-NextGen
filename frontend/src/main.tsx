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

// #855: the outermost net. The router-level boundary in App.tsx handles page
// crashes (and resets on navigation); this one catches anything thrown above it
// — the providers or the App shell itself — so no code path can leave the user
// staring at an empty #root with no way to recover.
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
