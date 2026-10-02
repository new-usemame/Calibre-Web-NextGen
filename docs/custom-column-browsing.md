# Browse custom columns

Text and enumeration columns can be browsed through the New UI's **Custom Columns** section, Classic Categories and OPDS. A column configured as hierarchical in Calibre's tag-browser preferences appears as a tree, including empty or leaf-only trees. Other columns appear as flat lists: `778.3` is one classification, and selecting it does not include `778.30`. Changing book values does not change a column's browse mode. Existing older libraries without Calibre's preferences table retain their earlier hierarchy detection.

Hierarchical columns appear by default; flat columns start hidden. Follow **Choose visible columns** to your profile and use **Show <column> Section** to enable or hide each column. These are personal display choices, shared by all three interfaces. Saved choices win over defaults. Hiding Categories also hides custom-column browsing. Hidden browse URLs return 404, while book metadata still shows the values as text. An upgrade preserves previously visible Classic columns without permanently hiding flat or empty columns. Their browse mode follows Calibre: to keep dotted categories as a tree, enable that column in Calibre’s tag-browser hierarchy preferences. New accounts resolve the same defaults regardless of how they were created.

Select a value to view its books. A hierarchy category includes books assigned to its descendants, counted once per book; a flat value matches exactly. A slash belongs to the value, and does not create a new hierarchy level. You can place books after the whole list or inside the selected category. Changing category resets pagination. A failed request stays an error and offers **Try again**; it is never described as an empty column.

Advanced Search continues to search text columns using partial matches. Browsing a category is a separate action. Editing hierarchy trees and New UI book-detail breadcrumb links are deferred to separate work; Classic detail links respect your visibility choices. No Calibre schema changes or writes are needed for browsing.

The initial implementation was contributed by @Rol3333 in #2363 and adopted with repairs to defaults, Calibre preference handling, hidden-column responses, loading/paging, keyboard semantics and localization.


Visibility choices use the existing per-column-ID profile contract. If an administrator switches to a different Calibre library, review those choices because reused column IDs can represent different fields. The compatibility upgrade preserves that contract; it does not copy preferences to Calibre or create a new library-scoped account model. A startup with no configured library completes without saving column choices, so a later library uses its current defaults. An unavailable configured library retries preservation when it becomes readable.

The compatibility upgrade records the highest existing account ID before serving account creation. If the library is temporarily unavailable, a later retry preserves only those older accounts; accounts created meanwhile keep live Calibre defaults. That boundary and the completion flag are stored in app.db, and the upgrade never changes Calibre metadata.

Administrators can set the same per-column choices on the existing user editor, including Guest when anonymous browsing is enabled. Guest choices apply to anonymous Classic, New UI and OPDS readers; each signed-in reader keeps their own choices.

A saved Guest choice takes precedence over anonymous browser preferences, including choices preserved during upgrade. Without a saved choice, anonymous browser preferences and the normal Calibre defaults still apply. After adding columns in Calibre, use the existing library schema refresh/restart before browsing. Ordinary slash values such as `AC/DC` work; leading or doubled slashes and dot-segment values remain limited by Classic and OPDS path URLs. Reload an old cached profile form before saving column choices.
