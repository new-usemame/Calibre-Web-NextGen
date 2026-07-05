import { User } from 'lucide-react';
import styles from './Avatar.module.css';

interface AvatarProps {
  /** `data:image/…;base64,…` URI from `Me.avatar`, or null/undefined for none. */
  src?: string | null;
  /** Rendered pixel size (width === height). */
  size?: number;
  /** Layout class from the host control (margins/colour for the glyph fallback). */
  className?: string;
}

/**
 * User profile picture (#668). Renders the custom picture set in the classic
 * profile-pictures panel; falls back to a neutral user glyph when the user has
 * none. The surrounding control always carries the accessible name (the
 * username), so the image is decorative (empty alt) and the glyph is aria-hidden.
 */
export function Avatar({ src, size = 32, className }: AvatarProps) {
  if (src) {
    return (
      <img
        src={src}
        alt=""
        className={className ? `${styles.image} ${className}` : styles.image}
        style={{ width: size, height: size }}
      />
    );
  }
  return <User size={size} className={className} aria-hidden="true" focusable={false} />;
}
