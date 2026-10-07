### Added
- **Bulk "Add books" on shelves in the new UI:** The shelf page now has an "Add books" button that opens a searchable picker. Tick books across several searches, then add them all at once. Books already on the shelf are shown but disabled, and any that fail stay selected for retry. Matches the classic UI's shelf modal, backed by a new `GET /api/v1/shelves/<id>/available-books` endpoint.
