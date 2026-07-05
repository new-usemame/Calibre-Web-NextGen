/* Focus trap for dialogs, drawers, and popovers.
 *
 * Extracted verbatim-in-spirit from the exemplary CoverPicker.ConfirmModal
 * (the audit's reference dialog): focus into the container on open, trap Tab
 * within it, Escape closes, and focus restores to the trigger on unmount.
 *
 * Reuse this for every overlay that takes focus — mobile drawer, reader TOC,
 * reader highlight popover — so we never re-implement (and re-break) the trap.
 * See ~/.claude/skills/CWNG_a11y/references/patterns.md → "Modal / dialog".
 */
import { useEffect, type RefObject } from 'react';

const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/** All tabbable descendants of `node`, in DOM order, excluding disabled ones. */
export function getFocusable(node: HTMLElement | null): HTMLElement[] {
  if (!node) return [];
  return Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hasAttribute('disabled') && el.getAttribute('aria-hidden') !== 'true',
  );
}

interface FocusTrapOptions {
  /** Invoked when Escape is pressed inside the trap. */
  onClose: () => void;
  /** Which focusable receives initial focus. 'last' suits confirm dialogs. */
  initialFocus?: 'first' | 'last';
  /** When false the trap is inert (e.g. a closed drawer). Defaults to true. */
  active?: boolean;
}

/**
 * Trap keyboard focus inside `ref` while `active`. Handles Tab wrap, Escape,
 * initial focus, and focus restoration. The container should have tabIndex={-1}
 * and role="dialog" aria-modal="true" (or role="menu"/appropriate) + an
 * accessible name.
 */
export function useFocusTrap<T extends HTMLElement>(
  ref: RefObject<T>,
  { onClose, initialFocus = 'first', active = true }: FocusTrapOptions,
): void {
  useEffect(() => {
    if (!active) return;
    const prevFocus = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const initial = getFocusable(node);
    const target = initialFocus === 'last' ? initial[initial.length - 1] : initial[0];
    (target ?? node)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== 'Tab') return;
      const els = getFocusable(node);
      if (!els.length) {
        e.preventDefault();
        node?.focus();
        return;
      }
      const first = els[0];
      const last = els[els.length - 1];
      const activeEl = document.activeElement;
      if (e.shiftKey && (activeEl === first || activeEl === node)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && activeEl === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      // Restore focus to the trigger so keyboard users aren't dumped to <body>.
      prevFocus?.focus?.();
    };
  }, [ref, onClose, initialFocus, active]);
}
