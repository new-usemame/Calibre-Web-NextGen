# Desktop continuation — OBSERVED / ASSUMED

Date: 2026-07-14. Target: shared Chrome, localhost:8101, real EPUB book 192.

- OBSERVED: reader route and title were present: “The Adventures of Sherlock Holmes”.
- OBSERVED: reload/hydration showed Dark / KaiTi / 140% / 40px / 180%. This did not equal the requested Sepia / SimSun / 160% / 0px / 100%. I did not force those values.
- OBSERVED: iframe was present, complete, and rendered body text. Light-pass computed body values were background `rgb(251, 247, 238)`, color `rgb(42, 42, 42)`, font `KaiTi, serif`, padding `20px 11px`, line-height `40.32px`.
- OBSERVED: one 1280×800 snapshot showed the appearance panel at x=900,y=0,width=380,height=800. ASSUMED this was the requested desktop snapshot; the attached backend subsequently reported 375×667 and did not hold 1280×800.
- OBSERVED: appearance focus trap for the exercised edge: Close → Tab → Light; Shift+Tab returned to Close; Escape closed and focus restored to Reading appearance.
- OBSERVED: app dark was applied through the documented mechanism (`document.documentElement.setAttribute('data-theme','dark')` and `cwng.theme=dark`). Immediate panel colors were background `rgb(27, 37, 48)` and text `rgb(236, 232, 225)`.
- OBSERVED: TOC opened and exposed real chapter entries, including chapter I and chapter II. ASSUMED/UNVERIFIED: clean TOC focus trap, Escape/restore, and actual non-current chapter activation in this continuation; an attempted scripted pass opened the Highlight color dialog and the reader remained on chapter I.
- ASSUMED/UNVERIFIED: ArrowRight and PageDown rendition/location/page change. These were not completed in this continuation, and no persistence claim is made.
