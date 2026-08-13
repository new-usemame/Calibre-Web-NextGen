/**
 * Canonical SPA route table. App.tsx renders these exact patterns and the
 * post-auth redirect classifier matches against the same values, so a classic
 * route can never drift into being treated as a client-side route.
 */
export const SPA_ROUTES = {
  reader: '/read/:id',
  nativeReader: '/view/:id/:format',
  editBook: '/book/:id/edit',
  coverPicker: '/book/:id/cover',
  annotations: '/book/:id/annotations',
  book: '/book/:id',
  authors: '/authors',
  author: '/authors/:id',
  seriesList: '/series',
  series: '/series/:id',
  tags: '/tags',
  tag: '/tags/:id',
  publishers: '/publishers',
  publisher: '/publishers/:id',
  languages: '/languages',
  language: '/languages/:id',
  ratings: '/ratings',
  rating: '/ratings/:id',
  formats: '/formats',
  format: '/formats/:id',
  shelves: '/shelves',
  shelf: '/shelf/:id',
  hot: '/hot',
  discover: '/discover',
  rated: '/rated',
  favorites: '/favorites',
  archived: '/archived',
  search: '/search',
  account: '/account',
  devices: '/account/devices',
  upload: '/upload',
  admin: '/admin',
  whatsNew: '/whats-new',
  about: '/about',
  tasks: '/tasks',
  table: '/table',
  duplicates: '/duplicates',
  magicEdit: '/magic/:id/edit',
  magicView: '/magic/:id',
  magic: '/magic',
  library: '/',
} as const;

export const AUTH_ROUTES = {
  login: '/login',
  magicLink: '/magic-link',
} as const;

export const SPA_ROUTE_PATTERNS = Object.values(SPA_ROUTES);
