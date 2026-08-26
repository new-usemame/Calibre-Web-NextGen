import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { getShelfVisibilityAction } from '../../src/lib/shelfVisibility.ts';

describe('getShelfVisibilityAction', () => {
  test('does not offer make public to an owner without the edit_shelfs role', () => {
    assert.equal(getShelfVisibilityAction({
      canEdit: true,
      canMakePublic: false,
      isPublic: false,
    }), null);
  });

  test('offers make public to an owner with the edit_shelfs role', () => {
    assert.equal(getShelfVisibilityAction({
      canEdit: true,
      canMakePublic: true,
      isPublic: false,
    }), 'make-public');
  });

  test('offers make private to any editor of a public shelf', () => {
    assert.equal(getShelfVisibilityAction({
      canEdit: true,
      canMakePublic: false,
      isPublic: true,
    }), 'make-private');
  });

  test('does not offer a visibility action to a viewer without edit access', () => {
    assert.equal(getShelfVisibilityAction({
      canEdit: false,
      canMakePublic: true,
      isPublic: true,
    }), null);
  });
});
