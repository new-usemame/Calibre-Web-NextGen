import { BookCard } from './BookCard';
import { useBooks, useMe } from '../lib/queries';
import { useT } from '../lib/i18n';
import styles from './MoreByAuthor.module.css';
import { selectedCustomColumns } from '../lib/customColumnDisplay';

const MAX = 12;

/** A full-width strip of other books by the same author, shown below the book
 *  detail two-column layout. Fills the page for sparse (description-less) books
 *  and turns a dead end into a browse surface. Renders nothing when the author
 *  has no other books, so it never leaves an empty heading. Reuses the library's
 *  author-filtered books query — no new endpoint. */
export function MoreByAuthor({ authorId, authorName, excludeBookId, hideActions = false, canRead = false }:
  { authorId: number | string; authorName: string; excludeBookId: number; hideActions?: boolean; canRead?: boolean }) {
  const t = useT();
  const { data } = useBooks({ page: 1, entityKind: 'author', entityId: authorId, sort: 'new' });
  const customColumns = selectedCustomColumns(data?.custom_column_definitions, useMe().data);
  const books = (data?.items ?? []).filter((b) => b.id !== excludeBookId).slice(0, MAX);

  if (books.length === 0) return null;

  const heading = t('More by {name}', { name: authorName });
  return (
    <section className={styles.box} aria-label={heading}>
      <h2 className={styles.title}>{heading}</h2>
      <div className={styles.strip}>
        {books.map((b) => (
          <div className={styles.item} key={b.id}>
            <BookCard book={b} hideActions={hideActions} canRead={canRead} customColumnDefinitions={customColumns} />
          </div>
        ))}
      </div>
    </section>
  );
}
