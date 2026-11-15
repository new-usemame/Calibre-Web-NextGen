import { useNamedPreference, type NamedPreferenceOptions } from './useNamedPreference';

/** Storage key for the shelf drag-handle preference on book cards (#2475). */
export const SHELF_DRAG_HANDLES_HIDDEN_KEY = 'cwng:shelf-drag-handles-hidden-v1';

/** Whether the grip button on book cards is switched off.
 *
 *  @kanjieater asked for it in #2475: on a desktop the card itself already
 *  drags onto a shelf, so the grip is clutter. It stays on by default because
 *  it is the only drag path on touch screens and the keyboard route to the
 *  shelf picker; selecting books and using "Add to shelf" still works with it
 *  off. */
export function useShelfDragHandlesHidden(options: NamedPreferenceOptions = {}) {
  return useNamedPreference(
    'shelf_drag_handles_hidden', SHELF_DRAG_HANDLES_HIDDEN_KEY, false, options,
  );
}
