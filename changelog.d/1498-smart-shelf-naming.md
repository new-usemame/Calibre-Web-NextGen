### Fixed

- **Smart shelves keep the same name across both interfaces, OPDS, statistics and sync settings.** Classic uses the existing localized Smart shelves term, matching the New UI. A short note explains the older Magic Shelves name; saved rules, API routes, statistics event keys and sync settings retain their identifiers. Updated labels and help ship with all 28 translations. Reported by Aoife in #1498. Classic rule selectors have accessible names, and quick-pick icons work with the keyboard. Password-strength messages fall back to a shipped locale without requesting missing language files.

### Security

- **Smart-shelf names and icons display as text in Classic headings and profile ordering.** Escape stored heading values and create profile display nodes as text, preventing shelf content from creating HTML elements. Activity history retains the shared statistics escaping helper already on main.
