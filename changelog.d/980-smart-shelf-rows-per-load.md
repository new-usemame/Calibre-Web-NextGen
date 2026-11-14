### Fixed

- **Smart shelves now load the number of rows you choose in the Library's "Rows per load" setting.** Smart shelves already follow the Library's density, but their paging still used the server's fixed page size. They now measure their own columns and ask for whole rows, like the Library and regular shelves, so 1 row per load loads one row at a time there too. The Smart shelf endpoint also keeps a requested page size between 1 and 200, as the author and series lists do (#980).
