// #609: the brand honors the admin-configured instance title everywhere the
// classic UI does (navbar + login card). A custom title renders as plain text;
// the stock name keeps the two-tone accent treatment.
export const DEFAULT_INSTANCE_NAME = 'Calibre-Web NextGen';

interface BrandNameProps {
  /** Server-provided instance_name (from /auth/config or /auth/me). */
  name?: string;
  accentClassName?: string;
}

export function BrandName({ name, accentClassName }: BrandNameProps) {
  if (name && name !== DEFAULT_INSTANCE_NAME) {
    return <>{name}</>;
  }
  return (
    <>
      Calibre-Web <span className={accentClassName}>NextGen</span>
    </>
  );
}
