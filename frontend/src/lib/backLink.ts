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

const BOOK_CHAIN_PATTERNS = [
  SPA_ROUTES.book,
  SPA_ROUTES.editBook,
  SPA_ROUTES.coverPicker,
  SPA_ROUTES.annotations,
  SPA_ROUTES.reader,
  SPA_ROUTES.nativeReader,
] as const;

function bookChainPatternRegex(pattern: string): RegExp {
  const source = pattern
    .split('/')
    .map((segment) => {
      if (segment === ':id') return '([^/]+)';
      if (segment.startsWith(':')) return '[^/]+';
      return segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    })
    .join('/');
  return new RegExp(`^${source}/?$`);
}

const BOOK_CHAIN_MATCHERS = BOOK_CHAIN_PATTERNS.map(bookChainPatternRegex);

let lastList: string | undefined;
let currentBook: string | undefined;

function hasSafePathSyntax(path: string): boolean {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) return false;
  try {
    return path.split('/').every((segment) => {
      const decoded = decodeURIComponent(segment);
      return decoded !== '.' && decoded !== '..' && !decoded.includes('\\');
    });
  } catch {
    return false;
  }
}

export function isListOrigin(path: string): boolean {
  return hasSafePathSyntax(path)
    && LIST_ORIGIN_MATCHERS.some((matcher) => matcher.test(path));
}

function bookChainId(path: string): string | undefined {
  if (!hasSafePathSyntax(path)) return undefined;
  for (const matcher of BOOK_CHAIN_MATCHERS) {
    const match = matcher.exec(path);
    if (match) return decodeURIComponent(match[1]);
  }
  return undefined;
}

function isValidOrigin(value: unknown): value is string {
  if (typeof value !== 'string' || value.includes('\\')) return false;
  const path = value.split(/[?#]/, 1)[0];
  if (!hasSafePathSyntax(path)) return false;
  try {
    if (decodeURIComponent(value).includes('\\')) return false;
  } catch {
    return false;
  }
  return isListOrigin(path);
}

function activeHistory(): History | undefined {
  const candidate = (globalThis as typeof globalThis & { history?: History }).history;
  return candidate && typeof candidate.replaceState === 'function' ? candidate : undefined;
}

function stateRecord(state: unknown): Record<string, unknown> | undefined {
  return state !== null && typeof state === 'object'
    ? state as Record<string, unknown>
    : undefined;
}

export function recordOrigin(path: string, search: string): void {
  if (isListOrigin(path)) {
    const candidate = path + (search ? `?${search}` : '');
    lastList = isValidOrigin(candidate) ? candidate : undefined;
    currentBook = undefined;
    return;
  }

  const bookId = bookChainId(path);
  if (bookId !== undefined) {
    if (currentBook === undefined) {
      currentBook = bookId;
    } else if (currentBook !== bookId) {
      lastList = undefined;
      currentBook = bookId;
    }
    return;
  }

  lastList = undefined;
  currentBook = undefined;
}

export function backTarget(activeBookPath?: string): { href: string; isOrigin: boolean } {
  if (activeBookPath !== undefined) recordOrigin(activeBookPath, '');

  const browserHistory = activeHistory();
  if (!browserHistory) return { href: '/', isOrigin: false };

  let currentState = stateRecord(browserHistory.state);
  if (!currentState || !Object.prototype.hasOwnProperty.call(currentState, 'cwngOrigin')) {
    browserHistory.replaceState({
      ...(currentState ?? {}),
      cwngOrigin: lastList ?? null,
    }, '');
    currentState = stateRecord(browserHistory.state);
  }

  const origin = currentState?.cwngOrigin;
  return isValidOrigin(origin)
    ? { href: origin, isOrigin: origin !== '/' }
    : { href: '/', isOrigin: false };
}

export function __resetOriginForTests(): void {
  lastList = undefined;
  currentBook = undefined;
}
