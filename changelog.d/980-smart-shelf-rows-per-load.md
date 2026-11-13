### Fixed

- **Smart shelves now load the number of rows you choose in the Library's "Rows per load" setting.** After Dense and Compact started applying to Smart shelves, their paging still used the server's fixed page size. They now measure their own columns and ask for whole rows, like the Library and regular shelves, so 1 row per load loads one row at a time there too (#980).
