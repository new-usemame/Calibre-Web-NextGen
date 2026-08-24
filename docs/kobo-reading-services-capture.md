# Kobo Reading Services private exchange capture

This diagnostic is for short, operator-controlled hardware experiments. It is
off by default and cannot be enabled with an ordinary boolean value.

Enable it by setting this exact environment value and restarting CWNG:

```text
CWNG_KOBO_READING_SERVICES_CAPTURE=I_UNDERSTAND_THIS_CAPTURES_PRIVATE_READING_DATA
```

Unset the variable and restart immediately after the experiment. Values such as
`1`, `true`, different case, or values with surrounding whitespace do not
enable capture.

Records are written to:

```text
<config>/.cwng-private-observability/kobo-reading-services/
```

In the standard container `<config>` is `/config`. The directory is mode 0700
and each `exchange-*.json.gz` record is mode 0600. Records are not copied into
the annotation backup format or the support debug ZIP, and no record belongs in
a repository, issue attachment, or other shared artifact. External backup jobs
that archive all of `/config` should explicitly exclude
`.cwng-private-observability/`.

Each schema-version-2 record contains:

- explicit request provenance (`authenticated`, `unauthenticated`, or
  `not_recorded`) and a local user ID only when one was authenticated;
- the device request body and redacted headers;
- `checkforchanges` decisions in original array order, including ownership,
  observed authority state, and whether each ID was suppressed or proxied;
- the exact request body actually sent to Kobo after filtering;
- Kobo's raw response body and redacted headers; and
- the final status, redacted headers, and exact body returned to the device.

Bodies carry byte length and SHA-256 metadata. UTF-8 bodies are stored directly;
any non-UTF-8 body is base64 encoded. Credential-like headers—including
Authorization, cookies, Kobo user keys, API keys, secrets, and tokens—are
replaced with `***REDACTED***` in every leg.

Retention is automatic and cross-process locked: at most 256 records, 64 MiB
compressed total, seven days, and 16 MiB for any individual body. An exchange
above the body limit is skipped whole rather than saved partially. Blocking
capture storage runs outside the gevent hub with a 100 ms request deadline.
Any observer or storage failure is logged only with structural metadata and
cannot replace, retry, or change the response being observed.

### Unauthenticated annotation PATCH refusals

When the private-data gate is enabled, an annotation PATCH refused with 401 is
captured before the refusal. Its record is explicitly marked
`authentication: unauthenticated` and `user_id: null`; the route does not guess
an owner from entitlement IDs, device headers, cookies, or request metadata.
The request is never admitted to the recovery spool because there is no safe
replay principal and unresolved spool records are intentionally non-evictable.

This unauthenticated diagnostic has a separate directory and retention budget:

```text
<config>/.cwng-private-observability/kobo-reading-services-unauthenticated/
```

It is capped at 32 records, 8 MiB compressed total, 24 hours, and 1 MiB for an
individual body. Requests without a bounded `Content-Length` or beyond 1 MiB
are refused normally but not read or captured. The diagnostic remains off by
default; when it is off, the auth gate does not consume the request body.
Unauthenticated churn therefore cannot evict authenticated exchange captures
or any always-on recovery record.

## Annotation PATCH recovery spool

Independently of the opt-in observer, every annotation PATCH body is staged to
durable local storage before JSON parsing, ownership dispatch, or annotation
persistence begins. This is an always-on data-integrity mechanism, not a
diagnostic gate. Its records live at:

```text
<config>/.cwng-private-observability/kobo-patch-spool/
```

The spool stores the exact raw body as base64 plus its byte length and SHA-256,
the entitlement/user/origin-device identifiers needed to route a controlled
replay, and one processing outcome: `staged`, `dispatch_exception`, or
`dispatch_completed`. It never stores request headers. A completed record is
retained too because completion of the route does not prove that every member
commit succeeded. Replay is deliberately a server-side operator action; CWNG
does not automatically reapply a PATCH or risk applying the same delta twice.
`cps.services.kobo_patch_spool.iter_replay_candidates()` identifies definitely
interrupted records and `load_spooled_patch(path)` verifies the stored length
and digest before returning the original bytes.

The spool uses a mode-0700 private parent and mode-0600 gzip records. Before a
stage is reported successful, the record and every newly created directory
entry have been fsynced. Blocking storage work runs outside the gevent hub and
the request waits at most 100 ms; timeout, lock contention, or any storage
failure degrades to no new recovery record without changing the PATCH route.

It is capped at 512 files, 64 MiB compressed total, 14 days, and 16 MiB per
PATCH body. `staged` and `dispatch_exception` records are protected: when the
only way to admit a new body would be to remove one, the new body is not
staged. Evictable completed records are moved through a durable transaction so
a failed new write restores the prior record set. A deadline-driven maintenance
worker enforces the age limit even while no new PATCHes arrive. An oversized
body or any storage failure is reported without body content and cannot change
parsing, dispatch, the existing ownership-unknown 503, or the upstream response
returned to the Kobo. The same repository, support-bundle, and external-backup
exclusions described above apply.
