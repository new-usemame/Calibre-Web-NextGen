/* Skip-to-content link (WCAG 2.2 SC 2.4.1 Bypass Blocks).
 *
 * Must be the first focusable element on the page. Visually hidden until it
 * receives keyboard focus, then it appears top-left. Targets <main id="main">.
 */
import { useT } from '../lib/i18n';
import styles from './SkipLink.module.css';

export function SkipLink() {
  const t = useT();
  return (
    <a href="#main" className={styles.skipLink}>
      {t('Skip to content')}
    </a>
  );
}
