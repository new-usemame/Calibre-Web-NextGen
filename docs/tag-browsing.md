# Browsing built-in tags

The New UI, Classic Tags and OPDS use Calibre’s **built-in Tags** hierarchy preference. In Calibre, open **Preferences → Look & feel → Tag browser → Hierarchy and searching** and select **Tags** for hierarchical display. A library without that preference remains flat. A dotted name alone does not enable hierarchy browsing.

For example, `Horror.Gothic.Southern Gothic` appears beneath **Horror → Gothic → Southern Gothic**. Selecting **Gothic** includes books assigned directly to it and to all of its descendants. A book assigned to several matching tags is counted once. Spaces around components are ignored for browsing, while the raw stored tag names remain unchanged. A slash, percent sign or underscore belongs to its component and does not act as a path separator or wildcard.

In the New UI and Classic, the arrow button expands children and the tag-group name opens its books. **All tags** opens the existing flat list for exact tag browsing and maintenance. Synthetic parent tag groups are browse groups, not editable tag records; rename and delete continue to operate on the actual stored tags. In OPDS, a parent feed includes an **All books in this tag group** entry alongside its subtags. Select that entry to acquire books from the whole subtree, including books assigned directly to the parent.

Counts and results retain the reader’s language, tag restrictions, hidden and archived state, and personal-library scope. OPDS additionally honors selected-shelf exposure. A parent does not grant access to books that those ordinary restrictions exclude. Sorting, pagination, Select all and book-list file export use the selected tag group’s membership.

An unreadable or malformed hierarchy preference produces an unavailable response so the reader can retry. It does not silently replace the configured tree with an empty list. Stored tags with empty components, more than 64 components, or a canonical path longer than 4096 characters remain available as exact tag records instead of creating an unbrowsable tree. This limit does not rewrite metadata.

[Custom text and enumeration columns](custom-column-browsing.md) have their own Calibre hierarchy choices. This feature does not change those preferences, write the Calibre database or enable hierarchy editing.
