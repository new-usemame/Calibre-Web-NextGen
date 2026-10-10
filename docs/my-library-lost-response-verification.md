# Single-book membership response-loss verification

Final aggregate review found the bulk-action response-loss fix did not cover
single-book membership mutations. They advanced catalog revision only on
success. When a DELETE committed and its response was lost, optimistic rollback
restored old query data, while ordinary invalidation left the saved Catalog
scroll snapshot in its old revision. Returning from detail showed a ghost card
even though the real catalog API excluded that book.

Both single-book add and remove now reconcile library views in `onSettled`,
after success or optimistic rollback. The operation still reports its transport
failure; it does not invent a successful outcome. The authoritative refetch and
new catalog revision reflect whatever the server actually committed.

The real browser regression creates an owned account, visits the catalog and
book detail, forwards the actual DELETE to the server, verifies its successful
response, then aborts delivery of that response to the browser. It checks that
no success announcement appears, the error is announced, the real API excludes
the book, and same-document navigation back does not restore its card. The old
code was seen failing with card count1 vs expected0. The fixed built frontend
passes this case and the existing successful-removal and mode-restoration cases.

Initial green browser proof served the isolated candidate's built static assets
through Playwright routing while retaining the real rig's API/auth/database.
That temporary asset routing is not committed into the regression. Parent owns
the final immutable-image rerun after integration. Owned test accounts were
cleaned up by the fixture. No shared product state was reset.
