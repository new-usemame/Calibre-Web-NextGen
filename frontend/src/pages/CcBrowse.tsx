import { useEffect, useState, type ReactNode } from 'react';
import { Link, useSearch } from 'wouter';
import {
  ChevronLeft, ChevronRight, ChevronRight as Chevron,
  Folder, FolderOpen,
} from 'lucide-react';
import { useColumns, useCcTree, useCcBooks, useMe } from '../lib/queries';
import type { CcNode } from '../lib/api';
import { BookCard } from '../components/BookCard';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import { canReadBooks } from '../lib/permissions';
import { usePersistentBool } from '../lib/usePersistentBool';
import styles from './CcBrowse.module.css';

/** True when `selected` is `nodePath` itself or a deeper descendant, so the
 *  ancestors of the active node render expanded (mirrors the classic tree's
 *  `current_path.startswith(node.path)` server-side open state). */
function isAncestorPath(selected: string, nodePath: string): boolean {
  return !!selected && (selected === nodePath || selected.startsWith(nodePath + '.'));
}

/** One tree node. Three visual states, mirroring a file browser:
 *
 *    ▾ FolderOpen   expandable node, expanded
 *    ▸ Folder       expandable node, collapsed
 *    • FileText     leaf node — a plain row, never a fake expandable control
 *
 *  Expandable nodes keep the native <details>/<summary> behaviour: clicking the
 *  row toggles expansion, clicking the category name navigates (the <a> is the
 *  click's activation target, so navigation wins and the toggle does not fire).
 *  When `booksSlot` is provided (after-children mode) it renders as the last
 *  row of this node's content — after its children, before its siblings — for
 *  the selected node only, so the books visually belong to it. */
function TreeNode({ node, colId, selected, booksSlot }: {
  node: CcNode; colId: string; selected: string; booksSlot: ReactNode;
}) {
  const t = useT();
  const hasChildren = node.children.length > 0;
  const isSelected = selected === node.path;
  const href = `/cc/${colId}?path=${encodeURIComponent(node.path)}`;
  const active = isSelected;
  const link = (
    <Link href={href}
      className={active ? `${styles.nodeLink} ${styles.nodeLinkActive}` : styles.nodeLink}
      aria-current={active ? 'page' : undefined}>
      {node.name}
    </Link>
  );
  const count = (
    <span className={styles.badge} aria-label={t('{count} books', { count: node.total_count })}>
      {node.total_count}
    </span>
  );

  // Leaf: a plain row — no <details>, no chevron, nothing pretend-expandable.
  // Its books (when selected) follow immediately inside the same <li>, which
  // sits at the parent's content level — exactly the leaf's own level.
  if (!hasChildren) {
    return (
      <li className={styles.treeNode}>
        <div className={styles.treeSummary}>
          <span className={styles.chevronPlaceholder} aria-hidden="true" />
          <span className={styles.leafMarker} aria-hidden="true">•</span>
          {link}
          {count}
        </div>
        {isSelected && booksSlot}
      </li>
    );
  }

  return (
    <li className={styles.treeNode}>
      <details open={isAncestorPath(selected, node.path)}>
        <summary className={styles.treeSummary}>
          <Chevron size={14} className={styles.chevron} aria-hidden="true" focusable={false} />
          {active
            ? <FolderOpen size={15} className={styles.nodeIcon} aria-hidden="true" focusable={false} />
            : <Folder size={15} className={styles.nodeIcon} aria-hidden="true" focusable={false} />}
          {link}
          {count}
        </summary>
        <ul className={styles.children}>
          {node.children.map((child) => (
            <TreeNode key={child.path} node={child} colId={colId} selected={selected} booksSlot={booksSlot} />
          ))}
          {isSelected && booksSlot && (
            <li className={styles.booksInset}>{booksSlot}</li>
          )}
        </ul>
      </details>
    </li>
  );
}

/** The tree for one column, or a loading/empty state. */
function ColumnTree({ colId, selected, booksSlot }: {
  colId: string; selected: string; booksSlot: ReactNode;
}) {
  const t = useT();
  const { data, isLoading } = useCcTree(colId);
  if (isLoading) return <SpinnerCentered size={40} />;
  if (!data || data.nodes.length === 0) {
    return <EmptyState title={t('No values in this column yet')}
      message={t('Books tagged with values in this column will appear here.')} />;
  }
  return (
    <ul className={styles.tree} role="tree">
      {data.nodes.map((node) => (
        <TreeNode key={node.path} node={node} colId={colId} selected={selected} booksSlot={booksSlot} />
      ))}
    </ul>
  );
}

/** Books under the selected node, paged. An empty `path` lists every book
 *  carrying any value in the column.
 *
 *  variant="section" — the standalone block below the tree (after-tree mode).
 *  variant="inset"   — the compact block inserted inside the tree under the
 *                      selected node's children (after-children mode): a
 *                      "Books" divider instead of a section heading, so it
 *                      reads as content belonging to the node, not as another
 *                      hierarchy level. */
function NodeBooks({ colId, path, variant }: { colId: string; path: string; variant: 'section' | 'inset' }) {
  const t = useT();
  const me = useMe().data;
  const [page, setPage] = useState(1);
  const { data, isLoading } = useCcBooks(colId, path, page);

  // Reset paging when the node changes.
  useEffect(() => { setPage(1); }, [colId, path]);

  const total = data?.total ?? 0;
  const perPage = data?.per_page ?? 24;
  const lastPage = Math.max(1, Math.ceil(total / perPage));

  const body = isLoading ? <SpinnerCentered size={40} /> : (data?.items.length ?? 0) === 0 ? (
    <EmptyState title={t('No books here yet')}
      message={t('Try a different category or page.')} />
  ) : (
    <>
      <div className={styles.bookGrid}>
        {data!.items.map((book) => (
          <BookCard key={book.id} book={book} canRead={canReadBooks(me)} />
        ))}
      </div>
      {lastPage > 1 && (
        <nav className={styles.pager} aria-label={t('Pagination')}>
          <button type="button" className={styles.pageButton} disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}>
            <ChevronLeft size={16} aria-hidden="true" focusable={false} />
            {t('Previous')}
          </button>
          <span className={styles.pageInfo}>
            {t('Page {page} of {last}', { page, last: lastPage })}
          </span>
          <button type="button" className={styles.pageButton} disabled={page >= lastPage}
            onClick={() => setPage((p) => Math.min(lastPage, p + 1))}>
            {t('Next')}
            <ChevronRight size={16} aria-hidden="true" focusable={false} />
          </button>
        </nav>
      )}
    </>
  );

  if (variant === 'inset') {
    return (
      <div className={styles.insetBooks}>
        <div className={styles.insetDivider} role="separator" aria-label={t('Books')}>
          <span className={styles.insetDividerLabel}>{t('Books')}</span>
        </div>
        {body}
      </div>
    );
  }

  return (
    <section className={styles.booksSection} aria-label={t('Books')}>
      <div className={styles.booksHeader}>
        <h2 className={styles.booksTitle}>{path || t('All')}</h2>
        <span className={styles.count}>{t('{count} books', { count: total })}</span>
      </div>
      {body}
    </section>
  );
}

/** /cc — the list of browsable custom columns; /cc/:id — one column's tree
 *  plus the books under the selected node (?path=). The SPA counterpart of
 *  the classic UI's /custom_column/<id>[/<path>] views.
 *
 *  The books-placement control chooses between `after-tree` (books below the
 *  whole tree) and `after-children` (books inserted inside the tree right
 *  after the selected node's children, before its siblings). It persists via
 *  the SPA's existing localStorage-preference mechanism. */
export function CcBrowse({ id }: { id?: string }) {
  const t = useT();
  const search = useSearch();
  const path = new URLSearchParams(search).get('path') ?? '';
  const [afterChildren, setAfterChildren] = usePersistentBool('cwng:cc-books-after-children', false);
  // Hook order: both hooks run on both modes; only the relevant one is enabled.
  const columns = useColumns(!id);
  const tree = useCcTree(id ?? '', !!id);

  // Column list mode: every browsable column as a card.
  if (!id) {
    return (
      <main className={styles.container}>
        <div className={styles.header}>
          <h1 className={styles.title}>{t('Custom Columns')}</h1>
          {!!columns.data?.items.length && (
            <span className={styles.count}>
              {t('{count} columns', { count: columns.data.items.length })}
            </span>
          )}
        </div>
        {columns.isLoading ? <SpinnerCentered size={40} /> : (columns.data?.items.length ?? 0) === 0 ? (
          <EmptyState title={t('No custom columns to browse')}
            message={t('Tag-like custom columns defined in the library appear here.')} />
        ) : (
          <ul className={styles.grid}>
            {columns.data!.items.map((col) => (
              <li key={col.id}>
                <Link href={`/cc/${col.id}`} className={styles.columnCard}>
                  <span className={styles.columnName}>{col.name}</span>
                  {col.hierarchical && (
                    <span className={styles.badge}>{t('Tree')}</span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    );
  }

  // In after-children mode the selected node's books render inside the tree;
  // the slot is handed down and each TreeNode renders it when it is the
  // selected one. One books query feeds either placement (§9 of the guide).
  const booksSlot = afterChildren && path
    ? <NodeBooks colId={id} path={path} variant="inset" />
    : null;

  // Column mode: toolbar + tree + books.
  return (
    <main className={styles.container}>
      <Link href="/cc" className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" focusable={false} />
        {t('Custom Columns')}
      </Link>
      <div className={styles.header}>
        <h1 className={styles.title}>{t('Browse by Column')}</h1>
        <div className={styles.toolbar}>
          <span className={styles.toolbarLabel} id="cc-books-placement-label">
            {t('Books placement')}
          </span>
          <div className={styles.viewToggle} role="group" aria-labelledby="cc-books-placement-label">
            <button type="button" aria-pressed={!afterChildren}
              onClick={() => setAfterChildren(false)}>
              {t('After tree')}
            </button>
            <button type="button" aria-pressed={afterChildren}
              onClick={() => setAfterChildren(true)}>
              {t('After children')}
            </button>
          </div>
        </div>
      </div>
      <p className={styles.hint}>
        {t('Select a category to see the books assigned to it and all of its sub-categories.')}
      </p>
      {tree.isLoading ? <SpinnerCentered size={40} /> : (
        <ColumnTree colId={id} selected={path} booksSlot={booksSlot} />
      )}
      {!afterChildren && <NodeBooks colId={id} path={path} variant="section" />}
    </main>
  );
}