import { test } from 'node:test';
import assert from 'node:assert/strict';
import { passwordRequirementsText } from '../src/lib/passwordPolicy.ts';
import type { PasswordPolicy } from '../src/lib/api.ts';

const t = (key: string, vars?: Record<string, string | number>) =>
  key.replace(/\{(\w+)\}/g, (_m, n: string) => String(vars?.[n] ?? `{${n}}`));

const base: PasswordPolicy = {
  enabled: true, min_length: 8, number: true, lower: true, upper: true, letter: false, special: true,
};

test('lists every enforced rule', () => {
  assert.equal(
    passwordRequirementsText(base, t),
    'Password requirements: at least 8 characters, including a number, a lowercase letter, an uppercase letter, a special character.',
  );
});

test('returns null when the policy is disabled or missing', () => {
  assert.equal(passwordRequirementsText({ ...base, enabled: false }, t), null);
  assert.equal(passwordRequirementsText(undefined, t), null);
});

test('handles singular length and length-only policies', () => {
  const only = { ...base, min_length: 1, number: false, lower: false, upper: false, special: false };
  assert.equal(passwordRequirementsText(only, t), 'Password requirements: at least 1 character.');
});

test('does not list "a letter" when a case rule already requires one', () => {
  const both = passwordRequirementsText({ ...base, letter: true }, t) ?? '';
  assert.equal((both.match(/\ba letter\b/g) ?? []).length, 0);
  const letterOnly = passwordRequirementsText({ ...base, lower: false, upper: false, letter: true }, t) ?? '';
  assert.match(letterOnly, /including a number, a letter, a special character/);
});
