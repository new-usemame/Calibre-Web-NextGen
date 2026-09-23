import { useState, useEffect, useRef } from 'react';
import { useLocation } from 'wouter';
import { Wand2, Plus, Trash2 } from 'lucide-react';
import {
  useMagicShelfPreview, useCreateMagicShelf, useEditMagicShelf,
  useMagicShelfBooks, useMagicShelfRuleSchema,
} from '../lib/queries';
import type { MagicRule, MagicRuleField, MagicRuleOperator } from '../lib/queries';
import {
  groupFromStored, groupToStored, leafCount, removeNode, someLeaf, updateNode,
} from '../lib/magicRuleTree';
import type { RuleGroup, RuleLeaf } from '../lib/magicRuleTree';
import { Button } from '../components/Button';
import { useT } from '../lib/i18n';
import { ApiError } from '../lib/api';
import styles from './MagicShelf.module.css';

let _rid = 0;
const nextKey = () => ++_rid;
const newRule = (): RuleLeaf => ({ kind: 'rule', key: nextKey(), id: 'title', operator: 'contains', value: '' });
const newGroup = (): RuleGroup => ({ kind: 'group', key: nextKey(), condition: 'AND', rules: [newRule()] });

const hasRuleValue = (value: MagicRule['value']) =>
  Array.isArray(value) ? value.some((item) => item.trim()) : value.trim().length > 0;

const blankValueFor = (operator?: MagicRuleOperator): MagicRule['value'] =>
  operator?.nb_inputs === 2 ? ['', ''] : '';

/** Native smart-collection (magic shelf) rule builder: name + icon, AND/OR
 *  match, field/operator/value rules and nested rule groups, live preview, save.
 *  Consumes the legacy /magicshelf/preview + /magicshelf endpoints (rule engine
 *  stays server-side). */
export function MagicShelf({ editId }: { editId?: string }) {
  const t = useT();
  const [, navigate] = useLocation();
  const preview = useMagicShelfPreview();
  const create = useCreateMagicShelf();
  const edit = useEditMagicShelf(editId ?? '');
  const schemaQuery = useMagicShelfRuleSchema();
  // In edit mode, load the existing shelf's name/icon/rules to seed the form.
  const existing = useMagicShelfBooks(editId ?? '', 1);

  const [name, setName] = useState('');
  const [icon, setIcon] = useState('🪄');
  const [isSystem, setIsSystem] = useState(false);
  const [tree, setTree] = useState<RuleGroup>(() => ({ kind: 'group', key: nextKey(), condition: 'AND', rules: [newRule()] }));
  const [seeded, setSeeded] = useState(false);

  useEffect(() => {
    if (!editId || seeded || !existing.data) return;
    const d = existing.data as unknown as { name: string; icon: string; is_system?: boolean; rules?: Parameters<typeof groupFromStored>[0] };
    setName(d.name || '');
    setIcon(d.icon || '🪄');
    setIsSystem(Boolean(d.is_system));
    const loaded = groupFromStored(d.rules, nextKey);
    setTree(loaded.rules.length ? loaded : { ...loaded, rules: [newRule()] });
    setSeeded(true);
  }, [editId, seeded, existing.data]);
  const [previewData, setPreviewData] = useState<{ count: number; sample: string[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const ruleSet = () => groupToStored(tree);

  // Live preview (debounced) whenever the rules change and have at least one value.
  useEffect(() => {
    if (!someLeaf(tree, (r) => hasRuleValue(r.value) || r.operator.includes('empty'))) { setPreviewData(null); return; }
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      preview.mutate(ruleSet(), {
        onSuccess: (d) => { if (d.success) setPreviewData({ count: d.count, sample: d.sample_books }); },
        onError: () => setPreviewData(null),
      });
    }, 500);
    return () => { if (debounce.current) clearTimeout(debounce.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(tree)]);

  const fields = schemaQuery.data?.fields ?? [];
  const operatorMap = new Map((schemaQuery.data?.operators ?? []).map((operator) => [operator.type, operator]));
  const fieldFor = (id: string) => fields.find((field) => field.id === id);
  const operatorsFor = (id: string) => (fieldFor(id)?.operators ?? [])
    .map((operatorId) => operatorMap.get(operatorId))
    .filter((operator): operator is MagicRuleOperator => Boolean(operator));
  const inputType = (field: MagicRuleField, operator: MagicRuleOperator) => {
    if (operator.type === 'in_last_days' || operator.type === 'not_in_last_days') return 'number';
    if (field.type === 'date' || field.type === 'datetime') return 'date';
    if (field.type === 'integer' || field.type === 'double') return 'number';
    return 'text';
  };
  const setRule = (k: number, patch: Partial<MagicRule>) =>
    setTree((current) => updateNode(current, k, (node) => ({ ...node, ...patch }) as RuleLeaf));
  const setGroupCondition = (k: number, next: 'AND' | 'OR') =>
    setTree((current) => updateNode(current, k, (node) => ({ ...node, condition: next }) as RuleGroup));
  const appendTo = (k: number, node: RuleLeaf | RuleGroup) =>
    setTree((current) => updateNode(current, k, (group) => ({ ...group, rules: [...(group as RuleGroup).rules, node] }) as RuleGroup));
  const totalRules = leafCount(tree);

  const renderRuleValue = (rule: RuleLeaf, field: MagicRuleField, operator: MagicRuleOperator) => {
    if (operator.nb_inputs === 0) return <span className={styles.noValue} aria-hidden="true" />;
    if (field.input === 'select' || field.input === 'radio') {
      return (
        <select aria-label={`${t(field.label)} ${t('value')}`} value={String(rule.value ?? '')}
          onChange={(event) => setRule(rule.key, { value: event.target.value })}>
          {Object.entries(field.values ?? {}).map(([value, label]) => (
            <option key={value} value={value}>{t(String(label))}</option>
          ))}
        </select>
      );
    }
    if (operator.nb_inputs === 2) {
      const values = Array.isArray(rule.value) ? rule.value : ['', ''];
      return (
        <span className={styles.rangeInputs} role="group" aria-label={`${t(field.label)} ${t(operator.label)}`}>
          {[0, 1].map((index) => (
            <input key={index} value={values[index] ?? ''}
              aria-label={`${t(field.label)} ${index + 1}`}
              onChange={(event) => {
                const next = [...values];
                next[index] = event.target.value;
                setRule(rule.key, { value: next });
              }}
              type={inputType(field, operator)} />
          ))}
        </span>
      );
    }
    return (
      <input value={String(rule.value ?? '')}
        onChange={(event) => setRule(rule.key, { value: event.target.value })}
        aria-label={`${t(field.label)} ${t('value')}`} placeholder={t('value')}
        type={inputType(field, operator)}
        min={operator.type === 'in_last_days' || operator.type === 'not_in_last_days' ? 1 : undefined} />
    );
  };

  const renderRule = (r: RuleLeaf) => {
    const ops = operatorsFor(r.id);
    const field = fieldFor(r.id);
    const operator = operatorMap.get(r.operator) ?? ops[0];
    if (!field || !operator) return null;
    return (
      <div key={r.key} className={styles.ruleRow}>
        <select aria-label={t('Rule field')} value={r.id} onChange={(e) => {
          const id = e.target.value;
          const nextOperator = operatorsFor(id)[0];
          if (!nextOperator) return;
          setRule(r.key, { id, operator: nextOperator.type, value: blankValueFor(nextOperator) });
        }}>
          {fields.map((f) => <option key={f.id} value={f.id}>{t(f.label)}</option>)}
        </select>
        <select aria-label={t('Rule operator')} value={operator.type} onChange={(e) => {
          const nextOperator = operatorMap.get(e.target.value);
          if (nextOperator) setRule(r.key, { operator: nextOperator.type, value: blankValueFor(nextOperator) });
        }}>
          {ops.map((o) => <option key={o.type} value={o.type}>{t(o.label)}</option>)}
        </select>
        {renderRuleValue(r, field, operator)}
        <button className={styles.removeRule} onClick={() => setTree((current) => removeNode(current, r.key))}
          disabled={totalRules === 1} aria-label={t('Remove rule')}>
          <Trash2 size={15} aria-hidden="true" focusable={false} />
        </button>
      </div>
    );
  };

  // Groups nest to any depth, as the classic builder allows; the root group's
  // match selector lives in the row above the rules.
  const renderGroup = (group: RuleGroup, nested: boolean) => (
    <div key={group.key} className={nested ? styles.group : styles.rules}
      role={nested ? 'group' : undefined} aria-label={nested ? t('Rule group') : undefined}>
      {nested && (
        <div className={styles.groupHeader}>
          {t('Match')}
          <select aria-label={t('Match condition')} value={group.condition}
            onChange={(e) => setGroupCondition(group.key, e.target.value as 'AND' | 'OR')}>
            <option value="AND">{t('all rules')}</option>
            <option value="OR">{t('any rule')}</option>
          </select>
          <button className={styles.removeRule} onClick={() => setTree((current) => removeNode(current, group.key))}
            disabled={leafCount(group) === totalRules} aria-label={t('Remove group')}>
            <Trash2 size={15} aria-hidden="true" focusable={false} />
          </button>
        </div>
      )}
      {group.rules.map((node) => (node.kind === 'group' ? renderGroup(node, true) : renderRule(node)))}
      <div className={styles.addRow}>
        <button className={styles.addRule} onClick={() => appendTo(group.key, newRule())}>
          <Plus size={15} aria-hidden="true" focusable={false} /> {t('Add rule')}
        </button>
        <button className={styles.addRule} onClick={() => appendTo(group.key, newGroup())}>
          <Plus size={15} aria-hidden="true" focusable={false} /> {t('Add group')}
        </button>
      </div>
    </div>
  );

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

  if (schemaQuery.isLoading) {
    return <div className={styles.container}><h1 className={styles.title}>{t('Loading…')}</h1></div>;
  }
  if (schemaQuery.isError || fields.length === 0) {
    return <div className={styles.container}><p role="alert">{t('Could not load smart-shelf rules.')}</p></div>;
  }

  return (
    <div className={styles.container}>
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
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} disabled={isSystem}
            placeholder={t('e.g. Unread sci-fi')} aria-invalid={err ? true : undefined}
            aria-describedby={err ? 'magic-shelf-error' : undefined} />
        </label>
      </div>

      <div className={styles.matchRow}>
        {t('Match')}
        <select aria-label={t('Match condition')} value={tree.condition}
          onChange={(e) => setGroupCondition(tree.key, e.target.value as 'AND' | 'OR')}>
          <option value="AND">{t('all rules')}</option>
          <option value="OR">{t('any rule')}</option>
        </select>
      </div>

      {renderGroup(tree, false)}

      <div className={previewData ? styles.preview : undefined} role="status">
        {previewData && (
          <>
            <strong>{previewData.count}</strong> {t('books match')}
            {previewData.sample.length > 0 && (
              <span className={styles.sample}> — {previewData.sample.slice(0, 5).join(', ')}…</span>
            )}
          </>
        )}
      </div>

      {err && <p className={styles.err} role="alert" id="magic-shelf-error">{err}</p>}

      <div className={styles.actions}>
        <Button onClick={onSave} disabled={saving}>
          <Wand2 size={16} aria-hidden="true" focusable={false} /> {saving ? t('Saving…') : (editId ? t('Save changes') : t('Create smart shelf'))}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={saving}>{t('Cancel')}</Button>
      </div>
    </div>
  );
}
