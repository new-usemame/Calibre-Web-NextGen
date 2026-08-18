import { SPA_ROUTES } from './routes.ts';

const LIST_ORIGIN_PATTERNS = [
  SPA_ROUTES.authors,
  SPA_ROUTES.author,
  SPA_ROUTES.seriesList,
  SPA_ROUTES.series,
  SPA_ROUTES.tags,
  SPA_ROUTES.tag,
  SPA_ROUTES.publishers,
  SPA_ROUTES.publisher,
  SPA_ROUTES.languages,
  SPA_ROUTES.language,
  SPA_ROUTES.ratings,
  SPA_ROUTES.rating,
  SPA_ROUTES.formats,
  SPA_ROUTES.format,
  SPA_ROUTES.shelves,
  SPA_ROUTES.shelf,
  SPA_ROUTES.magicView,
  SPA_ROUTES.hot,
  SPA_ROUTES.discover,
  SPA_ROUTES.rated,
  SPA_ROUTES.favorites,
  SPA_ROUTES.archived,
  // AdvancedSearch keeps criteria only in component state, not the URL, so returning
  // to /search restores an empty form rather than the previous result set.
  SPA_ROUTES.search,
  SPA_ROUTES.table,
  SPA_ROUTES.duplicates,
  SPA_ROUTES.library,
] as const;

function routePatternRegex(pattern: string): RegExp {
  const source = pattern
    .split('/')
    .map((segment) => segment.startsWith(':')
      ? '[^/]+'
      : segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('/');
  return new RegExp(pattern === '/' ? '^/$' : `^${source}/?$`);
}

const LIST_ORIGIN_MATCHERS = LIST_ORIGIN_PATTERNS.map(routePatternRegex);

let rememberedOrigin: string | undefined;

export function isListOrigin(path: string): boolean {
  return LIST_ORIGIN_MATCHERS.some((matcher) => matcher.test(path));
}

export function recordOrigin(path: string, search: string): void {
  if (!isListOrigin(path)) return;
  rememberedOrigin = path + (search ? `?${search}` : '');
}

export function backTarget(): { href: string; isOrigin: boolean } {
  return rememberedOrigin
    ? { href: rememberedOrigin, isOrigin: rememberedOrigin !== '/' }
    : { href: '/', isOrigin: false };
}

export function __resetOriginForTests(): void {
  rememberedOrigin = undefined;
}
