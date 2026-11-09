### Added

- **A KOSync progress push now hears whether it was kept.** A push behind the stored place, or an older report arriving late, was dropped with the same 200 reply as one that was saved, so a client moving back on purpose could not tell the move did not land. The reply now carries `accepted`, `shared` (false when that device's Share my place is off, so the place stays on the device) and, when the push was not kept, `kept_percentage` with the place kept instead. Stock KOReader ignores the new fields. (#2843)
