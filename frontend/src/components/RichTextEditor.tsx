import { useCallback, useEffect, useId, useRef, useState } from 'react';
import {
  Bold, Italic, Heading2, Heading3, List, ListOrdered, Quote,
  Code, Link2, Unlink, RemoveFormatting, Eye, PencilLine,
} from 'lucide-react';
import { useT } from '../lib/i18n';
import { sanitizeDescriptionHtml, isEmptyHtml } from '../lib/richText';
import styles from './RichTextEditor.module.css';

/*
 * Formatting editor for book descriptions — fork #919, reported by @mrdynamo
 * with @jsparrowio and @Gauva1n on the thread, plus #1038 (an anonymous
 * "switched back to the classic view" report naming the same gap).
 *
 * Built on contenteditable + execCommand rather than an editor library: the
 * project ships no bundler-era JS dependencies and adding one is gated
 * (CLAUDE.md rule 6). execCommand is deprecated but implemented everywhere,
 * and styleWithCSS is forced OFF so it emits <b>/<i> tags instead of
 * <span style>, which the server would strip.
 *
 * The toolbar deliberately has no underline or strikethrough. See
 * lib/richText.ts: bleach ESCAPES those, so they would come back as visible
 * "&lt;u&gt;" in the description rather than quietly doing nothing.
 */

interface Props {
  value: string;
  onChange: (html: string) => void;
  ariaLabel: string;
  id?: string;
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** Plain-text paste keeps its paragraph structure: blank lines become
 *  paragraphs, single newlines become <br>. Pasting a blurb out of a text file
 *  otherwise arrives as one run-on block. */
function plainTextToHtml(text: string): string {
  return text
    .split(/\n{2,}/)
    .map((para) => `<p>${escapeHtml(para).replace(/\n/g, '<br>')}</p>`)
    .join('');
}

export function RichTextEditor({ value, onChange, ariaLabel, id }: Props) {
  const t = useT();
  const editorRef = useRef<HTMLDivElement>(null);
  // null means "nothing written to the DOM yet". Seeding this with `value`
  // instead made the editor render EMPTY for every book that already had a
  // description: EditBook mounts this only after the metadata query resolves,
  // so the first value equalled the seed, the effect below decided nothing had
  // changed, and the contenteditable was never populated. Saving from that
  // state would have posted an empty description over the real one.
  const lastEmitted = useRef<string | null>(null);
  const savedRange = useRef<Range | null>(null);
  const [mode, setMode] = useState<'rich' | 'html'>('rich');
  const [active, setActive] = useState<Record<string, boolean>>({});
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkUrl, setLinkUrl] = useState('');
  const linkInputRef = useRef<HTMLInputElement>(null);
  const fallbackId = useId();
  const editorId = id ?? fallbackId;

  // Write the incoming value into the contenteditable only when it did not
  // come from us. Rewriting innerHTML on every keystroke would drop the caret
  // to the start of the field on every character typed.
  useEffect(() => {
    const el = editorRef.current;
    if (!el || mode !== 'rich') return;
    if (value !== lastEmitted.current) {
      // Sanitize INBOUND as well as outbound. /api/v1/books/<id>/metadata
      // returns the stored description raw and unsanitized on purpose (you
      // edit what is stored, cps/api/edit.py), and unlike the <textarea> this
      // replaced, innerHTML is a live sink: <img onerror> from a description
      // written by any edit-capable user or pulled in by a metadata provider
      // would run in the next editor's session.
      el.innerHTML = sanitizeDescriptionHtml(value);
      lastEmitted.current = value;
    }
  }, [value, mode]);

  const refreshActive = useCallback(() => {
    const state: Record<string, boolean> = {};
    for (const cmd of ['bold', 'italic', 'insertUnorderedList', 'insertOrderedList']) {
      try { state[cmd] = document.queryCommandState(cmd); } catch { state[cmd] = false; }
    }
    setActive(state);
  }, []);

  const emit = useCallback(() => {
    const el = editorRef.current;
    if (!el) return;
    const html = sanitizeDescriptionHtml(el.innerHTML);
    lastEmitted.current = html;
    onChange(html);
    refreshActive();
  }, [onChange, refreshActive]);

  const exec = useCallback((command: string, arg?: string) => {
    editorRef.current?.focus();
    // Emit tags, not inline styles: the server strips style attributes, so a
    // CSS-styled bold would look right in the editor and be plain on the page.
    try { document.execCommand('styleWithCSS', false, 'false'); } catch { /* not supported */ }
    document.execCommand(command, false, arg);
    emit();
  }, [emit]);

  const toggleBlock = useCallback((tag: string) => {
    let current = '';
    try { current = (document.queryCommandValue('formatBlock') || '').toLowerCase(); } catch { /* ignore */ }
    exec('formatBlock', current === tag || current === `<${tag}>` ? '<p>' : `<${tag}>`);
  }, [exec]);

  const wrapCode = useCallback(() => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return;
    exec('insertHTML', `<code>${escapeHtml(sel.toString())}</code>`);
  }, [exec]);

  const openLink = useCallback(() => {
    const sel = window.getSelection();
    savedRange.current = sel && sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
    setLinkUrl('https://');
    setLinkOpen(true);
    window.setTimeout(() => linkInputRef.current?.select(), 0);
  }, []);

  const applyLink = useCallback(() => {
    const url = linkUrl.trim();
    setLinkOpen(false);
    if (!url || url === 'https://') return;
    const el = editorRef.current;
    if (!el) return;
    el.focus();
    // Restore the selection the toolbar stole when the URL input took focus,
    // or execCommand would apply the link to nothing.
    const range = savedRange.current;
    if (range) {
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
      if (range.collapsed) {
        exec('insertHTML', `<a href="${escapeHtml(url)}">${escapeHtml(url)}</a>`);
        return;
      }
    }
    exec('createLink', url);
  }, [linkUrl, exec]);

  const onPaste = useCallback((e: React.ClipboardEvent<HTMLDivElement>) => {
    const html = e.clipboardData.getData('text/html');
    const text = e.clipboardData.getData('text/plain');
    if (!html && !text) return;
    e.preventDefault();
    // #919 (@Gauva1n): pasting a description off Goodreads or Amazon has to
    // keep its paragraphs and lists while dropping the wrapper markup a web
    // copy drags along.
    document.execCommand('insertHTML', false, html ? sanitizeDescriptionHtml(html) : plainTextToHtml(text));
    emit();
  }, [emit]);

  const switchMode = useCallback((next: 'rich' | 'html') => {
    if (next === 'rich') {
      // Clean whatever was hand-typed in source mode before it becomes DOM.
      // Clearing lastEmitted forces the effect to repopulate the contenteditable.
      const cleaned = sanitizeDescriptionHtml(value);
      lastEmitted.current = null;
      if (cleaned !== value) { onChange(cleaned); }
    }
    setMode(next);
  }, [value, onChange]);

  /* A mousedown on a toolbar button blurs the contenteditable and collapses its
     selection, so execCommand would format nothing. Measured before this was
     added: typing "Bold me", selecting it and clicking Bold saved the text with
     no <strong> at all. */
  const preventFocusSteal = (e: React.MouseEvent) => e.preventDefault();

  const btn = (key: string, label: string, Icon: typeof Bold, onClick: () => void, pressed?: boolean) => (
    <button key={key} type="button" className={styles.toolBtn} onClick={onClick}
      onMouseDown={preventFocusSteal}
      aria-label={label} title={label} aria-pressed={pressed === undefined ? undefined : pressed}
      data-active={pressed ? 'true' : undefined}>
      <Icon size={16} aria-hidden="true" />
    </button>
  );

  return (
    <div className={styles.wrap}>
      <div className={styles.toolbar} role="toolbar" aria-label={t('Description formatting')}
        aria-controls={editorId}>
        {mode === 'rich' && (
          <>
            {btn('bold', t('Bold'), Bold, () => exec('bold'), !!active.bold)}
            {btn('italic', t('Italic'), Italic, () => exec('italic'), !!active.italic)}
            <span className={styles.sep} aria-hidden="true" />
            {btn('h2', t('Heading'), Heading2, () => toggleBlock('h2'))}
            {btn('h3', t('Subheading'), Heading3, () => toggleBlock('h3'))}
            <span className={styles.sep} aria-hidden="true" />
            {btn('ul', t('Bulleted list'), List, () => exec('insertUnorderedList'), !!active.insertUnorderedList)}
            {btn('ol', t('Numbered list'), ListOrdered, () => exec('insertOrderedList'), !!active.insertOrderedList)}
            {btn('quote', t('Quote'), Quote, () => toggleBlock('blockquote'))}
            {btn('code', t('Code'), Code, wrapCode)}
            <span className={styles.sep} aria-hidden="true" />
            {btn('link', t('Add link'), Link2, openLink)}
            {btn('unlink', t('Remove link'), Unlink, () => exec('unlink'))}
            {btn('clear', t('Clear formatting'), RemoveFormatting, () => exec('removeFormat'))}
          </>
        )}
        <button type="button" className={styles.modeBtn}
          onClick={() => switchMode(mode === 'rich' ? 'html' : 'rich')}
          aria-label={mode === 'rich' ? t('Edit HTML') : t('Back to formatting')}
          aria-pressed={mode === 'html'}>
          {mode === 'rich'
            ? <><Code size={14} aria-hidden="true" /> {t('Edit HTML')}</>
            : <><PencilLine size={14} aria-hidden="true" /> {t('Back to formatting')}</>}
        </button>
      </div>

      {linkOpen && (
        <div className={styles.linkRow}>
          <input ref={linkInputRef} className={styles.linkInput} value={linkUrl} type="url"
            aria-label={t('Link address')} placeholder="https://"
            onChange={(e) => setLinkUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); applyLink(); }
              if (e.key === 'Escape') { e.preventDefault(); setLinkOpen(false); }
            }} />
          <button type="button" className={styles.linkApply} onClick={applyLink}>{t('Apply')}</button>
          <button type="button" className={styles.linkCancel} onClick={() => setLinkOpen(false)}>{t('Cancel')}</button>
        </div>
      )}

      {mode === 'rich' ? (
        <div ref={editorRef} id={editorId} className={styles.editor} contentEditable suppressContentEditableWarning
          role="textbox" aria-multiline="true" aria-label={ariaLabel} tabIndex={0}
          onInput={emit} onBlur={emit} onPaste={onPaste}
          onKeyUp={refreshActive} onMouseUp={refreshActive} onFocus={refreshActive} />
      ) : (
        <>
          <textarea id={editorId} className={styles.source} rows={10} value={value} aria-label={ariaLabel}
            spellCheck={false} onChange={(e) => onChange(e.target.value)}
            onBlur={() => { const c = sanitizeDescriptionHtml(value); if (c !== value) onChange(c); }} />
          <div className={styles.previewLabel} id={`${editorId}-preview-label`}>
            <Eye size={13} aria-hidden="true" /> {t('Preview')}
          </div>
          <div className={styles.preview} aria-labelledby={`${editorId}-preview-label`}
            // Preview of the author's own draft, cleaned by the same allowlist
            // the server applies on render.
            dangerouslySetInnerHTML={{ __html: sanitizeDescriptionHtml(value) }} />
          {isEmptyHtml(value) && <div className={styles.previewEmpty}>{t('Nothing to preview yet.')}</div>}
        </>
      )}
    </div>
  );
}
