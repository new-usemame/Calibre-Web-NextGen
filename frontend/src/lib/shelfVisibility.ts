export type ShelfVisibilityAction = 'make-public' | 'make-private';

type ShelfVisibilityCapabilities = {
  canEdit: boolean;
  canMakePublic: boolean;
  isPublic: boolean;
};

export function getShelfVisibilityAction({
  canEdit,
  canMakePublic,
  isPublic,
}: ShelfVisibilityCapabilities): ShelfVisibilityAction | null {
  if (!canEdit) return null;
  if (isPublic) return 'make-private';
  return canMakePublic ? 'make-public' : null;
}
