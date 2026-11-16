import { useEffect, useMemo, useRef, useState, useId } from 'react';
import { createPortal } from 'react-dom';
import { X, Plus } from 'lucide-react';
import { useCcPickerValues } from '../lib/queries';
import { useT } from '../lib/i18n';
import styles from './CcValuePicker.module.css';

/** Canonicalize newly entered hierarchy paths; stored spellings stay intact. */
function canonicalize(value: string): string {
  return value.split('.').map((s) => s.trim()).filter(Boolean).join('.');
}

interface FlatEntry {
  value: string;
  depth: number;
}

export interface CcValuePickerProps {
  columnId: number;
  columnLabel: string;
  /** True when the column's values form a dotted hierarchy (indented tree),
   *  false for a flat list. The server decides; see EditableCustomColumn. */
  hierarchical: boolean;
  multiple: boolean;
  /** Current field value — comma-joined when the column is multi-value. */
  value: string;
  onApply: (next: string) => void;
  onClose: () => void;
}

/** Modal for choosing a custom column's stored values on the book-edit page.
 *
 *  There is no save endpoint: Apply hands a comma-joined string back to the
 *  field, which the ordinary metadata update already persists. That keeps the
 *  round-trip contract in cps/api/edit.py::_custom_column_value intact.
 *
 *  Both editors use the same permission-filtered choices and server mode. */
export function CcValuePicker({ columnId, columnLabel, hierarchical: initialHierarchy, multiple, value, onApply, onClose }: CcValuePickerProps) {
  const t = useT();
  const tree = useCcPickerValues(columnId);
  const hierarchical = tree.data?.hierarchical ?? initialHierarchy;
  const [filter, setFilter] = useState('');
  const [newValue, setNewValue] = useState('');
  const [added, setAdded] = useState<FlatEntry[]>(() =>
    (multiple ? value.split(',') : [value]).map(part => part.trim()).filter(Boolean)
      .map(value => ({ value, depth: 0 })),
  );
  const [picked, setPicked] = useState<Set<string>>(() => new Set(
    (multiple ? value.split(',') : [value]).map(part => part.trim()).filter(Boolean),
  ));
  const [invalidNew, setInvalidNew] = useState(false);
  const titleId = useId();
  const modalRef = useRef<HTMLDivElement>(null);

  // Focus trap + Escape + scroll lock, mirroring the details modal.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const prevFocus = document.activeElement as HTMLElement | null;
    const node = modalRef.current;
    const focusables = () => (node
      ? Array.from(node.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
        .filter((el) => !el.hasAttribute('disabled'))
      : []);
    (focusables()[0] ?? node)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onCloseRef.current(); return; }
      if (e.key !== 'Tab') return;
      const els = focusables();
      if (!els.length) return;
      const first = els[0], last = els[els.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
      prevFocus?.focus?.();
    };
  }, []);

  // Stored spellings from the editor endpoint, plus unsaved additions.
  const entries = useMemo(() => {
    const base = tree.data?.entries ?? [];
    const merged = [...base];
    // Current values remain available even if absent from fetched choices.
    for (const entry of added) {
      if (!merged.some((m) => m.value === entry.value)) merged.push(entry);
    }
    return merged;
  }, [tree.data, added]);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((e) => e.value.toLowerCase().includes(needle));
  }, [entries, filter]);

  const toggle = (val: string, on: boolean) => setPicked(prev => {
    const next = new Set(multiple ? prev : []);
    if (on) next.add(val);
    else next.delete(val);
    return next;
  });

  const addNew = () => {
    const raw = newValue.trim();
    if (multiple && raw.includes(',')) { setInvalidNew(true); return; }
    const next = hierarchical ? canonicalize(raw) : raw;
    if (!next) return;
    setAdded(prev => prev.some(entry => entry.value === next)
      ? prev : [...prev, { value: next, depth: hierarchical ? next.split('.').length - 1 : 0 }]);
    toggle(next, true);
    setNewValue('');
    setInvalidNew(false);
    setFilter('');
  };

  const apply = () => {
    onApply(Array.from(picked).join(', '));
    onClose();
  };

  const isEmpty = !tree.isLoading && entries.length === 0;

  return createPortal(
    <div className={styles.overlay} onClick={onClose} role="presentation">
      <div className={styles.modal} onClick={(e) => e.stopPropagation()} ref={modalRef}
        role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className={styles.head}>
          <span className={styles.title} id={titleId}>{columnLabel}</span>
          <button type="button" className={styles.close} onClick={onClose} aria-label={t('Close')}>
            <X size={18} />
          </button>
        </div>

        <div className={styles.body}>
          <input
            className={styles.search}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t('Search or filter values...')}
            aria-label={t('Search or filter values')}
          />

          <div className={styles.list}>
            {tree.isLoading && <div className={styles.note}>{t('Loading values...')}</div>}
            {tree.isError && (
              <div className={styles.error} role="alert">{t('Could not load the values for this column.')}
                <button type="button" onClick={() => tree.refetch()}>{t('Try again')}</button></div>
            )}
            {!tree.isLoading && !tree.isError && isEmpty && (
              <div className={styles.note}>{t('No values stored for this column yet. Add one below.')}</div>
            )}
            {!tree.isLoading && !tree.isError && !isEmpty && visible.length === 0 && (
              <div className={styles.note}>{t('No values match the filter.')}</div>
            )}
            {visible.map((entry) => (
              <label key={entry.value} className={styles.item}
                style={hierarchical ? { paddingLeft: `${entry.depth * 16 + 8}px` } : undefined}>
                <input
                  type="checkbox"
                  checked={picked.has(entry.value)}
                  onChange={(e) => toggle(entry.value, e.target.checked)}
                />
                <span>{entry.value}</span>
              </label>
            ))}
          </div>

          {invalidNew && <div role="alert">{t('Enter one value without commas.')}</div>}
          <div className={styles.addRow}>
            <input
              className={styles.search}
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addNew(); } }}
              placeholder={t('e.g. Category.SubCategory.Item')}
              aria-label={t('New value')}
            />
            <button type="button" className={styles.addBtn} onClick={addNew} disabled={tree.isLoading || tree.isError}>
              <Plus size={15} /> {t('Add')}
            </button>
          </div>
        </div>

        <div className={styles.foot}>
          <button type="button" className={styles.cancel} onClick={onClose}>{t('Cancel')}</button>
          <button type="button" className={styles.apply} onClick={apply} disabled={tree.isLoading || tree.isError}>{t('Apply selection')}</button>
        </div>
      </div>
    </div>,
    document.body
  );
}
