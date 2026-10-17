import { useEffect, useMemo, useRef, useState, useId } from 'react';
import { createPortal } from 'react-dom';
import { X, Plus } from 'lucide-react';
import { useCcTree } from '../lib/queries';
import type { CcNode } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './CcValuePicker.module.css';

/** Canonicalise exactly like cps/hierarchy.py's split_path/join_path: each
 *  dot-segment is stripped and empty segments collapse, so a stored
 *  `Computers.` ticks the `Computers` node the browse tree counts. */
function canonicalize(value: string): string {
  return value.split('.').map((s) => s.trim()).filter(Boolean).join('.');
}

interface FlatEntry {
  value: string;
  depth: number;
}

/** The tree flattened to [{value, depth}], preserving DFS order. */
function flatten(nodes: CcNode[], depth = 0, out: FlatEntry[] = []): FlatEntry[] {
  for (const node of nodes) {
    out.push({ value: node.path, depth });
    flatten(node.children ?? [], depth + 1, out);
  }
  return out;
}

export interface CcValuePickerProps {
  columnId: number;
  columnLabel: string;
  /** True when the column's values form a dotted hierarchy (indented tree),
   *  false for a flat list. The server decides; see EditableCustomColumn. */
  hierarchical: boolean;
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
 *  Both modes come from one endpoint — the browse tree already serves flat
 *  columns with the same node shape (`children: []`) — so there is no
 *  client-side detection to drift out of sync with the server. */
export function CcValuePicker({ columnId, columnLabel, hierarchical, value, onApply, onClose }: CcValuePickerProps) {
  const t = useT();
  const tree = useCcTree(columnId);
  const [filter, setFilter] = useState('');
  const [newValue, setNewValue] = useState('');
  const [added, setAdded] = useState<FlatEntry[]>([]);
  const [picked, setPicked] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    value.split(',').forEach((part) => {
      const canon = canonicalize(part);
      if (canon) initial[canon] = true;
    });
    return initial;
  });
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

  // The server's nodes, plus anything added in this session.
  const entries = useMemo(() => {
    const base = hierarchical
      ? flatten(tree.data?.nodes ?? [])
      // Flat: every node is depth 0, and the server already gives the whole
      // stored value as `path` — Dewey 778.3 stays one entry.
      : (tree.data?.nodes ?? []).map((n) => ({ value: n.path, depth: 0 }));
    const merged = [...base];
    for (const entry of added) {
      if (!merged.some((m) => m.value === entry.value)) merged.push(entry);
    }
    return merged;
  }, [tree.data, hierarchical, added]);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((e) => e.value.toLowerCase().includes(needle));
  }, [entries, filter]);

  const toggle = (val: string, on: boolean) =>
    setPicked((prev) => ({ ...prev, [val]: on }));

  const addNew = () => {
    const canon = canonicalize(newValue.trim());
    if (!canon) return;
    setAdded((prev) => (prev.some((e) => e.value === canon)
      ? prev
      : [...prev, { value: canon, depth: hierarchical ? canon.split('.').length - 1 : 0 }]));
    setPicked((prev) => ({ ...prev, [canon]: true }));
    setNewValue('');
    setFilter('');
  };

  const apply = () => {
    onApply(Object.keys(picked).filter((k) => picked[k]).join(', '));
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
              <div className={styles.error} role="alert">{t('Could not load the values for this column.')}</div>
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
                  checked={!!picked[entry.value]}
                  onChange={(e) => toggle(entry.value, e.target.checked)}
                />
                <span>{entry.value}</span>
              </label>
            ))}
          </div>

          <div className={styles.addRow}>
            <input
              className={styles.search}
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addNew(); } }}
              placeholder={t('e.g. Category.SubCategory.Item')}
              aria-label={t('New value')}
            />
            <button type="button" className={styles.addBtn} onClick={addNew}>
              <Plus size={15} /> {t('Add')}
            </button>
          </div>
        </div>

        <div className={styles.foot}>
          <button type="button" className={styles.cancel} onClick={onClose}>{t('Cancel')}</button>
          <button type="button" className={styles.apply} onClick={apply}>{t('Apply selection')}</button>
        </div>
      </div>
    </div>,
    document.body
  );
}