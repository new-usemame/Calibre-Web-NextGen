### Fixed

- **Book sources could create a duplicate of an unchanged EPUB when only its ordinary package locator was serialized differently.** Requests now retain the original record, archive and reading state when the single-rootfile locator is equivalent and every publication resource is unchanged. Receipts preserve the new source hash and actual retained hash; ambiguous locators and changed editions stay separate.
