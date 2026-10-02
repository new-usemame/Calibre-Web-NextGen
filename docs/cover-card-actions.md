# Book-card actions

Book cards offer a single Actions button on the cover. It appears on mouse hover or keyboard focus, and stays visible on touch screens. Opening it shows a dialog near the cover on desktop and an action sheet on phones. The metadata below the cover has no shortcut row.

The dialog offers Read now and Edit when the account has permission, plus personal favorite and read-status actions for books in the account's library. A favorite star on the cover reflects the current account. On a shelf, the same panel includes Remove from shelf for accounts allowed to change that shelf. Books outside My Library retain their Add action.

The current reading state is shown as Unread, Reading or Read, using the same server fields as the cover badge. Mark as read finishes an unread or currently reading book. Mark as unread uses the existing endpoint, including its existing reading-position reset. Reading is established by a reader or device sync; this panel does not manufacture a reading position or cycle through a synthetic middle state.

After a read or favorite change, the catalog rebuilds from its first page so Favorites, Read/Unread and saved default filters show their current membership and count. This can reset pagination and scroll; replacing only the active page would leave holes when removing a book shifts later page boundaries. If the card disappears, keyboard focus moves to the list heading. Escape and ordinary dismissal restore focus to the cover button.

View settings' Show Read now and edit buttons preference controls the entire cover disclosure. Turning it off removes its button from the DOM. Selection mode also removes the disclosure while preserving the card's one selection toggle. Favorite and read-state badges remain available through their existing display preferences.

On covers at most 126px wide, status badges use compact icons and shelf tags collapse to one count. Their complete accessible names remain available; wider covers retain visible labels. This keeps the disclosure and badges within a 68px dense cover without covering the metadata.
