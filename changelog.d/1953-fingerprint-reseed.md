### Fixed

- **Declared Kobo entitlement payload-schema transitions no longer re-deliver unchanged books or removals.** Replay protection preserves the separate book and archive change clocks, always suppresses byte-identical replays, and delivers same-schema or unproven mismatches; manual merges now advance the Kobo book cursor after adding or replacing a Kobo-visible format, while automatic duplicate merges and conversion recovery advance it after adding one.
