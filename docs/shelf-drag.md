# Add books to a shelf from the New UI grid

Drag a book card onto a shelf in the sidebar. The sidebar opens while you drag, including on phones. Writable shelves show a drop border, and the shelf under the pointer is highlighted. Drop to add the book; its existing shelf memberships stay in place.

To add several books, use **Select**, choose the books, and drag one of the selected cards. The drag carries the whole current selection. Dragging an unselected card adds that book alone. The selected books stay selected after a successful add, so you can add them to another shelf. Wait until **Select all** finishes loading before dropping; a drop while selection is still loading is cancelled.

On a touchscreen, drag the small grip below the card. This handle leaves ordinary card and page scrolling available. Tap the handle, or focus it and press Enter or Space, to choose a shelf in a dialog instead. The existing **Add to shelf** bulk action and book-detail controls also remain available.

Press Escape or cancel the drag to leave shelf membership unchanged. A drop onto a writable shelf starts the operation; navigating away afterward does not undo already started writes. A result is announced when the operation finishes. If some books fail, the picker shows the server's reason and keeps only the failed books selected for retry. Leaving the current page or search, or saving a different Discover source, closes its picker and prevents a late failure from replacing selection in the new view. Already started writes still finish; changing the source does not undo them.

Only ordinary shelves you can edit accept drops. Your private shelves qualify; another reader's public shelf requires permission to edit public shelves. Smart shelves continue to determine their books from their rules, so they are not drop targets. Adding a book that is already on a shelf succeeds without creating another membership. Other permission or library-membership errors remain failures.
