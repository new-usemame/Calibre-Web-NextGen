# Luna SPA real-EPUB verification

Date: 2026-07-14 01:15 EDT
Target: `http://localhost:8101`
Book: “The Adventures of Sherlock Holmes”, book 192

## Result

FAIL — the reader, real EPUB, responsive appearance panel, TOC selection, named controls, and focus behavior were observed. The required persistence pass did not remain exact after reload, and desktop/mobile keyboard/rendition evidence was not complete enough for an overall PASS.

## Desktop — 1280×800

- OBSERVED: reader route `/app/read/192`, title “The Adventures of Sherlock Holmes”, and EPUB iframe rendered.
- OBSERVED: appearance panel controls were named: Page theme (Light/Sepia/Dark), Font family, Font size, Page margins, Line height.
- OBSERVED: Light selected, then Arial / 130% / 24px / 160%; iframe remained rendered at x=64, width=1152, height=749.
- OBSERVED: Dark selected afterward; Dark had `aria-pressed=true` and the four control values remained Arial / 130% / 24px / 160% immediately afterward.
- OBSERVED: reload and reopen showed Dark / Arial / 130% / 24px / 160% at one desktop checkpoint.
- OBSERVED: a later attached-browser desktop checkpoint could not hold the requested viewport consistently; it showed 1280×800 while reader state had reverted to the mobile-pass state. This makes the desktop persistence result inconsistent.
- OBSERVED: PageDown and ArrowRight were sent, and the visible Next page control was activated. A stable post-key rendition delta was not captured.
- ASSUMED: the immediate iframe geometry/control-value changes demonstrate rendition response; computed EPUB document style was not captured for every desktop control.

## Mobile — exactly 375×667

- OBSERVED: appearance panel measured x=30, y=0, width=345, height=667; it stayed inside the viewport.
- OBSERVED: Light pass changed KaiTi / 140% / 40px / 180%; Dark was then selected with those values present immediately afterward.
- OBSERVED: after reload/reopen, exact visible values were Sepia / Microsoft YaHei / 110% / 40px / 190%. This is a FAIL for exact mobile persistence and also shows theme/font/size/line-height drift from the immediately-after-change state.
- OBSERVED: real EPUB content was rendered in the mobile iframe, including chapter text.
- OBSERVED: visible Next page control was used; PageDown and ArrowRight inputs were sent. A swipe-specific post-change observation was not captured.
- ASSUMED: the panel’s bounding box proves viewport containment, but not that all internal content remains usable at every scroll position.

## TOC and navigation

- OBSERVED: TOC opened independently as navigation `Table of contents` and exposed real entries, including “I. A SCANDAL IN BOHEMIA” and “II. THE RED-HEADED LEAGUE”.
- OBSERVED: selecting “II. THE RED-HEADED LEAGUE” changed the EPUB content to the red-headed chapter (visible text begins with Jabez Wilson / fiery red hair).
- OBSERVED: Previous page, Next page, PageDown, and ArrowRight controls/inputs were exercised.
- ASSUMED: no position persistence was tested or claimed.

## Manual accessibility

- OBSERVED: appearance dialog had accessible name “Reading appearance”; Close, Light, Sepia, Dark, Font family, Font size, Page margins, and Line height were named in the accessibility tree.
- OBSERVED: appearance Tab entered on Light; Shift+Tab wrapped back to Close; Escape closed the dialog; the Reading appearance trigger was marked active after close.
- OBSERVED: TOC was exposed as navigation `Table of contents`; its Close button and chapter buttons were named. Shift+Tab wrapped to Close and Escape closed the panel.
- ASSUMED: full forward-cycle coverage of every TOC item was not independently enumerated.
- ASSUMED: no VoiceOver/screen-reader announcement quality claim was made.

## Verification chain

- OBSERVED → OBSERVED: localhost reader route → real book title/EPUB iframe.
- OBSERVED → OBSERVED: appearance trigger → named dialog/controls.
- OBSERVED → OBSERVED: TOC trigger → named chapter list → visible chapter change.
- OBSERVED → OBSERVED: keyboard actions → focus markers/closed overlays for the tested checkpoints.
- ASSUMED: every requested rendition key/swipe action produced a measurable page-position change.
- ASSUMED: exact settings persisted uniformly across all reloads; the mobile reload contradicts this.

## Fresh attached run (2026-07-14 01:20–01:25 EDT)

Verdict remains FAIL because the required keyboard PageDown/previous/next and mobile page-turn control produced no observed rendition-position delta in the tested chapter. The fresh persistence passes below did succeed after waiting for the explicit “Reader settings saved” status.

- OBSERVED desktop light 1280×800: real EPUB rendered; after reload the five values were Sepia / SimSun / 160% / 0px / 100%. The iframe document was complete and its computed style matched the selected theme, font, size, margin, and line-height.
- OBSERVED desktop dark app: app body was rgb(20, 28, 36); reader content used dark background with light text. Appearance controls were named and the stable accessibility snapshot fit the 1280×800 viewport.
- OBSERVED mobile dark exactly 375×667: panel snapshot bounds were x=30..375 and y=0..667. After save-confirmed reload, values were Sepia / Microsoft YaHei / 80% / 80px / 220%; iframe was complete and rendered chapter text.
- OBSERVED mobile light exactly 375×667: after save-confirmed reload, values were Dark / Arial / 150% / 12px / 210%; iframe was complete with matching computed styles.
- OBSERVED fresh TOC activation: selecting “I. A SCANDAL IN BOHEMIA” changed the iframe canonical resource to the chapter and exposed chapter text. No cross-device or KOReader position persistence was tested or claimed.
- OBSERVED appearance a11y: focus entered Close; Tab remained inside; Shift+Tab returned Close; Escape closed and restored the Reading appearance trigger. Controls had accessible names.
- OBSERVED TOC a11y: focus entered Close; Tab reached a named chapter item; Shift+Tab returned Close; Escape closed and restored the TOC trigger. Navigation and chapter buttons had accessible names.
- OBSERVED keyboard/mobile-turn gap: PageDown, ArrowRight, ArrowLeft, and mobile Next page were sent, but canonical URL, leading text, and measured scroll position did not change in the tested state. This is the blocking FAIL evidence.

### Fresh verification chain

- OBSERVED → OBSERVED: authenticated localhost SPA → book 192 detail with EPUB format → reader iframe.
- OBSERVED → OBSERVED: appearance controls → save status → reload → exact control values and computed iframe styles.
- OBSERVED → OBSERVED: app theme switch → dark/light body colors → reader panel/content contrast.
- OBSERVED → OBSERVED: TOC trigger → named chapter item → chapter canonical/text change.
- OBSERVED → OBSERVED: appearance/TOC focus actions → trapped focus and restored trigger focus.
- OBSERVED → OBSERVED: keyboard/mobile page-turn inputs → no measurable rendition delta.
- ASSUMED: the absence of a measured delta means the turn action failed or was ineffective in this state; no source-level diagnosis was performed.
