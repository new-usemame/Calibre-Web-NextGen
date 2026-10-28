### Fixed

- **Deleting a book no longer stalls a large library.** Every whole-book delete rebuilt the entire duplicate list inside the request, and a bulk delete did that once per book, which could leave the server unresponsive until a restart. A delete now recomputes only the duplicate groups the deleted books belonged to, once per delete or once per bulk selection, and the Duplicates page still drops the deleted copies right away. Reported by @kanjieater (#2751).
