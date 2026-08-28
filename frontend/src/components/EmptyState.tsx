import { Library, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import styles from './EmptyState.module.css';

interface EmptyStateProps {
  message: string;
  title?: string;
  icon?: LucideIcon;
  children?: ReactNode;
}

export function EmptyState({ message, title, icon: Icon = Library, children }: EmptyStateProps) {
  return (
    <div className={styles.wrap}>
      <Icon size={40} className={styles.icon} aria-hidden="true" focusable={false} />
      {title && <h2 className={styles.title}>{title}</h2>}
      <p className={styles.msg}>{message}</p>
      {children && <div className={styles.actions}>{children}</div>}
    </div>
  );
}
