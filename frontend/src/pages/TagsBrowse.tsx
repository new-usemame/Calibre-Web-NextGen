import { useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearch } from 'wouter';
import { ChevronRight } from 'lucide-react';
import { apiGet } from '../lib/api';
import { useLibraryRevision } from '../lib/libraryRevision';
import { useMe } from '../lib/queries';
import { useT } from '../lib/i18n';
import { BrowseList } from './BrowseList';
import { Catalog } from './Catalog';
import { NotFound } from './NotFound';
import { SectionError } from '../components/SectionError';
import { SpinnerCentered } from '../components/Spinner';
import styles from './CcBrowse.module.css';

interface TagNode {
  id: number | null;
  name: string;
  path: string | null;
  count: number;
  total_count: number;
  children: TagNode[];
}
interface TagTree { hierarchical: boolean; items: TagNode[] }

function Node({ node }: { node: TagNode }) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const childrenId = useId();
  const href = node.path === null ? `/tags/${node.id}`
    : `/tag-group?path=${encodeURIComponent(node.path)}`;
  return <li className={styles.treeNode}>
    <div className={styles.treeSummary}>
      {node.children.length > 0
        ? <button type="button" className={styles.disclosure} aria-label={node.name}
            aria-expanded={expanded} aria-controls={childrenId}
            onClick={() => setExpanded(value => !value)}>
            <ChevronRight size={14} aria-hidden="true" focusable={false}
              className={expanded ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron} />
          </button>
        : <span className={styles.chevronPlaceholder} aria-hidden="true" />}
      <Link href={href} className={styles.nodeLink}>{node.name}</Link>
      <span className={styles.badge} role="img" aria-label={t(
        node.total_count === 1 ? '{count} book' : '{count} books', { count: node.total_count })}>
        {node.total_count}
      </span>
    </div>
    {node.children.length > 0 && <ul id={childrenId} role="list" className={styles.children} hidden={!expanded}>
      {node.children.map(child => <Node key={JSON.stringify([child.path, child.id])} node={child} />)}
    </ul>}
  </li>;
}

export function TagsBrowse() {
  const t = useT();
  const search = useSearch();
  const flat = new URLSearchParams(search).get('view') === 'flat';
  const me = useMe().data;
  const revision = useLibraryRevision();
  const tree = useQuery<TagTree>({
    queryKey: ['tag-tree', me?.id, me?.library_mode, revision],
    queryFn: ({ signal }) => apiGet<TagTree>('/api/v1/tags/tree', { signal }),
    enabled: !flat,
  });
  if (flat || tree.data?.hierarchical === false) return <BrowseList plural="tags" title="Tags" />;
  return <div className={styles.container}>
    <h1>{t('Tags')}</h1>
    <p><Link href="/tags?view=flat">{t('All tags')}</Link></p>
    {tree.isPending ? <SpinnerCentered /> : tree.isError
      ? <SectionError message={t('Could not load tags. Please try again.')}
          onRetry={() => void tree.refetch()} retrying={tree.isFetching} />
      : <>
          <p>{t('Select a category to see the books assigned to it and all of its sub-categories.')}</p>
          <ul role="list" className={styles.tree}>
            {tree.data?.items.map(node => <Node key={JSON.stringify([node.path, node.id])} node={node} />)}
          </ul>
        </>}
  </div>;
}

export function TagGroup() {
  const search = useSearch();
  const path = new URLSearchParams(search).get('path') ?? '';
  return path ? <Catalog key={path} tagPath={path} /> : <NotFound />;
}
