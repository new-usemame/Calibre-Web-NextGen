// Calibre-Web Automated – fork of Calibre-Web
// Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
// SPDX-License-Identifier: GPL-3.0-or-later
// See CONTRIBUTORS for full list of authors.

/** The separator between two authors.
 *
 *  An author's *display name* may itself contain a comma — "Leckie, Ann" is a
 *  perfectly ordinary Calibre author name — so a comma cannot also separate one
 *  author from the next without making the list ambiguous ("Leckie, Ann,
 *  Tchaikovsky, Adrian" reads as four names, issue #948).
 *
 *  ' & ' is not a new convention invented here; it is what the rest of the app
 *  already uses. Calibre joins authors with '&', the classic templates render
 *  '&' between author links (templates/index.html, templates/detail.html), the
 *  edit API hands the form '&'-joined authors (cps/api/edit.py) and the edit
 *  field tells the user so ("Authors (separate with &)"). Display is the only
 *  place that drifted.
 *
 *  Authors are the exception, not the rule: tags, publishers and languages are
 *  comma-separated lists whose values do not contain commas, so they keep ', '.
 */
export const AUTHOR_SEPARATOR = ' & ';

/** Join author display names for presentation. */
export function formatAuthors(authors: readonly string[] | null | undefined): string {
  return (authors ?? []).join(AUTHOR_SEPARATOR);
}
