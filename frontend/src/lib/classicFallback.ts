/** SPA-only tools have no classic route to suggest, even behind a proxy prefix. */
export function classicFallbackPath(pathname: string, prefix: string): string | null {
  const appBase = `${prefix}/app`;
  const path = pathname.startsWith(`${appBase}/`) ? pathname.slice(appBase.length) : '/';
  if (path.startsWith('//')) return null;
  if (['/find-books', '/admin/acquisition'].includes(path.replace(/\/+$/, ''))) return null;
  return prefix + path;
}
