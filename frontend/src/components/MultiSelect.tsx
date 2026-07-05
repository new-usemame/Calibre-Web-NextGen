import { useState, useRef, useEffect, useMemo, useId } from 'react';
import { X, ChevronDown } from 'lucide-react';
import { useT } from '../lib/i18n';
import styles from './MultiSelect.module.css';

export interface Option {
  id: string | number;
  name: string;
}

interface MultiSelectProps {
  options: Option[];
  /** Selected option ids. */
  value: (string | number)[];
  onChange: (next: (string | number)[]) => void;
  placeholder?: string;
}

/** Chip-based searchable multi-select implementing the WAI-ARIA editable
 *  combobox + listbox pattern: a persistent text input (role="combobox") is the
 *  single tab stop, ArrowUp/Down move the active option via aria-activedescendant,
 *  Enter selects, Escape closes, Backspace on an empty query removes the last
 *  chip. Reused by advanced search for tags / series / languages / formats. */
export function MultiSelect({ options, value, onChange, placeholder = 'Select…' }: MultiSelectProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const optionId = (i: number) => `${listId}-opt-${i}`;

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const byId = useMemo(() => new Map(options.map((o) => [String(o.id), o])), [options]);
  const selectedSet = useMemo(() => new Set(value.map(String)), [value]);

  const available = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return options
      .filter((o) => !selectedSet.has(String(o.id)))
      .filter((o) => !needle || o.name.toLowerCase().includes(needle))
      .slice(0, 50);
  }, [options, selectedSet, query]);

  // Keep the active option in range as the filtered list changes.
  useEffect(() => {
    setActiveIndex((i) => Math.min(Math.max(i, 0), Math.max(available.length - 1, 0)));
  }, [available.length]);

  const add = (id: string | number) => {
    onChange([...value, id]);
    setQuery('');
    setActiveIndex(0);
    inputRef.current?.focus();
  };
  const remove = (id: string | number) => onChange(value.filter((v) => String(v) !== String(id)));

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        if (!open) { setOpen(true); return; }
        setActiveIndex((i) => Math.min(i + 1, available.length - 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (!open) { setOpen(true); return; }
        setActiveIndex((i) => Math.max(i - 1, 0));
        break;
      case 'Enter':
        if (open && available[activeIndex]) {
          e.preventDefault();
          add(available[activeIndex].id);
        }
        break;
      case 'Escape':
        if (open) { e.preventDefault(); setOpen(false); }
        break;
      case 'Backspace':
        if (query === '' && value.length > 0) remove(value[value.length - 1]);
        break;
    }
  };

  const activeDescendant = open && available[activeIndex] ? optionId(activeIndex) : undefined;

  return (
    <div className={styles.wrap} ref={wrapRef}>
      {/* clicking anywhere in the control focuses the combobox input */}
      <div className={styles.control} onClick={() => inputRef.current?.focus()}>
        {value.map((id) => {
          const name = byId.get(String(id))?.name ?? String(id);
          return (
            <span key={String(id)} className={styles.chip}>
              {name}
              <button
                type="button"
                className={styles.chipX}
                aria-label={t('Remove {name}', { name })}
                onClick={(e) => {
                  e.stopPropagation();
                  remove(id);
                }}
              >
                <X size={12} aria-hidden="true" focusable={false} />
              </button>
            </span>
          );
        })}
        <input
          ref={inputRef}
          className={styles.input}
          value={query}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeDescendant}
          placeholder={value.length === 0 ? placeholder : ''}
          aria-label={placeholder}
          onFocus={() => setOpen(true)}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onKeyDown={onKeyDown}
        />
        <ChevronDown size={15} className={styles.caret} aria-hidden="true" focusable={false} />
      </div>

      {open && available.length > 0 && (
        <ul className={styles.menu} role="listbox" id={listId} aria-label={placeholder}>
          {available.map((o, i) => (
            <li
              key={String(o.id)}
              id={optionId(i)}
              role="option"
              aria-selected={i === activeIndex}
              className={i === activeIndex ? styles.optionActive : styles.option}
              // onMouseDown (not onClick) + preventDefault: select before the
              // input blurs, so the menu doesn't close out from under the click.
              onMouseDown={(e) => { e.preventDefault(); add(o.id); }}
              onMouseEnter={() => setActiveIndex(i)}
            >
              {o.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
