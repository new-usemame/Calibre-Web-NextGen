import { useState, useEffect, useRef } from 'react';
import { useLocation } from 'wouter';
import { Wand2, Plus, Trash2 } from 'lucide-react';
import { useMagicShelfPreview, useCreateMagicShelf, useEditMagicShelf, useMagicShelfBooks } from '../lib/queries';
import type { MagicRule } from '../lib/queries';
import { Button } from '../components/Button';
import { useT } from '../lib/i18n';
import { ApiError } from '../lib/api';
import styles from './MagicShelf.module.css';

const FIELDS = [
  { id: 'title', label: 'Title' },
  { id: 'author', label: 'Author' },
  { id: 'series', label: 'Series' },
  { id: 'tag', label: 'Tag' },
  { id: 'publisher', label: 'Publisher' },
  { id: 'language', label: 'Language' },
  { id: 'rating', label: 'Rating' },
  { id: 'comments', label: 'Description' },
];
const TEXT_OPS = [
  { id: 'contains', label: 'contains' },
  { id: 'not_contains', label: 'does not contain' },
  { id: 'equal', label: 'is' },
  { id: 'not_equal', label: 'is not' },
  { id: 'begins_with', label: 'begins with' },
  { id: 'ends_with', label: 'ends with' },
];
const NUM_OPS = [
  { id: 'equal', label: '=' },
  { id: 'greater', label: '>' },
  { id: 'greater_or_equal', label: '≥' },
  { id: 'less', label: '<' },
  { id: 'less_or_equal', label: '≤' },
];

let _rid = 0;
const newRule = (): MagicRule & { _k: number } => ({ _k: ++_rid, id: 'title', operator: 'contains', value: '' });

/** Native smart-collection (magic shelf) rule builder: name + icon, AND/OR
 *  match, a list of field/operator/value rules, live preview, save. Consumes the
 *  legacy /magicshelf/preview + /magicshelf endpoints (rule engine stays server-side). */
export function MagicShelf({ editId }: { editId?: string }) {
  const t = useT();
  const [, navigate] = useLocation();
  const preview = useMagicShelfPreview();
  const create = useCreateMagicShelf();
  const edit = useEditMagicShelf(editId ?? '');
  // In edit mode, load the existing shelf's name/icon/rules to seed the form.
  const existing = useMagicShelfBooks(editId ?? '', 1);

  const [name, setName] = useState('');
  const [icon, setIcon] = useState('🪄');
  const [condition, setCondition] = useState<'AND' | 'OR'>('AND');
  const [rules, setRules] = useState<(MagicRule & { _k: number })[]>([newRule()]);
  const [seeded, setSeeded] = useState(false);

  useEffect(() => {
    if (!editId || seeded || !existing.data) return;
    const d = existing.data as unknown as { name: string; icon: string; rules?: { condition?: 'AND' | 'OR'; rules?: MagicRule[] } };
    setName(d.name || '');
    setIcon(d.icon || '🪄');
    setCondition(d.rules?.condition === 'OR' ? 'OR' : 'AND');
    const loaded = (d.rules?.rules || []).map((r) => ({ _k: ++_rid, id: r.id, operator: r.operator, value: String(r.value ?? '') }));
    setRules(loaded.length ? loaded : [newRule()]);
    setSeeded(true);
  }, [editId, seeded, existing.data]);
  const [previewData, setPreviewData] = useState<{ count: number; sample: string[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const ruleSet = () => ({ condition, rules: rules.map(({ id, operator, value }) => ({ id, operator, value })) });

  // Live preview (debounced) whenever the rules change and have at least one value.
  useEffect(() => {
    const filled = rules.filter((r) => r.value.trim() || r.operator.includes('empty'));
    if (filled.length === 0) { setPreviewData(null); return; }
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      preview.mutate(ruleSet(), {
        onSuccess: (d) => { if (d.success) setPreviewData({ count: d.count, sample: d.sample_books }); },
        onError: () => setPreviewData(null),
      });
    }, 500);
    return () => { if (debounce.current) clearTimeout(debounce.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(rules), condition]);

  const isNum = (id: string) => id === 'rating';
  const setRule = (k: number, patch: Partial<MagicRule>) =>
    setRules((rs) => rs.map((r) => (r._k === k ? { ...r, ...patch } : r)));

  const onCancel = () => {
    // Discard edits and go back where the user came from; fall back to the shelf
    // view (editing) or the shelves list (creating) on a direct/bookmarked load.
    if (window.history.length > 1) window.history.back();
    else navigate(editId ? `/magic/${editId}` : '/shelves');
  };

  const onSave = () => {
    setErr(null);
    if (!name.trim()) { setErr(t('Give your smart shelf a name.')); return; }
    const payload = { name: name.trim(), icon: icon || '🪄', rules: ruleSet() };
    if (editId) {
      edit.mutate(payload, {
        onSuccess: (d) => d.success ? navigate(`/magic/${editId}`) : setErr(d.message || t('Could not save the shelf.')),
        onError: (e) => setErr(e instanceof ApiError ? e.message : t('Could not save the shelf.')),
      });
    } else {
      create.mutate(payload, {
        onSuccess: (d) => d.success ? navigate(d.shelf_id ? `/magic/${d.shelf_id}` : '/') : setErr(d.message || t('Could not create the shelf.')),
        onError: (e) => setErr(e instanceof ApiError ? e.message : t('Could not create the shelf.')),
      });
    }
  };
  const saving = create.isPending || edit.isPending;

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <Wand2 size={22} className={styles.headerIcon} aria-hidden="true" focusable={false} />
        <h1 className={styles.title}>{editId ? t('Edit smart shelf') : t('New smart shelf')}</h1>
      </div>

      <div className={styles.topRow}>
        <label className={styles.iconField}>
          <span>{t('Icon')}</span>
          <input value={icon} onChange={(e) => setIcon(e.target.value)} maxLength={4} className={styles.iconInput} />
        </label>
        <label className={styles.nameField}>
          <span>{t('Name')}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100}
            placeholder={t('e.g. Unread sci-fi')} />
        </label>
      </div>

      <div className={styles.matchRow}>
        {t('Match')}
        <select value={condition} onChange={(e) => setCondition(e.target.value as 'AND' | 'OR')}>
          <option value="AND">{t('all rules')}</option>
          <option value="OR">{t('any rule')}</option>
        </select>
      </div>

      <div className={styles.rules}>
        {rules.map((r) => {
          const ops = isNum(r.id) ? NUM_OPS : TEXT_OPS;
          return (
            <div key={r._k} className={styles.ruleRow}>
              <select value={r.id} onChange={(e) => {
                const id = e.target.value;
                setRule(r._k, { id, operator: (isNum(id) ? NUM_OPS : TEXT_OPS)[0].id });
              }}>
                {FIELDS.map((f) => <option key={f.id} value={f.id}>{t(f.label)}</option>)}
              </select>
              <select value={r.operator} onChange={(e) => setRule(r._k, { operator: e.target.value })}>
                {ops.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
              <input value={r.value} onChange={(e) => setRule(r._k, { value: e.target.value })}
                placeholder={t('value')} type={isNum(r.id) ? 'number' : 'text'} />
              <button className={styles.removeRule} onClick={() => setRules((rs) => rs.filter((x) => x._k !== r._k))}
                disabled={rules.length === 1} aria-label={t('Remove rule')}>
                <Trash2 size={15} aria-hidden="true" focusable={false} />
              </button>
            </div>
          );
        })}
        <button className={styles.addRule} onClick={() => setRules((rs) => [...rs, newRule()])}>
          <Plus size={15} aria-hidden="true" focusable={false} /> {t('Add rule')}
        </button>
      </div>

      {previewData && (
        <div className={styles.preview}>
          <strong>{previewData.count}</strong> {t('books match')}
          {previewData.sample.length > 0 && (
            <span className={styles.sample}> — {previewData.sample.slice(0, 5).join(', ')}…</span>
          )}
        </div>
      )}

      {err && <p className={styles.err}>{err}</p>}

      <div className={styles.actions}>
        <Button onClick={onSave} disabled={saving}>
          <Wand2 size={16} aria-hidden="true" focusable={false} /> {saving ? t('Saving…') : (editId ? t('Save changes') : t('Create smart shelf'))}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={saving}>{t('Cancel')}</Button>
      </div>
    </main>
  );
}
