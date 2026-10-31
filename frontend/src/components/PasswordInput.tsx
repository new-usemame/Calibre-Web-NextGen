import { useState, type InputHTMLAttributes } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { useT } from '../lib/i18n';
import styles from './PasswordInput.module.css';

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  /** Class for the wrapper element (layout); `className` goes to the input. */
  wrapClassName?: string;
};

/** Password input with a show/hide toggle. Drop-in for <input type="password">. */
export function PasswordInput({ wrapClassName, className, ...rest }: Props) {
  const t = useT();
  const [shown, setShown] = useState(false);
  const label = shown ? t('Hide password') : t('Show password');
  return (
    <span className={`${styles.wrap}${wrapClassName ? ` ${wrapClassName}` : ''}`}>
      <input {...rest} type={shown ? 'text' : 'password'}
        className={`${styles.input}${className ? ` ${className}` : ''}`} />
      <button type="button" className={styles.toggle} onClick={() => setShown((v) => !v)}
        aria-label={label} aria-pressed={shown} title={label}>
        {shown
          ? <EyeOff size={16} aria-hidden="true" focusable={false} />
          : <Eye size={16} aria-hidden="true" focusable={false} />}
      </button>
    </span>
  );
}
