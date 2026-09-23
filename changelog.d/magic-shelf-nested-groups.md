### Fixed

- **Smart shelves with rule groups open in the new UI's editor.** A smart shelf built in the classic editor with "Add group" (for example "tag is not X, and tag is A or B") crashed the new UI's edit page with `can't access property "includes"`. The editor now shows each group with its own "all rules / any rule" choice, keeps every group when you save, and can add and remove groups itself. Reported by @vinxa (#2257).
