import { useEffect, useId, useState, type ReactNode } from 'react';
import { Link, useSearch } from 'wouter';
import { ChevronLeft, ChevronRight, Folder, FolderOpen, Tag } from 'lucide-react';
import { useColumns, useCcTree, useCcBooks, useMe } from '../lib/queries';
import { apiUrl, type CcNode } from '../lib/api';
import { BookCard } from '../components/BookCard';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import { SectionError } from '../components/SectionError';
import { useCardActionsHidden } from '../lib/useCardActionsHidden';
import { useReadingTagsHidden } from '../lib/useReadingTagsHidden';
import { useShelfBadgesHidden } from '../lib/useShelfBadgesHidden';
import { clampPage } from '../lib/pagination';
import { canReadBooks } from '../lib/permissions';
import { usePersistentBool } from '../lib/usePersistentBool';
import styles from './CcBrowse.module.css';

/** True when `selected` is `nodePath` itself or a deeper descendant, so the
 *  ancestors of the active node render expanded (mirrors the classic tree's
 *  server-side open state). */
function isAncestorPath(selected: string, nodePath: string): boolean {
  return !!selected && (selected === nodePath || selected.startsWith(nodePath + '.'));
}

/** One tree node. Three visual states, mirroring a file browser:
 *
 *    ▾ FolderOpen   expandable node, expanded
 *    ▸ Folder       expandable node, collapsed
 *    •              leaf node — a plain row, never a fake expandable control
 *
 *  Expandable nodes have separate native buttons and links: the button
 *  toggles the children, while the category name opens its books.
 *  When `booksSlot` is provided (after-children mode) it renders as the last
 *  row of this node's content — after its children, before its siblings — for
 *  the selected node only, so the books visually belong to it. */
function TreeNode({ node, colId, selected, booksSlot }: {
  node: CcNode; colId: string; selected: string; booksSlot: ReactNode;
}) {
  const t = useT();
  const hasChildren = node.children.length > 0;
  const childrenId = useId();
  const [expanded, setExpanded] = useState(() => isAncestorPath(selected, node.path));
  useEffect(() => {
    if (isAncestorPath(selected, node.path)) setExpanded(true);
  }, [selected, node.path]);
  const isSelected = selected === node.path;
  const href = `/cc/${colId}?path=${encodeURIComponent(node.path)}`;
  const link = (
    <Link href={href}
      className={isSelected ? `${styles.nodeLink} ${styles.nodeLinkActive}` : styles.nodeLink}
      aria-current={isSelected ? 'page' : undefined}>
      {node.name}
    </Link>
  );
  const count = (
    <span role="img" className={styles.badge} aria-label={t(node.total_count === 1 ? '{count} book' : '{count} books', { count: node.total_count })}>
      {node.total_count}
    </span>
  );

  // Leaf: a plain row with no disclosure control.
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
      <div className={styles.treeSummary}>
        <button type="button" className={styles.disclosure} aria-expanded={expanded}
          aria-controls={childrenId} aria-label={node.name}
          onClick={() => setExpanded(value => !value)}>
          <ChevronRight size={14} className={expanded ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron}
            aria-hidden="true" focusable={false} />
        </button>
        {isSelected
          ? <FolderOpen size={15} className={styles.nodeIcon} aria-hidden="true" focusable={false} />
          : <Folder size={15} className={styles.nodeIcon} aria-hidden="true" focusable={false} />}
        {link}
        {count}
      </div>
      <ul id={childrenId} className={styles.children} hidden={!expanded}>
        {node.children.map(child => (
          <TreeNode key={child.path} node={child} colId={colId} selected={selected} booksSlot={booksSlot} />
        ))}
        {isSelected && booksSlot && <li className={styles.booksInset}>{booksSlot}</li>}
      </ul>
    </li>
  );
}

function RequestError({ title, error, retry, retrying }: {
  title: string; error: unknown; retry: () => void; retrying: boolean;
}) {
  const t = useT();
  const message = error instanceof Error ? error.message : t('An unexpected error occurred');
  return <section className={styles.requestError}>
    <h2>{title}</h2>
    <SectionError message={message} onRetry={retry} retrying={retrying} />
  </section>;
}

function ValuesList({ colId, selected, booksSlot }: {
  colId: string; selected: string; booksSlot: ReactNode;
}) {
  const t = useT();
  const { data, isLoading, isFetching, isError, error, refetch } = useCcTree(colId);
  if (isLoading) return <SpinnerCentered size={40} />;
  if (isError) return <RequestError title={t('Could not load column')} error={error} retrying={isFetching} retry={() => { void refetch(); }} />;
  if (!data || data.nodes.length === 0) {
    return <EmptyState title={t('No values in this column yet')}
      message={t('Books assigned a value in this column will appear here.')} />;
  }
  // Native lists/buttons keep Tab and Space behavior without claiming the
  // arrow-key tree-widget contract. Flat values simply have no children.
  return <ul className={styles.tree}>
    {data.nodes.map(node => <TreeNode key={node.path} node={node}
      colId={colId} selected={selected} booksSlot={booksSlot} />)}
  </ul>;
}

/** Books under the selected node, paged. An empty `path` lists every book
 *  carrying any value in the column.
 *
 *  Two failure modes this has to survive, both from holding the page in
 *  component state while the node lives in the URL:
 *
 *  - The page belongs to the node it was requested for. `NodeBooks` stays
 *    mounted when `path` changes (only the query key changes), so page 3 of a
 *    large node used to carry over to a one-page node. The result was an empty
 *    grid with NO pager at all — `last > 1` was false, so the Previous button
 *    that would have walked the page back was not rendered either, leaving a
 *    full reload as the only escape. The reset below puts the page back to 1
 *    whenever the node changes; the clamp then self-heals a page that is out of
 *    range for any other reason (a stale link, a result set that shrank).
 *  - A failed request is not an empty result. `useCcBooks` rejects on any
 *    non-2xx, and reading only `data` turned a 404 (or a 403, or a 500 from a
 *    locked metadata.db) into "No books here yet", hiding the API's own error
 *    message. The error is surfaced instead. */
function NodeBooks({ colId, path, variant }: {
  colId: string; path: string; variant: 'section' | 'inset';
}) {
  const t = useT();
  const [page, setPage] = useState(1);
  // Render-phase state adjustment (React re-renders immediately, before
  // paint, with no wasted fetch) rather than an effect, so the new node is
  // never shown carrying the previous node's page.
  const queryId = `${colId}\u0000${path}`;
  const [lastQueryId, setLastQueryId] = useState(queryId);
  if (lastQueryId !== queryId) {
    setLastQueryId(queryId);
    setPage(1);
  }
  const { data, isLoading, isFetching, isPlaceholderData, isError, error, refetch } = useCcBooks(colId, path, page);
  const { data: me } = useMe();
  const [cardActionsHidden] = useCardActionsHidden();
  const [readingTagsHidden] = useReadingTagsHidden();
  const [shelfBadgesHidden] = useShelfBadgesHidden();
  const total = data?.total ?? 0;
  const perPage = data?.per_page || 24;
  const last = Math.max(1, Math.ceil(total / perPage));
  // Shared clamp, not a local Math.min: this is the same "the page you were
  // reading no longer exists" case the other paginated surfaces handle, and
  // one helper is one place to keep correct.
  const clamped = clampPage(page, last);
  useEffect(() => {
    if (data && !isPlaceholderData && String(data.column.id) === colId && data.path === path && clamped !== page) setPage(clamped);
  }, [clamped, page, data, isPlaceholderData, path, colId]);
  if (isLoading || (isPlaceholderData && (data?.path !== path || String(data?.column.id) !== colId))) return <SpinnerCentered size={40} />;
  if (isError) return <RequestError title={t('Could not load books')} error={error} retrying={isFetching} retry={() => { void refetch(); }} />;
  const items = data?.items ?? [];
  if (total === 0) {
    return <EmptyState title={t('No books here yet')}
      message={t('Nothing in this column matches the selected value.')} />;
  }
  const body = (
    <>
      <div className={styles.sectionHeader}>
        <span className={styles.sectionTitle}>
          {path || t('All')}
        </span>
        <span className={styles.count}>{t(total === 1 ? '{count} book' : '{count} books', { count: total })}</span>
      </div>
      {items.length === 0 ? (
        <EmptyState title={t('Nothing on this page')}
          message={t('This node has fewer pages than the one you were reading.')} />
      ) : (
        <ul className={variant === 'inset' ? styles.insetGrid : styles.grid}>
          {items.map((book) => <li key={book.id}><BookCard book={book} canRead={canReadBooks(me)} quickEdit={!!me?.role.edit} hideActions={cardActionsHidden} hideReadingTags={readingTagsHidden} hideShelfTags={shelfBadgesHidden} /></li>)}
        </ul>
      )}
      {last > 1 && (
        <nav className={styles.pager} aria-label={t('Pagination')}>
          <button type="button" disabled={clamped <= 1 || isFetching}
            onClick={() => setPage((p) => Math.max(1, p - 1))}>
            <ChevronLeft size={16} aria-hidden="true" focusable={false} /> {t('Previous')}
          </button>
          <span aria-current="page">{t('Page {page} of {pages}', { page: clamped, pages: last })}</span>
          <button type="button" disabled={clamped >= last || isFetching}
            onClick={() => setPage((p) => Math.min(last, p + 1))}>
            {t('Next')} <ChevronRight size={16} aria-hidden="true" focusable={false} />
          </button>
        </nav>
      )}
    </>
  );
  return variant === 'inset'
    ? <div className={styles.insetBooks}><div className={styles.insetDivider} role="separator" />{body}</div>
    : <section className={styles.section}>{body}</section>;
}

/** /cc — the list of browsable custom columns; /cc/:id — one column's values
 *  plus the books under the selected node (?path=). The SPA counterpart of
 *  the classic UI's /custom_column/<id>[/<path>] views.
 *
 *  A column is hierarchical or flat, and the `hierarchical` flag from the
 *  server decides which of the two presentations is used. The difference is
 *  not cosmetic: a flat column's values are atomic, so rendering them as a
 *  tree would invent a `778` parent that the cataloger never entered.
 *
 *  The books-placement control chooses between `after-tree` (books below the
 *  whole list/tree) and `after-children` (books inserted inside the tree right
 *  after the selected node's children, before its siblings). It persists via
 *  the SPA's existing localStorage-preference mechanism. */
export function CcBrowse({ id }: { id?: string }) {
  const t = useT();
  const search = useSearch();
  const path = new URLSearchParams(search).get('path') ?? '';
  const [afterChildren, setAfterChildren] = usePersistentBool('cwng:cc-books-after-children', false);
  // Hook order: both hooks run on both modes; only the relevant one is enabled.
  const { data: me } = useMe();
  const columns = useColumns(!id);
  const tree = useCcTree(id ?? '', !!id);
  const isHierarchical = tree.data?.column.hierarchical !== false;

  // Column list mode: every browsable column as a card. A hierarchical one
  // carries a "Tree" badge; a flat one does not, because it has no tree.
  if (!id) {
    return (
      <div className={styles.container}>
        <div className={styles.header}>
          <h1 className={styles.title}>{t('Custom columns')}</h1>
          {me && !me.role.anonymous && <a className={styles.profile} href={apiUrl('/me')}>{t('Choose visible columns')}</a>}
          {!!columns.data?.items.length && (
            <span className={styles.count}>
              {t(columns.data.items.length === 1 ? '{count} column' : '{count} columns', { count: columns.data.items.length })}
            </span>
          )}
        </div>
        {columns.isLoading ? <SpinnerCentered size={40} /> : columns.isError ? (
          <RequestError title={t('Could not load columns')} error={columns.error} retrying={columns.isFetching} retry={() => { void columns.refetch(); }} />
        ) : (columns.data?.items.length ?? 0) === 0 ? (
          <EmptyState title={t('No custom columns to browse')}
            message={t('Tag-like custom columns defined in the library appear here.')} />
        ) : (
          <ul className={styles.columnGrid}>
            {columns.data!.items.map((col) => (
              <li key={col.id}>
                <Link href={`/cc/${col.id}`} className={styles.columnCard}>
                  <Tag size={16} className={styles.nodeIcon} aria-hidden="true" focusable={false} />
                  <span className={styles.columnName}>{col.name}</span>
                  {col.hierarchical && (
                    <span className={styles.badge}>{t('Tree')}</span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  // In after-children mode the selected node's books render inside the list;
  // the slot is handed down and each TreeNode renders it when it is the
  // selected one. One books query feeds either placement.
  const containsPath = (nodes: CcNode[]): boolean => nodes.some(node => node.path === path || containsPath(node.children));
  const useInset = afterChildren && !!path && containsPath(tree.data?.nodes ?? []);
  const booksSlot = useInset
    ? <NodeBooks colId={id} path={path} variant="inset" />
    : null;

  // Column mode: toolbar + values + books.
  return (
    <div className={styles.container}>
      <Link href="/cc" className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" focusable={false} />
        {t('Custom columns')}
      </Link>
      <div className={styles.header}>
        <h1 className={styles.title}>
          {tree.data?.column.name ?? t('Browse by Column')}
        </h1>
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
        {isHierarchical
          ? t('Select a category to see the books assigned to it and all of its sub-categories.')
          : t('Select a value to see the books assigned to it.')}
      </p>
      {tree.isLoading ? <SpinnerCentered size={40} /> : tree.isError ? (
        <RequestError title={t('Could not load column')} error={tree.error} retrying={tree.isFetching} retry={() => { void tree.refetch(); }} />
      ) : (
        <>
          <ValuesList colId={id} selected={path} booksSlot={booksSlot} />
          {!useInset && <NodeBooks colId={id} path={path} variant="section" />}
        </>
      )}
    </div>
  );
}
