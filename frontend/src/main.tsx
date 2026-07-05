import './styles/tokens.css';
import './styles/global.css';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider, QueryCache, MutationCache } from '@tanstack/react-query';
import { App } from './App';
import { AnnouncerProvider } from './lib/a11y/announcer';
import { ApiError } from './lib/api';

// On any 401 (expired/invalid session), drop the cached `me` to null. App.tsx
// gates on `me`, so this routes the user straight back to the login screen
// instead of leaving stale data on screen behind a dead session.
function onUnauthorized(err: unknown) {
  if (err instanceof ApiError && err.status === 401) {
    queryClient.setQueryData(['me'], null);
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onUnauthorized }),
  mutationCache: new MutationCache({ onError: onUnauthorized }),
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AnnouncerProvider>
        <App />
      </AnnouncerProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
