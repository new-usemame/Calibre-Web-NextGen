# UI feedback implementation evidence — 2026-09-15

## Scope

This change addresses the cover-designer lettering strip, the distinction
between shared and private book covers, and the book-detail action order.

### Actual storage and authority

- **Shared cover** is the book's global Calibre `cover.jpg`; people who can see
  the book see it. Changing it remains an edit/admin operation.
- **My private cover** is a per-user override under
  `CONFIG_DIR/user-covers/<user_id>`. It changes only that person's web view
  and their e-reader deliveries. It does not create another library and cannot
  modify the shared image.
- Image providers and their keys are instance-wide configuration. A selected
  scope determines where a fetched image is saved; it does not determine which
  key is used. The UI now says this directly, and provider-key status is shown
  to an editor in either cover scope. Key values remain write-only password
  fields and are not present in any evidence artifact.

## Behavior delivered

- Lettering is one horizontally scrollable, snapping row at all widths. On a
  phone the cards stay large enough to read instead of wrapping into tiny,
  mostly blank columns. The existing arrow/Home/End radio-group behavior keeps
  keyboard selection and scrolls the selected card into view.
- Font cards use real authenticated server JPEGs. The sample uses the fixed
  glyph string `Aa Bb Cc`, so the image shows a font face rather than a font
  label. The crop is anchored at the top to retain the rendered ink. The
  sample URL and disk-cache key now share catalogue cache version 2, so an
  existing browser or server cannot keep serving the prior sample for 24 hours.
- The action row is ordered **Read now → Edit cover → Add to shelf → star-only
  favorite → BookX-only Remove from my library → flexible spacer → Settings**.
  `BookX` is unique in the SPA and communicates removing a personal library
  membership without implying a global deletion. Its accessible name and
  tooltip are exactly “Remove from my library”. The existing impact/confirm
  sequence and the separate admin-only global `Trash2` deletion remain intact.

## Seen-red / green browser evidence

The original product commit `c170a3a37` was built in a throwaway private rig
from `/tmp/cwng-ui-feedback-pre-fix`, using the same newly written behavior
checks as the fixed build:

| Check | Original product (red) | Fixed worktree (green) |
| --- | --- | --- |
| 320px lettering rail | `scrollWidth === clientWidth === 238`; cards wrapped, so there was no horizontal rail. | Contract E2E at 320px: `scrollWidth > clientWidth`, `overflow-x:auto`, snap, and End moves focus/scroll. |
| Personal-library actions | Visible order was `Read now`, `Add to favorites`, `Add to shelf`, `More actions`; it lacked direct cover edit, private removal, and Settings naming. | Focused desktop action E2E verifies the requested order, BookX class, exact accessible name/title, and no “delete” wording. |
| Real font pixels | The supplied feedback and the old rendering show cropped/blank-looking samples. | Live backend E2E loads authenticated JPEG responses and checks distinct checksums for Serif, Sans, and Mono. Manual browser proof at 390px observed 297px client width, 3880px scroll width, five loaded 266×400 real samples, and End changed `scrollLeft` from 0 to 3583 without document overflow. |

Both browser builds used `local-dev/private-e2e-rig.sh`, which records the
image id and served bundle hash before and after each run. The fixed rig used
image `sha256:e50fa02168cafac485f899829421750d99c34fd41e1c2e81ba4f0b25f1df15cc`
and served bundle SHA-256
`46df8740c8e8488f16a4edf119ddb760892657eda72a8f1d557bcd703ac6892f`.

## Screenshot evidence

The compressed JPEGs are all from the isolated local fixed/private rig unless
labelled `before`:

- `artifacts/ui-feedback-2026-09-15/before-phone-320-font-grid-real.jpg` — old
  wrapping, blank/cropped font-card layout.
- `artifacts/ui-feedback-2026-09-15/after-phone-390-font-carousel-start.jpg`
  and `after-phone-390-font-carousel-end.jpg` — first and End-key-revealed
  positions of the real font carousel.
- `artifacts/ui-feedback-2026-09-15/after-desktop-font-carousel.jpg` — loaded
  desktop font samples and single horizontal strip.
- `artifacts/ui-feedback-2026-09-15/before-desktop-book-actions.jpg` — old
  personal-action ordering from the original product probe.
- `artifacts/ui-feedback-2026-09-15/after-phone-320-book-actions.jpg` — fixed
  responsive row in the real global-library admin context. Its JSON companion
  records 320px document width/no overflow. The private membership control is
  absent there by design; it is covered by the dedicated personal-library E2E.

The original user-provided reference screenshots remain in the supplied
feedback directory outside the repository.

## Commands and results

- `npm run build` — passed.
- `npx tsc -p tsconfig.e2e.json --noEmit` — passed.
- `./local-dev/private-e2e-rig.sh test . --project=mobile e2e/cover-designer.spec.ts --grep 'lettering is a keyboard-operable'` — passed after the original-product red probe.
- `./local-dev/private-e2e-rig.sh test . --project=desktop e2e/cover-designer-live.spec.ts --grep 'real server font samples'` — passed against the real backend, no page routes for designer/font endpoints.
- `./local-dev/private-e2e-rig.sh test . --project=desktop e2e/book-page-actions.spec.ts` — passed (20 tests plus setup).
- `./local-dev/private-e2e-rig.sh test . --project=mobile e2e/book-page-actions.spec.ts --grep 'book page layout holds'` — passed.
- `./local-dev/private-e2e-rig.sh test . --project=desktop --project=mobile e2e/a11y.spec.ts --grep 'book detail'` — passed (desktop and mobile axe scans; no critical or serious findings).
- Container-level cache probe — changing the catalogue version changed both the server disk-cache key and the font-sample URL; passed.
- SPA msgid extraction, all shipped `messages.po` `msgfmt --check` compilation, Python compileall, and E2E TypeScript typecheck — passed.

The focused Python test could not be collected under the host interpreter due
an existing PyOpenSSL/OpenSSL ABI mismatch (`lib.GEN_EMAIL` absent) before the
project test module imported. The already-running private app container also
has no `pytest` module. No dependency was changed or installed to work around
either environment limit; the private project container supplied the
browser/server verification above.
