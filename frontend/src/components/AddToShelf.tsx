import { useState, useRef, useEffect } from 'react';
import { Link } from 'wouter';
import { BookCopy, Check, Plus, Globe, Lock } from 'lucide-react';
import { useShelves, useBookShelves, useShelfMembership, useMe, useCreateShelf } from '../lib/queries';
import { useT } from '../lib/i18n';
import { Spinner } from './Spinner';
import styles from './AddToShelf.module.css';

/** "Add to shelf" popover for a book — toggles membership on the user's
 *  editable shelves and can create a new shelf inline. */
export function AddToShelf({ bookId }: { bookId: number }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  const me = useMe().data;
  const { data: shelvesData, isLoading } = useShelves();
  const { data: membership } = useBookShelves(bookId);
  const { add, remove } = useShelfMembership();
  const createShelf = useCreateShelf();

  const [newName, setNewName] = useState('');

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const canEditPublic = !!me?.role?.edit_shelfs;
  const editable = (shelvesData?.items ?? []).filter(
    (s) => s.is_owner || (s.is_public && canEditPublic),
  );
  const onShelf = new Set(membership?.shelf_ids ?? []);

  const toggle = (shelfId: number) => {
    if (onShelf.has(shelfId)) remove.mutate({ shelfId, bookId });
    else add.mutate({ shelfId, bookId });
  };

  const onCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    createShelf.mutate(
      { name },
      {
        onSuccess: (shelf) => {
          setNewName('');
          add.mutate({ shelfId: shelf.id, bookId });
        },
      },
    );
  };

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="true"
        aria-expanded={open}
      >
        <BookCopy size={15} aria-hidden="true" focusable={false} />
        {t('Add to shelf')}
      </button>

      {open && (
        // Disclosure, not an ARIA menu: it holds toggles + a form + a link, which
        // a menu can't contain (S8). Toggles use aria-pressed.
        <div className={styles.panel}>
          {isLoading ? (
            <div className={styles.loading}>
              <Spinner size={16} />
            </div>
          ) : (
            <>
              {editable.length > 0 ? (
                <ul className={styles.list}>
                  {editable.map((s) => {
                    const active = onShelf.has(s.id);
                    return (
                      <li key={s.id}>
                        <button
                          type="button"
                          className={styles.item}
                          onClick={() => toggle(s.id)}
                          aria-pressed={active}
                        >
                          <span className={active ? styles.checkOn : styles.checkOff} aria-hidden="true">
                            {active && <Check size={13} strokeWidth={3} />}
                          </span>
                          <span className={styles.itemName}>{s.name}</span>
                          <span className={styles.itemIcon} role="img"
                            aria-label={s.is_public ? t('Public shelf') : t('Private shelf')}>
                            {s.is_public
                              ? <Globe size={12} aria-hidden="true" focusable={false} />
                              : <Lock size={12} aria-hidden="true" focusable={false} />}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className={styles.empty}>{t('No shelves yet — create one below.')}</p>
              )}

              <form className={styles.createRow} onSubmit={onCreate}>
                <input
                  className={styles.createInput}
                  placeholder={t('New shelf…')}
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  maxLength={120}
                  aria-label={t('New shelf name')}
                />
                <button
                  type="submit"
                  className={styles.createBtn}
                  disabled={!newName.trim() || createShelf.isPending}
                  aria-label={t('Create shelf and add book')}
                >
                  <Plus size={15} aria-hidden="true" focusable={false} />
                </button>
              </form>

              <Link href="/shelves" className={styles.manageLink} onClick={() => setOpen(false)}>
                {t('Manage shelves')}
              </Link>
            </>
          )}
        </div>
      )}
    </div>
  );
}
