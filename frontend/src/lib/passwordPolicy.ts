import type { PasswordPolicy } from './api';
import type { TFunction } from './i18n';

/**
 * Human-readable summary of the configured password policy, e.g.
 * "Password requirements: at least 8 characters, including a number, an
 * uppercase letter." Returns null when no policy is enforced.
 */
export function passwordRequirementsText(
  policy: PasswordPolicy | null | undefined,
  t: TFunction,
): string | null {
  if (!policy || !policy.enabled) return null;
  const parts: string[] = [];
  if (policy.number) parts.push(t('a number'));
  if (policy.lower) parts.push(t('a lowercase letter'));
  if (policy.upper) parts.push(t('an uppercase letter'));
  if (policy.letter) parts.push(t('a letter'));
  if (policy.special) parts.push(t('a special character'));
  const length = policy.min_length > 0
    ? (policy.min_length === 1 ? t('at least 1 character') : t('at least {count} characters', { count: policy.min_length }))
    : '';
  if (!length && parts.length === 0) return null;
  const rules = [length, parts.length ? t('including {rules}', { rules: parts.join(', ') }) : '']
    .filter(Boolean)
    .join(', ');
  return `${t('Password requirements:')} ${rules}.`;
}
