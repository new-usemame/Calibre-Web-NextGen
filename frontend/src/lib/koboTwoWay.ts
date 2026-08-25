/* Kobo two-way annotation sync — shared per-book state presentation.
 *
 * The labels live here (not duplicated per page) because Account, BookDetail
 * and Annotations all render the same five authority states and must never
 * drift apart in wording. `t` is passed in so every user-facing string stays
 * a literal single-quoted t() call the msgid extractor can see, and so
 * translation happens in the calling component's render.
 *
 * Honesty rule (design doc §10 Stage 0): enabling the feature does NOT mean a
 * book is syncing — it must be seeded and proven first — so the states are
 * labelled by what is actually true ("Not set up yet"), never by what the
 * user hopes is true ("Will sync").
 */
import type { TFunction } from './i18n';
import type { KoboTwoWayBookState } from './api';

export type KoboTwoWayTone = 'muted' | 'info' | 'ok' | 'warn';

export function authorityLabel(
  t: TFunction,
  book: Pick<KoboTwoWayBookState, 'authority_status'>,
  scope: 'all' | 'selected',
): string {
  switch (book.authority_status) {
    case 'seeding':
      return t('Setting up…');
    case 'authoritative':
      return t('Two-way sync active');
    case 'quarantined':
      return t('Paused after a problem');
    case 'disabled':
      return scope === 'selected' ? t('Not picked') : t('Excluded');
    default:
      return t('Not set up yet');
  }
}

export function authorityTone(
  book: Pick<KoboTwoWayBookState, 'authority_status'>,
): KoboTwoWayTone {
  switch (book.authority_status) {
    case 'seeding':
      return 'info';
    case 'authoritative':
      return 'ok';
    case 'quarantined':
      return 'warn';
    default:
      return 'muted';
  }
}

/** Short badge for the blocked case only. 'unknown'/'absent' are ordinary
 *  pipeline states (everything starts unknown) and are explained in the
 *  Account section copy rather than badged on every row. */
export function opaqueLabel(
  t: TFunction,
  book: Pick<KoboTwoWayBookState, 'opaque_content_status'>,
): string | null {
  return book.opaque_content_status === 'present'
    ? t('Has pen or drawing notes')
    : null;
}
