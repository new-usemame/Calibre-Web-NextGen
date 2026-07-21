import { lazy, Suspense, useEffect, type ReactNode } from 'react';
import { Router, Route, Switch, useLocation } from 'wouter';
import { RouteA11y } from './lib/a11y/useRouteA11y';
import { BASE_PREFIX, type AdvancedSearchParams } from './lib/api';
import { bodyFontStack, displayFontStack } from './lib/fonts';
import { resolveTheme } from './lib/themes';
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
import { RoutedErrorBoundary } from './components/ErrorBoundary';
import { SpinnerCentered } from './components/Spinner';
import { I18nProvider } from './lib/i18n';
import { usePostAuthRedirect } from './lib/authRedirect';
import { AUTH_ROUTES, SPA_ROUTES } from './lib/routes';

// The reader pulls in epub.js (large) — load it only when a book is opened so it
// stays out of the initial bundle.
const Reader = lazy(() => import('./pages/Reader').then((m) => ({ default: m.Reader })));
// Native multi-format reader (PDF/audio/text) — also lazy, full-screen.
const NativeReader = lazy(() => import('./pages/NativeReader').then((m) => ({ default: m.NativeReader })));

// The SPA is mounted at <prefix>/app — where <prefix> is the reverse-proxy mount
// path (empty at the domain root). wouter needs the full base so client-side
// links resolve to <prefix>/app/… rather than a prefix-less /app/….
const ROUTER_BASE = BASE_PREFIX + '/app';

// #855: a render error anywhere under the router used to unmount the entire SPA,
// leaving an empty #root — a black screen with no way back. This boundary keeps
// the crash contained and recoverable, and clears itself when the user navigates
// away, so a single bad route never strands the session. It sits INSIDE <Router>
// so it can read the current location as its reset key.
function RouteBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return (
    <RoutedErrorBoundary location={location} homeHref={ROUTER_BASE || '/'}>
      {children}
    </RoutedErrorBoundary>
  );
}

// A saved default view (#498) FILTERS the library; it does not replace it with
// the search page. Swapping the component here cost the library heading, actions
// and Discover strip and retitled the home page "Advanced search" (#928).
function Library({ defaultFilter }: { defaultFilter?: AdvancedSearchParams }) {
  return <Catalog defaultFilter={defaultFilter} />;
}

function AuthenticatedAuthLanding() {
  const redirectAfterAuth = usePostAuthRedirect();
  useEffect(() => { redirectAfterAuth(); }, [redirectAfterAuth]);
  return <SpinnerCentered size={40} />;
}

export function App() {
  const { data: me, isLoading } = useMe();
  const logout = useLogout();
  // Anonymous browsing (#1023): /me answers with the Guest identity rather than
  // 401ing, so `me != null` means "we know who you are", not "you signed in".
  const isGuest = !!me?.role?.anonymous;

  // #701 — apply the user's UI font presets by overriding the design tokens
  // (--font-body / --font-display) on the document root. The stored value is a
  // preset key; lib/fonts.ts resolves it to a CSS stack (empty → clear the
  // override so the theme default from tokens.css applies). Reset on logout.
  useEffect(() => {
    const root = document.documentElement;
    const body = bodyFontStack(me?.ui_font_body);
    const display = displayFontStack(me?.ui_font_display);
    if (body) root.style.setProperty('--font-body', body);
    else root.style.removeProperty('--font-body');
    if (display) root.style.setProperty('--font-display', display);
    else root.style.removeProperty('--font-display');
  }, [me?.ui_font_body, me?.ui_font_display]);

  // Apply the user's saved palette after authentication. The pre-boot script
  // handles the logged-out/loading tree; this keeps it current once `me` loads.
  useEffect(() => {
    const stored = me?.theme || 'dark';
    localStorage.setItem('cwng.theme', stored);
    document.documentElement.setAttribute('data-theme', resolveTheme(stored));

    if (stored !== 'system') return;

    const media = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => {
      document.documentElement.setAttribute('data-theme', resolveTheme('system'));
    };
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, [me?.theme]);

  // #609: the classic UI puts the configured instance title in <title> on every
  // page. Per-page titling + route focus is handled by <RouteA11y> below
  // (SC 2.4.2 / 2.4.3); index.html ships the stock name as the pre-boot fallback.

  if (isLoading) {
    return <SpinnerCentered size={40} />;
  }

  if (!me) {
    // Logged-out tree is routed too, so the magic-link page gets a real URL and
    // Login can navigate to it via wouter. On success the me-cache flips and the
    // authenticated tree below mounts.
    return (
      <Router base={ROUTER_BASE}>
        <RouteA11y />
        <RouteBoundary>
          <Switch>
            <Route path={AUTH_ROUTES.magicLink}>{() => <MagicLink />}</Route>
            <Route>{() => <Login />}</Route>
          </Switch>
        </RouteBoundary>
      </Router>
    );
  }

  return (
    <I18nProvider locale={me.locale}>
    <Router base={ROUTER_BASE}>
      <RouteA11y instanceName={me.instance_name} />
      <RouteBoundary>
      <Switch>
        {/* Full-screen reader — outside the app shell (no sidebar/topbar). */}
        <Route path={SPA_ROUTES.reader}>
          {(p) => (
            <Suspense fallback={<SpinnerCentered size={40} />}>
              <Reader id={p.id} />
            </Suspense>
          )}
        </Route>

        {/* Native non-EPUB reader (PDF / audio / text) — full screen */}
        <Route path={SPA_ROUTES.nativeReader}>
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
          {/* #1023: with anonymous browsing on, `me` is populated for a visitor
              who has not signed in, so these routes can no longer assume an
              authenticated session. Bouncing a guest off /login would leave them
              with no way to sign in through the SPA at all. */}
          <Route path={AUTH_ROUTES.login}>{() => isGuest ? <Login /> : <AuthenticatedAuthLanding />}</Route>
          <Route path={AUTH_ROUTES.magicLink}>{() => isGuest ? <MagicLink /> : <AuthenticatedAuthLanding />}</Route>
          <Route path={SPA_ROUTES.editBook}>{(p) => <EditBook id={p.id} />}</Route>
          <Route path={SPA_ROUTES.coverPicker}>{(p) => <CoverPicker id={p.id} />}</Route>
          <Route path={SPA_ROUTES.annotations}>{(p) => <Annotations id={p.id} />}</Route>
          <Route path={SPA_ROUTES.book} component={BookDetail} />

          {/* Browse: entity lists + per-entity filtered catalog */}
          <Route path={SPA_ROUTES.authors}>{() => <BrowseList plural="authors" title="Authors" />}</Route>
          <Route path={SPA_ROUTES.author}>
            {(p) => <Catalog entityKind="author" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path={SPA_ROUTES.seriesList}>{() => <BrowseList plural="series" title="Series" />}</Route>
          <Route path={SPA_ROUTES.series}>
            {(p) => <Catalog entityKind="series" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path={SPA_ROUTES.tags}>{() => <BrowseList plural="tags" title="Tags" />}</Route>
          <Route path={SPA_ROUTES.tag}>
            {(p) => <Catalog entityKind="tag" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path={SPA_ROUTES.publishers}>{() => <BrowseList plural="publishers" title="Publishers" />}</Route>
          <Route path={SPA_ROUTES.publisher}>
            {(p) => <Catalog entityKind="publisher" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path={SPA_ROUTES.languages}>{() => <BrowseList plural="languages" title="Languages" />}</Route>
          <Route path={SPA_ROUTES.language}>
            {(p) => <Catalog entityKind="language" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path={SPA_ROUTES.ratings}>{() => <BrowseList plural="ratings" title="Ratings" />}</Route>
          <Route path={SPA_ROUTES.rating}>
            {(p) => <Catalog entityKind="rating" entityId={decodeURIComponent(p.id)} />}
          </Route>

          <Route path={SPA_ROUTES.formats}>{() => <BrowseList plural="formats" title="Formats" />}</Route>
          <Route path={SPA_ROUTES.format}>
            {(p) => <Catalog entityKind="format" entityId={decodeURIComponent(p.id)} />}
          </Route>

          {/* Shelves */}
          <Route path={SPA_ROUTES.shelves}>{() => <Shelves />}</Route>
          <Route path={SPA_ROUTES.shelf}>{(p) => <Shelf id={p.id} />}</Route>

          {/* Discovery views (fixed server-side ?filter= categories) */}
          <Route path={SPA_ROUTES.hot}>{() => <Catalog view="hot" />}</Route>
          <Route path={SPA_ROUTES.discover}>{() => <Catalog view="discover" />}</Route>
          <Route path={SPA_ROUTES.rated}>{() => <Catalog view="rated" />}</Route>
          <Route path={SPA_ROUTES.favorites}>{() => <Catalog view="favorites" />}</Route>
          <Route path={SPA_ROUTES.archived}>{() => <Catalog view="archived" />}</Route>

          {/* Advanced search */}
          <Route path={SPA_ROUTES.search}>{() => <AdvancedSearch />}</Route>

          {/* Account / settings */}
          <Route path={SPA_ROUTES.account}>{() => <Account />}</Route>

          {/* Upload */}
          <Route path={SPA_ROUTES.upload}>{() => <Upload />}</Route>

          {/* Admin */}
          <Route path={SPA_ROUTES.admin}>{() => <Admin />}</Route>

          {/* Info pages */}
          <Route path={SPA_ROUTES.whatsNew}>{() => <WhatsNew />}</Route>
          <Route path={SPA_ROUTES.about}>{() => <About />}</Route>
          <Route path={SPA_ROUTES.tasks}>{() => <Tasks />}</Route>
          <Route path={SPA_ROUTES.table}>{() => <Table />}</Route>
          <Route path={SPA_ROUTES.duplicates}>{() => <Duplicates />}</Route>
          <Route path={SPA_ROUTES.magicEdit}>{(p) => <MagicShelf editId={p.id} />}</Route>
          <Route path={SPA_ROUTES.magicView}>{(p) => <MagicShelfView id={p.id} />}</Route>
          <Route path={SPA_ROUTES.magic}>{() => <MagicShelf />}</Route>

          <Route path={SPA_ROUTES.library}>{() => <Library defaultFilter={me.catalog?.default_filter ?? undefined} />}</Route>

          {/* Graceful 404 for any unmatched in-shell route (no blank page). */}
          <Route>{() => <NotFound />}</Route>
            </Switch>
          </AppShell>
        </Route>
      </Switch>
      </RouteBoundary>
    </Router>
    </I18nProvider>
  );
}
