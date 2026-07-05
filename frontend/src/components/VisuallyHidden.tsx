/* Render content for screen readers only (off-screen, still in the a11y tree).
 * Use for accessible names/labels that would be redundant or noisy on screen —
 * e.g. a text alternative for a color swatch, or extra context on an icon button.
 */
import type { ElementType, ReactNode } from 'react';

export function VisuallyHidden({
  children,
  as: Tag = 'span',
}: {
  children: ReactNode;
  as?: ElementType;
}) {
  return <Tag className="sr-only">{children}</Tag>;
}
