# Stats and Activity translation verification

The reporter’s flow is `/cwa-stats-show`, opened directly in Classic or through
the New UI’s Statistics link, with a non-English account language. The fix
covers all four tabs, including the System template that the original report
did not identify, and request-time history headers.

The behavioral checks render the actual templates against compiled gettext
catalogs and execute their scripts in Node. They cover translated tab/System
headings, populated and empty API charts, the demo toggle, actual pie legend,
label and tooltip callbacks, raw category identity and metric totals, hostile
translated text, attempted-login names and multiline System history text.
Actual Russian, French and Dutch catalogs render counted fixes and live
controls. A real handler check preserves the all-time query while translating
its display and history headers; the JavaScript bridge keeps reordered named
fields and a single percent sign.

The original templates failed five checks. Separate regressions reproduced
unsafe history HTML, the English all-time label and import-time headers.
The current focused checks pass. All 28 shipped catalogs compile. Independent
source review found untranslated event/format codes and live count fragments;
those findings were repaired before freezing the change.

Published-image equality, authenticated HTTP/browser flows, visual evidence
and full CI are separate gates. Their final results will be recorded in the
PR before it becomes ready. Synthetic translator catalogs prove the pipeline;
they do not certify native-speaker linguistic review. The supplied new locale
text is project-authored; existing contributed translations are preserved.

CSV identifiers, source metadata and formats stay stable. No physical-device
or full reverse-proxy claim follows from the local render checks.
