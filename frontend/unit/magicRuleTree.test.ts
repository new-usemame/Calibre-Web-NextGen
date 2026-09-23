import assert from 'node:assert/strict';
import test from 'node:test';
import {
  groupFromStored, groupToStored, leafCount, removeNode, someLeaf, updateNode,
} from '../src/lib/magicRuleTree.ts';
import type { RuleGroup, RuleLeaf } from '../src/lib/magicRuleTree.ts';

// Exactly what the classic builder's getRules() returns after "Add group" (#2257).
const classic = {
  condition: 'AND',
  rules: [
    { id: 'tag', field: 'tag', type: 'string', input: 'text', operator: 'not_contains', value: 'Horror' },
    {
      condition: 'OR',
      rules: [
        { id: 'tag', field: 'tag', type: 'string', input: 'text', operator: 'contains', value: 'Fiction' },
        { condition: 'AND', rules: [{ id: 'series', operator: 'is_empty', value: null }] },
      ],
    },
  ],
  valid: true,
};

const keys = () => { let n = 0; return () => ++n; };

test('a nested rule set survives load and save with every group, condition and rule intact', () => {
  const saved = groupToStored(groupFromStored(classic as never, keys()));
  assert.deepEqual(saved, {
    condition: 'AND',
    rules: [
      { id: 'tag', operator: 'not_contains', value: 'Horror' },
      {
        condition: 'OR',
        rules: [
          { id: 'tag', operator: 'contains', value: 'Fiction' },
          { condition: 'AND', rules: [{ id: 'series', operator: 'is_empty', value: '' }] },
        ],
      },
    ],
  });
});

test('walking a tree with groups and a malformed rule never throws', () => {
  const tree = groupFromStored({ condition: 'or', rules: [{ condition: 'AND', rules: [{} as never] }] } as never, keys());
  assert.equal(tree.condition, 'OR');
  assert.equal(leafCount(tree), 1);
  assert.equal(someLeaf(tree, (rule) => rule.operator.includes('empty')), false);
  assert.equal(someLeaf(groupFromStored(classic as never, keys()), (rule) => rule.operator.includes('empty')), true);
});

test('editing reaches a nested rule and removing its last rule removes the emptied group', () => {
  const tree = groupFromStored(classic as never, keys());
  const inner = tree.rules[1] as RuleGroup;
  const fiction = inner.rules[0] as RuleLeaf;
  const edited = updateNode(tree, fiction.key, (node) => ({ ...node, value: 'Fantasy' }) as RuleLeaf);
  assert.equal(((edited.rules[1] as RuleGroup).rules[0] as RuleLeaf).value, 'Fantasy');
  assert.equal((tree.rules[1] as RuleGroup).rules[0], fiction, 'the original tree is not mutated');

  const deepest = (inner.rules[1] as RuleGroup).rules[0];
  const pruned = removeNode(tree, deepest.key);
  assert.deepEqual(groupToStored(pruned).rules[1], {
    condition: 'OR', rules: [{ id: 'tag', operator: 'contains', value: 'Fiction' }],
  });
  assert.equal(leafCount(pruned), 2);
});
