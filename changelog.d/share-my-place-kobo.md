### Changed

- **Kobo devices can be given their own place and read status.** Per-device place and status controls are behind a rollout switch that is off by default. Nothing about how places or read status sync changes until the switch is turned on.
- **Checked Kobos can open at KOReader's exact place**, including a move back, when they hold the current KEPUB and the books align. Otherwise they retain their existing Location with the shared percentage; opening and closing there does not rewind other devices or undo Finished status. Real Kobo reading, including backward moves, reaches KOReader. These behaviours remain off until the rollout switch is enabled.
