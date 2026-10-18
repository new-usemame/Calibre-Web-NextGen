import type { PasswordPolicy } from '../lib/api';
import { passwordRequirementsText } from '../lib/passwordPolicy';
import { useT } from '../lib/i18n';
import styles from './PasswordPolicyHint.module.css';

/** Shows the configured password rules; renders nothing when no policy applies. */
export function PasswordPolicyHint({ policy, id }: { policy: PasswordPolicy | null | undefined; id: string }) {
  const t = useT();
  const text = passwordRequirementsText(policy, t);
  if (!text) return null;
  return <span id={id} className={styles.hint}>{text}</span>;
}
