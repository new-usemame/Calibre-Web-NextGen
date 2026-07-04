import { lazy, Suspense, useEffect } from 'react';
import { Router, Route, Switch } from 'wouter';
import { BASE_PREFIX } from './lib/api';
import { useMe, useLogout } from './lib/queries';
import { Login } from './pages/Login';
import { MagicLink } from './pages/MagicLink';
import { Catalog } from './pages/Catalog';
import { BookDetail } from './pages/BookDetail';
import { BrowseList } from './pages/BrowseList';
import { NotFound } from './pages/NotFound';
import { Shelves } from './pages/Shelves';
import { Shelf } from './pages/Shelf';
import { AdvancedSearch } from './pages/AdvancedSearch';
import { Account } from './pages/Account';
import { EditBook } from './pages/EditBook';
import { CoverPicker } from './pages/CoverPicker';
import { Upload } from './pages/Upload';
import { Admin } from './pages/Admin';
import { About } from './pages/About';
import { Tasks } from './pages/Tasks';
import { Table } from './pages/Table';
import { Duplicates } from './pages/Duplicates';
import { Annotations } from './pages/Annotations';
import { WhatsNew } from './pages/WhatsNew';
import { MagicShelf } from './pages/MagicShelf';
import { MagicShelfView } from './pages/MagicShelfView';
import { AppShell } from './components/AppShell';
import { SpinnerCentered } from './components/Spinner';
import { I18nProvider } from './lib/i18n';

// The reader pulls in epub.js (large) — load it only when a book is opened so it
// stays out of the initial bundle.
const Reader = lazy(() => import('./pages/Reader').then((m) => ({ default: m.Reader })));
// Native multi-format reader (PDF/audio/text) — also lazy, full-screen.
const NativeReader = lazy(() => import('./pages/NativeReader').then((m) => ({ default: m.NativeReader })));

// The SPA is mounted at <prefix>/app — where <prefix> is the reverse-proxy mount
// path (empty at the domain root). wouter needs the full base so client-side
// links resolve to <prefix>/app/… rather than a prefix-less /app/….
const ROUTER_BASE = BASE_PREFIX + '/app';

export function App() {
  const { data: me, isLoading } = useMe();
  const logout = useLogout();

  // #609: the classic UI puts the configured instance title in <title> on every
  // page; index.html ships the stock name as the pre-boot fallback.
  useEffect(() => {
    if (me?.instance_name) document.title = me.instance_name;
  }, [me?.instance_name]);

  if (isLoading) {
    return <SpinnerCentered size={40} />;
  }

  if (!me) {
    // Logged-out tree is routed too, so the magic-link page gets a real URL and
    // Login can navigate to it via wouter. On success the me-cache flips and the
    // authenticated tree below mounts.
    return (
      <Router base={ROUTER_BASE}>
        <Switch>
          <Route path="/magic-link">{() => <MagicLink />}</Route>
          <Route>{() => <Login />}</Route>
        </Switch>
      </Router>
    );
  }

  return (
    <I18nProvider locale={me.locale}>
    <Router base={ROUTER_BASE}>
      <Switch>
        {/* Full-screen reader — outside the app shell (no sidebar/topbar). */}
        <Route path="/read/:id">
          {(p) => (
            <Suspense fallback={<SpinnerCentered size={40} />}>
              <Reader id={p.id} />
            </Suspense>
          )}
        </Route>

        {/* Native non-EPUB reader (PDF / audio / text) — full screen */}
        <Route path="/view/:id/:format">
          {(p) => (
            <Suspense fallback={<SpinnerCentered size={40} />}>
              <NativeReader id={p.id} format={p.format} />
            </Suspense>
          )}
        </Route>

        {/* Everything else lives inside the shell. */}
        <Route>
          <AppShell userName={me.name} instanceName={me.instance_name} onLogout={() => logout.mutate()}>
            <Switch>
          <Route path="/book/:id/edit">{(p) => <EditBook id={p.id} />}</Route>
          <Route path="/book/:id/cover">{(p) => <CoverPicker id={p.id} />}</Route>
          <Route path="/book/:id/annotations">{(p) => <Annotations id={p.id} />}</Route>
          <Route path="/book/:id" component={BookDetail} />

          {/* Browse: entity lists + per-entity filtered catalog */}
          <Route path="/authors">{() => <BrowseList plural="authors" title="Authors" />}</Route>
          <Route path="/authors/:id">
            {(p) => <Catalog entityKind="author" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path="/series">{() => <BrowseList plural="series" title="Series" />}</Route>
          <Route path="/series/:id">
            {(p) => <Catalog entityKind="series" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path="/tags">{() => <BrowseList plural="tags" title="Tags" />}</Route>
          <Route path="/tags/:id">
            {(p) => <Catalog entityKind="tag" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path="/publishers">{() => <BrowseList plural="publishers" title="Publishers" />}</Route>
          <Route path="/publishers/:id">
            {(p) => <Catalog entityKind="publisher" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path="/languages">{() => <BrowseList plural="languages" title="Languages" />}</Route>
          <Route path="/languages/:id">
            {(p) => <Catalog entityKind="language" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path="/ratings">{() => <BrowseList plural="ratings" title="Ratings" />}</Route>
          <Route path="/ratings/:id">
            {(p) => <Catalog entityKind="rating" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path="/formats">{() => <BrowseList plural="formats" title="Formats" />}</Route>
          <Route path="/formats/:id">
            {(p) => <Catalog entityKind="format" entityId={decodeURIComponent(p.id)} />}
          </Route>

          {/* Shelves */}
          <Route path="/shelves">{() => <Shelves />}</Route>
          <Route path="/shelf/:id">{(p) => <Shelf id={p.id} />}</Route>

          {/* Discovery views (fixed server-side ?filter= categories) */}
          <Route path="/hot">{() => <Catalog view="hot" />}</Route>
          <Route path="/discover">{() => <Catalog view="discover" />}</Route>
          <Route path="/rated">{() => <Catalog view="rated" />}</Route>
          <Route path="/favorites">{() => <Catalog view="favorites" />}</Route>
          <Route path="/archived">{() => <Catalog view="archived" />}</Route>

          {/* Advanced search */}
          <Route path="/search">{() => <AdvancedSearch />}</Route>

          {/* Account / settings */}
          <Route path="/account">{() => <Account />}</Route>

          {/* Upload */}
          <Route path="/upload">{() => <Upload />}</Route>

          {/* Admin */}
          <Route path="/admin">{() => <Admin />}</Route>

          {/* Info pages */}
          <Route path="/whats-new">{() => <WhatsNew />}</Route>
          <Route path="/about">{() => <About />}</Route>
          <Route path="/tasks">{() => <Tasks />}</Route>
          <Route path="/table">{() => <Table />}</Route>
          <Route path="/duplicates">{() => <Duplicates />}</Route>
          <Route path="/magic/:id/edit">{(p) => <MagicShelf editId={p.id} />}</Route>
          <Route path="/magic/:id">{(p) => <MagicShelfView id={p.id} />}</Route>
          <Route path="/magic">{() => <MagicShelf />}</Route>

          <Route path="/">{() => <Catalog />}</Route>

          {/* Graceful 404 for any unmatched in-shell route (no blank page). */}
          <Route>{() => <NotFound />}</Route>
            </Switch>
          </AppShell>
        </Route>
      </Switch>
    </Router>
    </I18nProvider>
  );
}
