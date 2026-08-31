import { useState, useRef, useEffect, useId } from 'react';
import { apiUrl } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './MetadataTypeahead.module.css';

export type TypeaheadField = 'tags' | 'authors' | 'series' | 'publishers' | 'languages';

interface Props {
  value: string;
  onChange: (v: string) => void;
  field: TypeaheadField;
  /** Multi-value fields (tags/publishers/languages/authors) suggest for the
   *  token at the caret and keep the rest; single-value (series) replaces all. */
  multi: boolean;
  /** Canonical join between values — ', ' for lists, ' & ' for authors. */
  sep?: string;
  inputClassName?: string;
  placeholder?: string;
  id?: string;
  'aria-label'?: string;
  /** Values to keep out of the suggestion list regardless of what is typed.
   *  A host that already holds the chosen values elsewhere (chips, pills) needs
   *  this: `multi={false}` parses no tokens out of the field, so without it the
   *  menu offers values the host will silently discard on commit. */
  excludeValues?: string[];
  /** Keys the combobox did not consume are handed on, so a host can own Enter
   *  and Escape without losing suggestion navigation. */
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  onBlur?: (e: React.FocusEvent<HTMLInputElement>) => void;
  autoFocus?: boolean;
  disabled?: boolean;
}

interface Segment { start: number; end: number; query: string; }

/** Bounds + query text of the value segment the caret sits in. For single-value
 *  fields (delim=null) the whole value is one segment. */
export function activeSegment(value: string, caret: number, delim: string | null): Segment {
  if (!delim) return { start: 0, end: value.length, query: value.trim() };
  const before = value.lastIndexOf(delim, Math.max(0, caret - 1));
  const start = before === -1 ? 0 : before + 1;
  const nextIdx = value.indexOf(delim, caret);
  const end = nextIdx === -1 ? value.length : nextIdx;
  return { start, end, query: value.slice(start, end).trim() };
}

/** Replace the active segment with `name`, normalizing to '<value><sep><value>'
 *  spacing without disturbing the untouched neighbours. */
export function applySuggestion(
  value: string, seg: Segment, name: string, delim: string | null, sep: string,
): string {
  if (!delim) return name;
  const head = value.slice(0, seg.start).replace(/\s+$/, ''); // keeps any trailing delim
  const tail = value.slice(seg.end);                          // begins with the next delim (if any)
  const joiner = head ? (head.endsWith(delim) ? ' ' : sep) : '';
  return head + joiner + name + tail;
}

/** A plain text input backed by server-fed suggestions of existing library
 *  values, so the editor stops spawning near-duplicate tags/series/authors from
 *  typos (#741, #778, #689). Free-text entry is preserved — the dropdown only
 *  offers what already exists; typing a genuinely new value still works.
 *  Implements the WAI-ARIA editable combobox + listbox pattern. */
export function MetadataTypeahead(props: Props) {
  const { value, onChange, field, multi, sep = ', ', inputClassName, placeholder, id,
          excludeValues, autoFocus, disabled } = props;
  const t = useT();
  const delim = multi ? sep.trim() : null;
  const [open, setOpen] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  // -1 = nothing chosen yet, so what you typed is what Enter commits. Starting
  // at 0 pre-selected the first row and Enter silently applied it instead of the
  // text under the caret, which made a value that merely SHARES a substring with
  // an existing one impossible to type ("foo" became "Fools and Jesters", #1398).
  // Manual selection is also what the WAI-ARIA combobox pattern asks for.
  const [activeIndex, setActiveIndex] = useState(-1);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const caretRef = useRef<number>(value.length);
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

  // Debounced fetch of suggestions for the segment currently under the caret.
  // Values already present in the field are hidden so we only surface additions.
  const fetchFor = (query: string, currentTokens: Set<string>) => {
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(
          apiUrl(`/api/v1/metadata/typeahead/${field}?q=${encodeURIComponent(query)}`),
          { credentials: 'include', signal: ctrl.signal, headers: { Accept: 'application/json' } },
        );
        if (!res.ok) { setSuggestions([]); return; }
        const data = await res.json();
        const list: string[] = Array.isArray(data?.suggestions) ? data.suggestions : [];
        setSuggestions(list.filter((s) => !currentTokens.has(s.toLowerCase())));
        // A fresh list must not silently re-arm Enter on a row nobody picked.
        setActiveIndex(-1);
      } catch {
        /* aborted or offline — leave the last list, dropdown just won't update */
      }
    }, 160);
    return () => { clearTimeout(timer); ctrl.abort(); };
  };

  const tokensExcludingActive = (val: string, seg: Segment): Set<string> => {
    const set = new Set<string>();
    // Host-supplied exclusions apply in both modes — they describe values the
    // host already has, which the field itself cannot reveal in single mode.
    (excludeValues ?? []).forEach((v) => {
      const trimmed = v.trim().toLowerCase();
      if (trimmed) set.add(trimmed);
    });
    if (!delim) return set;
    val.split(delim).forEach((tok, _i, _arr) => {
      const trimmed = tok.trim().toLowerCase();
      if (trimmed && trimmed !== seg.query.toLowerCase()) set.add(trimmed);
    });
    return set;
  };

  const refresh = (val: string, caret: number) => {
    const seg = activeSegment(val, caret, delim);
    return fetchFor(seg.query, tokensExcludingActive(val, seg));
  };

  const cancelRef = useRef<null | (() => void)>(null);
  const scheduleRefresh = (val: string, caret: number) => {
    cancelRef.current?.();
    cancelRef.current = refresh(val, caret);
  };
  useEffect(() => () => cancelRef.current?.(), []);

  const onInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    const caret = e.target.selectionStart ?? val.length;
    caretRef.current = caret;
    onChange(val);
    setOpen(true);
    scheduleRefresh(val, caret);
  };

  const openAndFetch = () => {
    setOpen(true);
    const caret = inputRef.current?.selectionStart ?? value.length;
    caretRef.current = caret;
    scheduleRefresh(value, caret);
  };

  const choose = (name: string) => {
    const seg = activeSegment(value, caretRef.current, delim);
    const next = applySuggestion(value, seg, name, delim, sep);
    onChange(next);
    // caret lands just after the inserted value
    const newCaret = seg.start + (value.slice(0, seg.start).replace(/\s+$/, '') ? 1 : 0) + name.length + 1;
    setOpen(false);
    setSuggestions([]);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) {
        el.focus();
        const pos = Math.min(newCaret, next.length);
        el.setSelectionRange(pos, pos);
        caretRef.current = pos;
      }
    });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        if (!open) { openAndFetch(); return; }
        setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (!open) { openAndFetch(); return; }
        // From nothing, wrap to the last option; from the first, step back out
        // to your own text rather than sticking on row 0.
        setActiveIndex((i) => (i === -1 ? suggestions.length - 1 : i - 1));
        break;
      case 'Enter':
        if (open && activeIndex >= 0 && suggestions[activeIndex]) {
          e.preventDefault();
          choose(suggestions[activeIndex]);
        }
        break;
      case 'Escape':
        if (open) { e.preventDefault(); setOpen(false); }
        break;
    }
    // The switch above calls preventDefault only when it actually consumed the
    // key — accepted a suggestion, moved the active option, closed an open
    // menu. Everything it left alone belongs to the host, which is what lets a
    // caller bind Enter to "commit" and Escape to "cancel" without breaking
    // arrow-key navigation through the suggestions.
    if (!e.defaultPrevented) props.onKeyDown?.(e);
  };

  const activeDescendant = open && suggestions[activeIndex] ? optionId(activeIndex) : undefined;

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <input
        ref={inputRef}
        id={id}
        className={inputClassName}
        value={value}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={activeDescendant}
        aria-label={props['aria-label']}
        placeholder={placeholder}
        autoComplete="off"
        autoFocus={autoFocus}
        disabled={disabled}
        onChange={onInput}
        onFocus={openAndFetch}
        onClick={() => { caretRef.current = inputRef.current?.selectionStart ?? value.length; }}
        onKeyDown={onKeyDown}
        onBlur={props.onBlur}
      />
      {open && suggestions.length > 0 && (
        <ul className={styles.menu} role="listbox" id={listId} aria-label={props['aria-label'] || field}>
          {suggestions.map((name, i) => (
            <li
              key={name}
              id={optionId(i)}
              role="option"
              aria-selected={i === activeIndex}
              className={i === activeIndex ? styles.optionActive : styles.option}
              // onMouseDown + preventDefault so the pick happens before the input
              // blurs (which would close the menu out from under the click).
              onMouseDown={(e) => { e.preventDefault(); choose(name); }}
              onMouseEnter={() => setActiveIndex(i)}
            >
              {name}
            </li>
          ))}
        </ul>
      )}
      {open && suggestions.length === 0 && (
        // A visually-hidden live region keeps SR users informed without a popup.
        <span className={styles.srOnly} aria-live="polite">{t('No suggestions')}</span>
      )}
    </div>
  );
}
