### Fixed

- **Book pages offer only the shelf changes you can make.** The classic book page's "Remove from shelf" menu listed public shelves to readers without the "Edit public shelves" role, whose removal the server then refused, and it appeared with nothing in it when only another reader's private shelf held the book. It now lists the shelves you can change and is hidden when there are none. In the new UI, the Add to shelf menu and the bulk bar no longer offer a public shelf you made before an admin took that role away, which the server also refuses.
