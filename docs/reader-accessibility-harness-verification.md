# Reader accessibility harness verification

The former reader gate checked `isVisible()` immediately after navigating to
book detail and could skip while the detail query was loading. The seeded rig
now requires an EPUB returned by its real API and awaits the Read now link and
rendered iframe. Missing reader fixtures fail instead of silently passing.

A second gap appeared once this test actually ran. EPUB content uses a
`sandbox="allow-same-origin"` iframe: author scripts are intentionally disabled.
AxeBuilder's asynchronous child-frame scan cannot finish reliably there.
Directly evaluating axe.run in that frame produced Chromium's "Resulting promise
was garbage collected" error; AxeBuilder catches child-frame exceptions. A
visible, deliberately unnamed button inside the frame was not reported. Runs
could take around thirty seconds per child scan or exceed the test timeout.

`e2e/readerAxeSnapshot.ts` creates an explicitly test-only, inert snapshot of
the currently rendered book in the same iframe. It strips author scripts and
inline event handlers before permitting axe execution. It asserts that the
actual product frame initially has its script-disabled same-origin sandbox,
and that iframe/body rectangles, visible text, foreground/background colors
and font size match after replacement. Scroll position is restored. The
product's source and production sandbox are unchanged.

Real TOC, focus, Escape and progressbar behavior is tested before replacement.
Dark and light use separate browser contexts and the reader's own persisted
palette. Both full parent/frame scans retain all WCAG tags and the empty
violation allowlist. The helper does not exclude iframe content or disable rules.

Verification against the final audit rig:

- Real dark and light reader checks pass, including exact snapshot measurements.
- A forced visible unnamed button in the actual reader frame produces a critical
  `button-name` failure with the iframe and button selectors after snapshotting.
- A reusable synthetic harness regression checks that the full axe runner reports
  a child defect and that neither author script nor inline onload handler runs.
- Without the helper, that regression fails to detect the defect. Restoring it
  passes. The normal reader checks complete in approximately four seconds total.

Limits: the snapshot is an oracle for the rendered document's accessibility,
not post-replacement reader interaction, future chapters, arbitrary EPUB script
behavior, or all possible font/layout combinations. Such interaction remains
covered on the actual sandboxed reader before snapshotting and by the separate
reader behavior suite. The snapshot assertions deliberately fail on unexpected
layout changes instead of accepting an unrepresentative scan.
