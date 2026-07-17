import styles from './KofiMark.module.css';

export const KOFI_URL = 'https://ko-fi.com/calibrewebnextgen';

export function KofiMark({ size = 16 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
      className={styles.mark}
    >
      <path className={styles.cup} d="M3.5 6.5h13v6.1a5 5 0 0 1-5 5h-3a5 5 0 0 1-5-5V6.5Z" />
      <path className={styles.handle} d="M16.5 8h1.75a2.75 2.75 0 0 1 0 5.5H16.2" />
      <path className={styles.heart} d="M10 14.4 6.9 11.5a1.9 1.9 0 0 1 2.7-2.7l.4.4.4-.4a1.9 1.9 0 1 1 2.7 2.7L10 14.4Z" />
    </svg>
  );
}
