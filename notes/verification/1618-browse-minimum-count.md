# Minimum book count on Authors and Series (#1618)

Authors and Series now expose an inclusive Minimum books field alongside name search. Blank shows all counts; 2 hides single-book authors, and 5 finds series with more than four books. Invalid non-whole or unsafe values produce an associated error, an explicit empty-result message and a polite result count. Controls remain visible on small lists. Existing permission-filtered API entity counts drive the filter; no new query or visibility rule is added.

Product choices: inclusive minimum is easier to explain than separate strict greater-than controls and serves both requested examples. Local name/count filters reset when changing browse routes, avoiding hidden filters after navigation; compact/grid preference remains separate. Parent #706 read/unread aggregation is outside this request. This targets New UI lists, not a redesign of classic browse pages.

Independent root review reproduced a route-state bug in the first implementation: Authors 999 carried into Series on client navigation. The worker keyed the inner view by route and gated count parsing to Authors/Series. A real sidebar regression proves Series starts blank/full, Tags has no count filter and shows all visible tags, and returning to Authors resets filters without reloading the document. Seen-red evidence is retained.

Final complete private image 10a7c8229dce6ece80345ddf4be8bc3f2970fdabe15190b35c105a2090c884f3 serves index-r4agJTzz.js. Eleven focused desktop/phone cases pass: inclusive/name combination, invalid values and clearing, small-list controls, real un-intercepted API counts, route navigation, keyboard and light/dark Axe checks. Actual JPEGs at 1280 desktop and 375 phone show controls, help and result status; root inspected desktop light and phone dark captures. No physical-device or screen-reader audio claim.

Production build, E2E TypeScript, SPA fuzzy/anchor checks and FR/NL catalog checks pass. The live private i18n API returns the added FR/NL strings. Root corrected the initially malformed changelog fragment and verified real fragment assembly. Full local smoke/unit: 10,272 passed, 103 skipped, only the two baseline metadata-replacement SQLite I/O failures previously reproduced on clean main. Diff check passes. Independent source review has no unresolved blocker.

Local evidence is retained in the X8 cwng-promised-1618 directory, including missing-control/navigation red logs, final focused/full logs, compressed screenshots and served-bundle hash. Operator owns merging and releases.
