### Fixed

- **Smart shelves keep the same name across both interfaces, OPDS and sync settings.** Classic uses the existing localized Smart shelves term, matching the New UI. A short note explains the older Magic Shelves name; saved rules, API routes and sync settings retain their identifiers. Updated labels and help ship with all 28 translations. Reported by Aoife in #1498.

### Security

- **Smart-shelf names and icons display as text in Classic headings.** Escape stored values before inserting them into the existing HTML heading, preventing shelf content from creating HTML elements.
