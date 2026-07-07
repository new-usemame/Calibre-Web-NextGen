import { Star } from 'lucide-react';
import { useT } from '../lib/i18n';
import styles from './StarRating.module.css';

/** Read-only star display for a Calibre 0–10 rating (half-star granularity:
 *  9 → 4.5 stars). Renders five stars with fractional fill so the new-UI book
 *  page reaches parity with the classic detail page's rating block. Exposed as a
 *  single labelled image to assistive tech rather than five ambiguous glyphs. */
export function StarRating({ rating, size = 16 }: { rating: number; size?: number }) {
  const t = useT();
  const stars = Math.max(0, Math.min(5, rating / 2));
  const shown = Number.isInteger(stars) ? String(stars) : stars.toFixed(1);
  const label = t('Rated {rating} out of 5', { rating: shown });
  return (
    <div className={styles.stars} role="img" aria-label={label} title={label}>
      {Array.from({ length: 5 }, (_, i) => {
        // 0..1 fill fraction for this slot (e.g. 4.5 stars → slot 4 is half full).
        const fill = Math.max(0, Math.min(1, stars - i));
        return (
          <span className={styles.slot} style={{ width: size, height: size }} key={i}>
            <Star size={size} className={styles.base} aria-hidden="true" focusable={false} />
            {fill > 0 && (
              <span className={styles.fillWrap} style={{ width: `${fill * 100}%` }}>
                <Star size={size} className={styles.fill} fill="currentColor" strokeWidth={0}
                      aria-hidden="true" focusable={false} />
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}
