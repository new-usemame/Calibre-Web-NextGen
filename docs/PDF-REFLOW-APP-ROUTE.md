# PDF Reflow application route

The Reflow page starts `TaskReflowPdf`. Its supervised native worker calls
`vision_pipeline.convert`, the same page-image converter used by direct library
callers. The application does not ask a model to restyle extracted HTML.

Clean native pages retain their source text. Pages that need reading are rendered
as images and transcribed by the configured page-reading model. Local English OCR
supplies alignment evidence. It is not used as a substitute for a model's
transcription. Source ownership, note links, text quality, non-Latin handling,
whole-book coverage, EPUB structure and output size are checked before publication.
A passage that cannot be read safely remains available as an explicitly disclosed
original-source image, including enlarged evidence where needed.

## Estimates and consent

The initial source assessment can sample a long PDF and labels that scope. Paid
conversion additionally requires a prepared vision quote bound to the current PDF
SHA-256, page-image identities, model configuration and request limits. It records
first-reading, targeted-reread and bounded note-marker ceilings separately from
expected cost. Expected cost is an estimate, not a reservation or a promise.

The per-book target is $0.50; the maximum allowed cap is $5, subject to a lower
administrator limit. The user's authorized cap and the shared administrator budget
must both permit each request. A cap below the quote can produce a partial set of
readings, with original-source disclosures for the rest. Changing source, selected
pages or authorized cap requires consent again. A sample starts at the first body
page found by the bounded front-of-PDF search, or the first PDF page if no body page
is found there. Quote selection and conversion use the same start.

The historical wire value `review_mode=source_verified` now selects paid pixel
reading under `consent_contract=vision-page-1`. It does not select the earlier
structural proposal/approval pipeline. Historical job summaries remain readable
and are labeled as historical formatting-review jobs.

## No-key and no-paid-reading operation

With no provider key, no shared paid allowance, or paid reading switched off,
the application still uses the vision converter. It keeps clean native text,
reuses compatible cached readings, and discloses original-source images wherever
no safe reading is available. It sends no new model request. It does not silently
substitute an older structural route or publish local OCR as a new model reading.
The page offers this choice explicitly, with zero provider authorization.

## Worker, accounting and publication

Native PDF work runs in an isolated worker with decoded-stream limits and an
absolute deadline. The parent owns provider credentials, consent authorization,
request admission and durable financial settlement. Cancellation stops new work;
unknown billing retains its reservation, while proven-unbilled failures release it.
A durable rejection completes that release after restart. Job-owned preparation
checkpoints are fingerprinted and removed by interrupted-job recovery or retention.

The job result and the embedded report count native pages, model readings and
whole-page source disclosures separately. Confirmed spend, held liability and cap
come from the same ledger. Financial details in the EPUB and its sidecar follow
the report's cost-visibility setting. Job lists and samples stay owner-scoped.

Samples are downloaded without adding a format to the library. Whole-book output
uses the existing validated, transactional publication path. Replacing an existing
EPUB requires explicit JSON-boolean consent. Early worker failures still produce
an owner-visible terminal job result with the reason. Retention removes expired
samples and eligible scratch without erasing unresolved financial evidence.
