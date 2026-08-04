import { useMemo, useState } from 'react';
import { Link } from 'wouter';
import { LayoutGrid, List, Search, Pencil, Trash2, Check, X, Merge } from 'lucide-react';
import { useEntityList, useMe, useRenameTag, useDeleteTag, tagConflictOf } from '../lib/queries';
import type { TagConflict } from '../lib/queries';
import { ApiError } from '../lib/api';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useI18n } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { usePersistentBool } from '../lib/usePersistentBool';
import styles from './BrowseList.module.css';

interface BrowseListProps {
  /** Endpoint/route segment, e.g. "authors". */
  plural: string;
  /** Heading, e.g. "Authors". */
  title: string;
}

/** One tag row with in-place rename and delete (#973).
 *
 *  The reporter asked for these "where I can see all the tags, so I can more
 *  easily dedupe and consolidate" — the tag's own page already had rename, and
 *  bouncing into each tag to compare it with its neighbour is the ergonomics
 *  problem. Each row owns its own mutations, which is why this is a component
 *  rather than inline JSX in the map.
 */
function TagRow({ id, name, count, href }: { id: number | string; name: string; count: number; href: string }) {
  const { t } = useI18n();
  const announce = useAnnouncer();
  const rename = useRenameTag(id);
  const remove = useDeleteTag(id);
  const [mode, setMode] = useState<'idle' | 'rename' | 'delete'>('idle');
  const [draft, setDraft] = useState(name);
  const [err, setErr] = useState('');
  const [conflict, setConflict] = useState<TagConflict | null>(null);

  const close = () => { setMode('idle'); setErr(''); setConflict(null); };
  const failure = (error: unknown, fallback: string) => {
    if (error instanceof ApiError) {
      const messages: Record<number, string> = {
        400: t('Enter a valid tag name'),
        401: t('You must be signed in'),
        403: t('You are not allowed to edit metadata'),
        404: t('Tag not found'),
      };
      return messages[error.status] ?? fallback;
    }
    return fallback;
  };

  const submit = (next: string, merge?: boolean) => {
    rename.mutate({ name: next, merge }, {
      onSuccess: (result) => {
        announce(result.merged
          ? t('Merged into {name}', { name: result.name })
          : t('Tag renamed to {name}', { name: result.name }));
        close();
      },
      onError: (error) => {
        // A named collision is the merge offer, not a failure (#973).
        const found = tagConflictOf(error);
        if (found && !merge) { setConflict(found); setErr(''); return; }
        setConflict(null);
        setErr(failure(error, t('Could not rename tag')));
      },
    });
  };

  if (mode === 'rename') {
    return (
      <li className={styles.rowEditing}>
        <form className={styles.rowForm} onSubmit={(e) => {
          e.preventDefault();
          const next = draft.trim();
          setConflict(null);
          if (!next) { setErr(t('Tag name cannot be empty')); return; }
          submit(next);
        }}>
          <label className="sr-only" htmlFor={`tag-rename-${id}`}>{t('Tag name')}</label>
          <input id={`tag-rename-${id}`} className={styles.rowInput} value={draft} autoFocus
            aria-invalid={!!err} aria-describedby={err ? `tag-rename-err-${id}` : undefined}
            onKeyDown={(e) => { if (e.key === 'Escape') close(); }}
            onChange={(e) => setDraft(e.target.value)} />
          <button type="submit" className={styles.rowAction} disabled={rename.isPending}
            aria-label={t('Save tag name')}><Check size={17} aria-hidden="true" focusable={false} /></button>
          <button type="button" className={styles.rowAction} onClick={close}
            aria-label={t('Cancel')}><X size={17} aria-hidden="true" focusable={false} /></button>
          {err && <span id={`tag-rename-err-${id}`} className={styles.rowError} role="alert">{err}</span>}
          {conflict && (
            <div className={styles.rowConfirm} role="alert">
              <span>{conflict.count === 1
                ? t('“{name}” already exists on {count} book. Merge this tag into it?', { name: conflict.name, count: conflict.count })
                : t('“{name}” already exists on {count} books. Merge this tag into it?', { name: conflict.name, count: conflict.count })}</span>
              <button type="button" className={styles.rowAction} disabled={rename.isPending}
                onClick={() => submit(conflict.name, true)}
                aria-label={t('Merge into {name}', { name: conflict.name })}>
                <Merge size={16} aria-hidden="true" focusable={false} />
                <span className={styles.actionLabel}>{t('Merge')}</span>
              </button>
              <button type="button" className={styles.rowAction} onClick={() => setConflict(null)}
                aria-label={t('Cancel')}><X size={16} aria-hidden="true" focusable={false} /></button>
            </div>
          )}
        </form>
      </li>
    );
  }

  if (mode === 'delete') {
    return (
      <li className={styles.rowEditing}>
        <div className={styles.rowConfirm} role="alert">
          <span>{count === 1
            ? t('Delete “{name}”? It is removed from {count} book, which is kept.', { name, count })
            : t('Delete “{name}”? It is removed from {count} books, which are kept.', { name, count })}</span>
          <button type="button" className={styles.rowDanger} disabled={remove.isPending}
            aria-label={t('Confirm delete tag {name}', { name })}
            onClick={() => remove.mutate(undefined, {
              onSuccess: (result) => { announce(t('Deleted tag {name}', { name: result.name })); close(); },
              onError: (error) => setErr(failure(error, t('Could not delete tag'))),
            })}>
            <Trash2 size={16} aria-hidden="true" focusable={false} />
            <span className={styles.actionLabel}>{t('Delete')}</span>
          </button>
          <button type="button" className={styles.rowAction} onClick={close}
            aria-label={t('Cancel')}><X size={16} aria-hidden="true" focusable={false} /></button>
          {err && <span className={styles.rowError} role="alert">{err}</span>}
        </div>
      </li>
    );
  }

  // The link and the buttons are siblings — a button nested inside an anchor is
  // invalid and unreachable by keyboard.
  return (
    <li className={styles.rowWithActions}>
      <Link href={href} className={styles.item}>
        <span className={styles.name}>{name}</span>
        <span className={styles.badge}>{count}</span>
      </Link>
      <span className={styles.rowActions}>
        <button type="button" className={styles.rowAction}
          onClick={() => { setDraft(name); setErr(''); setConflict(null); setMode('rename'); }}
          aria-label={t('Rename tag {name}', { name })}>
          <Pencil size={16} aria-hidden="true" focusable={false} />
        </button>
        <button type="button" className={styles.rowAction}
          onClick={() => { setErr(''); setMode('delete'); }}
          aria-label={t('Delete tag {name}', { name })}>
          <Trash2 size={16} aria-hidden="true" focusable={false} />
        </button>
      </span>
    </li>
  );
}

export function BrowseList({ plural, title }: BrowseListProps) {
  const { t, locale } = useI18n();
  const { data, isLoading, error } = useEntityList(plural);
  // Tag maintenance is an editor action; everyone else sees the plain list.
  const canEditTags = plural === 'tags' && !!useMe().data?.role?.edit;
  const [q, setQ] = useState('');
  const [compact, setCompact] = usePersistentBool('cwng:browse-list-compact', false);
  const translatedItems = useMemo(() => {
    const translated = t(title);
    try {
      return translated.toLocaleLowerCase((locale || 'en').replace('_', '-'));
    } catch {
      return translated.toLocaleLowerCase();
    }
  }, [locale, t, title]);

  const items = useMemo(() => {
    const all = data?.items ?? [];
    if (!q.trim()) return all;
    const needle = q.trim().toLowerCase();
    return all.filter((e) => e.name.toLowerCase().includes(needle));
  }, [data, q]);

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <div className={styles.heading}>
          <h1 className={styles.title}>{t(title)}</h1>
          {data && <span className={styles.count}>{data.items.length}</span>}
        </div>
        <div className={styles.viewToggle} role="group" aria-label={t('View')}>
          <button type="button" onClick={() => setCompact(false)} aria-pressed={!compact} aria-label={t('Grid view')}>
            <LayoutGrid size={17} aria-hidden="true" focusable={false} />
          </button>
          <button type="button" onClick={() => setCompact(true)} aria-pressed={compact} aria-label={t('List view')}>
            <List size={17} aria-hidden="true" focusable={false} />
          </button>
        </div>
      </div>

      {data && data.items.length > 8 && (
        <div className={styles.searchWrap}>
          <Search size={15} className={styles.searchIcon} />
          <input
            type="search"
            className={styles.searchInput}
            placeholder={t('Filter {items}…', { items: translatedItems })}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label={t('Filter {items}', { items: translatedItems })}
          />
        </div>
      )}

      {isLoading ? (
        <SpinnerCentered size={36} />
      ) : error ? (
        <EmptyState message={error instanceof Error ? error.message : t('Failed to load.')} />
      ) : items.length === 0 ? (
        <EmptyState message={q
          ? t('No matching {items} for "{query}".', { items: translatedItems, query: q })
          : t('No {items} yet.', { items: translatedItems })} />
      ) : (
        <ul className={compact ? styles.list : styles.grid} role="list">
          {items.map((e) => {
            const href = `/${plural}/${encodeURIComponent(String(e.id))}`;
            return canEditTags ? (
              <TagRow key={String(e.id)} id={e.id} name={e.name} count={e.count} href={href} />
            ) : (
              <li key={String(e.id)}>
                <Link href={href} className={styles.item}>
                  <span className={styles.name}>{e.name}</span>
                  <span className={styles.badge}>{e.count}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
