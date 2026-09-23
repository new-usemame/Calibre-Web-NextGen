import type { MagicRule, MagicRuleNode, MagicRuleSet } from './queries';

/** Editable smart-shelf rule tree. A stored rule set may nest groups to any
 *  depth — the classic builder's "Add group" writes `{condition, rules}` nodes —
 *  and the server evaluates them recursively (`build_query_from_rules`), so the
 *  editor must load, edit and save the same shape or it corrupts the shelf. */
export type RuleLeaf = { kind: 'rule'; key: number; id: string; operator: string; value: string | string[] };
export type RuleGroup = { kind: 'group'; key: number; condition: 'AND' | 'OR'; rules: RuleNode[] };
export type RuleNode = RuleLeaf | RuleGroup;

/** Same test the server uses: a node carrying `condition` is a group. */
export const isStoredGroup = (node: unknown): node is MagicRuleSet =>
  typeof node === 'object' && node !== null && 'condition' in node;

const conditionOf = (value: unknown): 'AND' | 'OR' =>
  String(value ?? '').toUpperCase() === 'OR' ? 'OR' : 'AND';

export function groupFromStored(stored: Partial<MagicRuleSet> | null | undefined, nextKey: () => number): RuleGroup {
  const rules: RuleNode[] = (Array.isArray(stored?.rules) ? stored.rules : []).map((node): RuleNode => {
    if (isStoredGroup(node)) return groupFromStored(node, nextKey);
    const rule = (node ?? {}) as Partial<MagicRule>;
    return {
      kind: 'rule',
      key: nextKey(),
      id: String(rule.id ?? ''),
      operator: String(rule.operator ?? ''),
      value: Array.isArray(rule.value) ? rule.value.map(String) : String(rule.value ?? ''),
    };
  });
  return { kind: 'group', key: nextKey(), condition: conditionOf(stored?.condition), rules };
}

export function groupToStored(group: RuleGroup): MagicRuleSet {
  return {
    condition: group.condition,
    rules: group.rules.map((node): MagicRuleNode => (node.kind === 'group'
      ? groupToStored(node)
      : { id: node.id, operator: node.operator, value: node.value })),
  };
}

export const leafCount = (group: RuleGroup): number =>
  group.rules.reduce((total, node) => total + (node.kind === 'group' ? leafCount(node) : 1), 0);

export const someLeaf = (group: RuleGroup, predicate: (rule: RuleLeaf) => boolean): boolean =>
  group.rules.some((node) => (node.kind === 'group' ? someLeaf(node, predicate) : predicate(node)));

/** Replace the node with `key` (anywhere in the tree) by `update(node)`. */
export function updateNode(group: RuleGroup, key: number, update: (node: RuleNode) => RuleNode): RuleGroup {
  if (group.key === key) return update(group) as RuleGroup;
  return {
    ...group,
    rules: group.rules.map((node) => {
      if (node.key === key) return update(node);
      return node.kind === 'group' ? updateNode(node, key, update) : node;
    }),
  };
}

/** Remove the node with `key`; a nested group left without rules goes with it,
 *  because the classic builder refuses to save an empty group. */
export function removeNode(group: RuleGroup, key: number): RuleGroup {
  return {
    ...group,
    rules: group.rules
      .filter((node) => node.key !== key)
      .map((node) => (node.kind === 'group' ? removeNode(node, key) : node))
      .filter((node) => node.kind === 'rule' || node.rules.length > 0),
  };
}
