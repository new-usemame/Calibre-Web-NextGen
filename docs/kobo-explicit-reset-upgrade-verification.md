# Kobo explicit reset versus historical upgrade evidence

Reviewed and fixed from assembled cb25b58f73 on 2026-09-08. Independent review of 4f0078c60b and 802004edef found a recovery interaction introduced by pending-release #2025 (0e3b86005a).

Full Sync deleted delivery rows and the classification marker but kept device reading positions. The next historical migration used those positions to recreate a `legacy-position-receipt` row, so an explicitly reset reader received ChangedEntitlement. A reset/empty reader can drop that message instead of acquiring its book. Per-book resend before the first classification pass had the same interaction.

Full Sync now leaves/creates a current classification boundary with empty delivery ledgers. Per-book resend first applies the ordinary historical classification policy, then removes the requested book's delivery evidence. Neither operation deletes reading positions; unrelated books and other accounts retain their existing semantics. Ordinary upgrades still retain proven delivery rows and rearm ambiguous household guesses.

Behavioral evidence: tests/unit/test_kobo_upgrade_reset_behavior.py restores the actual v4.1.43 tagged emitted database, records a device-authored 42% position, invokes real administrative recovery then real HandleSyncRequest. Full Sync and per-book resend, with and without an existing seed marker, all emitted Changed on original code (four seen-red failures), and now emit New while retaining the position. Independent equal-clock provenance, repeated schema/classification migration, and failed response-commit/retry cases also pass. Normal tagged upgrades remain silent.

Validation: main Kobo sync, tagged fixtures, and new behavior suite 115 passed, 1 existing strict xfail; forensic/archive/admin resend suites 27 passed. The expected failure is the already documented pre-v4.1.43 no-device-ledger compatibility gap; this fix does not invent missing historical provenance. Physical reader verification remains the integration owner's hardware gate.
