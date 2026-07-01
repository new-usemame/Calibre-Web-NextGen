import { Link } from 'wouter';
import { BASE_PREFIX } from '../lib/api';
import { useT } from '../lib/i18n';
import { EmptyState } from '../components/EmptyState';

/** Graceful in-SPA 404. Reached when no route matches (e.g. an old bookmark or a
 *  not-yet-built page) — shows a clear message + a way home instead of a blank
 *  content area. Also links to the legacy UI in case the path exists there. */
export function NotFound() {
  const t = useT();
  // The path after the <prefix>/app base, so the legacy-UI suggestion points at
  // the equivalent classic route the user may have been looking for. Keep the
  // reverse-proxy mount prefix so the classic link stays under the same subpath.
  // Plain string slice (not a RegExp) so a dotted prefix like /app.v2 is matched
  // literally rather than as a wildcard.
  const appBase = `${BASE_PREFIX}/app`;
  const { pathname } = window.location;
  const afterApp = pathname.startsWith(appBase) ? pathname.slice(appBase.length) || '/' : '/';
  const legacyPath = BASE_PREFIX + afterApp;
  return (
    <main style={{ padding: '48px 24px', maxWidth: 560, margin: '0 auto', textAlign: 'center' }}>
      <EmptyState message={t("This page doesn't exist here.")} />
      <p style={{ color: 'var(--text-muted)', marginTop: 8 }}>
        {t('It may have moved, or it might still live in the classic interface.')}
      </p>
      <div style={{ display: 'flex', gap: 12, justifyContent: 'center', marginTop: 20, flexWrap: 'wrap' }}>
        <Link href="/" style={{ color: 'var(--accent)', fontWeight: 600 }}>{t('Go to your library')}</Link>
        <a href={legacyPath} style={{ color: 'var(--text-muted)' }}>{t('Open the classic interface')}</a>
      </div>
    </main>
  );
}
