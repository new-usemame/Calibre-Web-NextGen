import { useState, useEffect } from 'react';
import { Mail, Globe, KeyRound, Check, Smartphone, Trash2, Copy } from 'lucide-react';
import {
  useAccount, useMe, useUpdateProfile, useChangePassword,
  useCreateAppPassword, useRevokeAppPassword,
} from '../lib/queries';
import { Avatar } from '../components/Avatar';
import { Button } from '../components/Button';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { ApiError } from '../lib/api';
import { UI_BODY_FONTS, UI_DISPLAY_FONTS } from '../lib/fonts';
import { useT } from '../lib/i18n';
import styles from './Account.module.css';

const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin', upload: 'Upload', edit: 'Edit metadata', download: 'Download',
  delete_books: 'Delete books', edit_shelfs: 'Edit public shelves', viewer: 'Viewer',
  passwd: 'Change password',
};

export function Account() {
  const t = useT();
  const { data: account, isLoading, error } = useAccount();
  const avatar = useMe().data?.avatar;
  const updateProfile = useUpdateProfile();
  const changePassword = useChangePassword();
  const createAppPw = useCreateAppPassword();
  const revokeAppPw = useRevokeAppPassword();

  // Profile form
  const [email, setEmail] = useState('');
  const [kindleMail, setKindleMail] = useState('');
  const [kindleSubject, setKindleSubject] = useState('');
  const [koboSync, setKoboSync] = useState(false);
  const [opdsSync, setOpdsSync] = useState(false);
  const [locale, setLocale] = useState('');
  const [defaultLanguage, setDefaultLanguage] = useState('');
  const [uiFontBody, setUiFontBody] = useState('');
  const [uiFontDisplay, setUiFontDisplay] = useState('');
  const [profileMsg, setProfileMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // App passwords
  const [appPwLabel, setAppPwLabel] = useState('');
  const [newToken, setNewToken] = useState<{ label: string; token: string } | null>(null);
  const [appPwMsg, setAppPwMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Password form
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Seed the profile form once the account loads.
  useEffect(() => {
    if (!account) return;
    setEmail(account.email);
    setKindleMail(account.kindle_mail);
    setKindleSubject(account.kindle_mail_subject);
    setKoboSync(account.kobo_only_shelves_sync);
    setOpdsSync(account.opds_only_shelves_sync);
    setLocale(account.locale);
    setDefaultLanguage(account.default_language);
    setUiFontBody(account.ui_font_body || '');
    setUiFontDisplay(account.ui_font_display || '');
  }, [account]);

  if (isLoading) return <SpinnerCentered size={40} />;
  if (error || !account) {
    return (
      <main className={styles.container}>
        <EmptyState message={error instanceof Error ? error.message : t('Could not load your account.')} />
      </main>
    );
  }

  const onSaveProfile = (e: React.FormEvent) => {
    e.preventDefault();
    setProfileMsg(null);
    updateProfile.mutate(
      {
        email, kindle_mail: kindleMail, kindle_mail_subject: kindleSubject,
        kobo_only_shelves_sync: koboSync, opds_only_shelves_sync: opdsSync,
        locale, default_language: defaultLanguage,
        ui_font_body: uiFontBody, ui_font_display: uiFontDisplay,
      },
      {
        onSuccess: () => setProfileMsg({ ok: true, text: t('Profile saved.') }),
        onError: (err) =>
          setProfileMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Could not save.') }),
      },
    );
  };

  const onCreateAppPw = (e: React.FormEvent) => {
    e.preventDefault();
    setAppPwMsg(null);
    setNewToken(null);
    createAppPw.mutate(appPwLabel.trim(), {
      onSuccess: (r) => { setNewToken({ label: r.label, token: r.token }); setAppPwLabel(''); },
      onError: (err) =>
        setAppPwMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Could not create.') }),
    });
  };

  const onChangePassword = (e: React.FormEvent) => {
    e.preventDefault();
    setPwMsg(null);
    if (newPw !== confirmPw) {
      setPwMsg({ ok: false, text: t('New passwords do not match.') });
      return;
    }
    changePassword.mutate(
      { current_password: currentPw, new_password: newPw },
      {
        onSuccess: () => {
          setPwMsg({ ok: true, text: t('Password changed.') });
          setCurrentPw('');
          setNewPw('');
          setConfirmPw('');
        },
        onError: (err) =>
          setPwMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Could not change password.') }),
      },
    );
  };

  const activeRoles = Object.entries(account.role).filter(([, v]) => v);

  return (
    <main className={styles.container}>
      <h1 className={styles.title}>{t('Account')}</h1>

      {/* Identity */}
      <section className={styles.card}>
        <div className={styles.identity}>
          <Avatar src={avatar} size={48} className={styles.avatar} />
          <div>
            <p className={styles.name}>{account.name}</p>
            <div className={styles.roles}>
              {activeRoles.map(([key]) => (
                <span key={key} className={styles.roleBadge}>{t(ROLE_LABELS[key] ?? key)}</span>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Profile */}
      <form className={styles.card} onSubmit={onSaveProfile}>
        <h2 className={styles.cardTitle}><Mail size={16} aria-hidden="true" focusable={false} /> {t('Profile')}</h2>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="acc-email">{t('Email')}</label>
          <input id="acc-email" type="email" className={styles.input}
            value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>

        <div className={styles.row}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="acc-kindle">{t('Send-to-eReader email')}</label>
            <input id="acc-kindle" type="text" className={styles.input}
              value={kindleMail} onChange={(e) => setKindleMail(e.target.value)}
              placeholder="kindle@kindle.com" />
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="acc-ksubj">{t('eReader email subject')}</label>
            <input id="acc-ksubj" type="text" className={styles.input}
              value={kindleSubject} onChange={(e) => setKindleSubject(e.target.value)}
              placeholder="(default)" />
          </div>
        </div>

        <div className={styles.field}>
          <label className={styles.toggle}>
            <input type="checkbox" checked={koboSync} onChange={(e) => setKoboSync(e.target.checked)} />
            {t('Sync only selected shelves to Kobo')}
          </label>
          <label className={styles.toggle}>
            <input type="checkbox" checked={opdsSync} onChange={(e) => setOpdsSync(e.target.checked)} />
            {t('Expose only selected shelves over OPDS')}
          </label>
        </div>

        <div className={styles.row}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="acc-locale"><Globe size={13} aria-hidden="true" focusable={false} /> {t('Interface language')}</label>
            <select id="acc-locale" className={styles.input}
              value={locale} onChange={(e) => setLocale(e.target.value)}>
              {account.locales.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="acc-lang">{t('Show books in language')}</label>
            <select id="acc-lang" className={styles.input}
              value={defaultLanguage} onChange={(e) => setDefaultLanguage(e.target.value)}>
              {account.languages.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
          </div>
        </div>

        {/* #701 — per-user UI fonts. Each option previews in its own family
            (honoured by Firefox/Safari; Chrome shows the label plainly). */}
        <div className={styles.row}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="acc-font-body">{t('UI body font')}</label>
            <select id="acc-font-body" className={styles.input}
              style={{ fontFamily: uiFontBody ? UI_BODY_FONTS.find((f) => f.key === uiFontBody)?.stack : undefined }}
              value={uiFontBody} onChange={(e) => setUiFontBody(e.target.value)}>
              {UI_BODY_FONTS.map((f) => (
                <option key={f.key || 'default'} value={f.key}
                  style={{ fontFamily: f.stack || undefined }}>{t(f.label)}</option>
              ))}
            </select>
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="acc-font-display">{t('UI display font')}</label>
            <select id="acc-font-display" className={styles.input}
              style={{ fontFamily: uiFontDisplay ? UI_DISPLAY_FONTS.find((f) => f.key === uiFontDisplay)?.stack : undefined }}
              value={uiFontDisplay} onChange={(e) => setUiFontDisplay(e.target.value)}>
              {UI_DISPLAY_FONTS.map((f) => (
                <option key={f.key || 'default'} value={f.key}
                  style={{ fontFamily: f.stack || undefined }}>{t(f.label)}</option>
              ))}
            </select>
          </div>
        </div>

        <div className={styles.actions}>
          <Button type="submit" disabled={updateProfile.isPending}>
            <Check size={16} aria-hidden="true" focusable={false} /> {t('Save profile')}
          </Button>
          {/* Persistent live region so the save result is announced (SC 4.1.3). */}
          <span
            className={profileMsg ? (profileMsg.ok ? styles.msgOk : styles.msgErr) : undefined}
            role="status"
          >
            {profileMsg?.text}
          </span>
        </div>
      </form>

      {/* Password */}
      {account.can_change_password && (
        <form className={styles.card} onSubmit={onChangePassword}>
          <h2 className={styles.cardTitle}><KeyRound size={16} aria-hidden="true" focusable={false} /> {t('Change password')}</h2>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="acc-cur">{t('Current password')}</label>
            <input id="acc-cur" type="password" autoComplete="current-password" className={styles.input}
              value={currentPw} onChange={(e) => setCurrentPw(e.target.value)} />
          </div>
          <div className={styles.row}>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="acc-new">{t('New password')}</label>
              <input id="acc-new" type="password" autoComplete="new-password" className={styles.input}
                value={newPw} onChange={(e) => setNewPw(e.target.value)}
                aria-invalid={pwMsg && !pwMsg.ok ? true : undefined}
                aria-describedby={pwMsg && !pwMsg.ok ? 'acc-pw-msg' : undefined} />
            </div>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="acc-confirm">{t('Confirm new password')}</label>
              <input id="acc-confirm" type="password" autoComplete="new-password" className={styles.input}
                value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)}
                aria-invalid={pwMsg && !pwMsg.ok ? true : undefined}
                aria-describedby={pwMsg && !pwMsg.ok ? 'acc-pw-msg' : undefined} />
            </div>
          </div>

          <div className={styles.actions}>
            <Button type="submit" variant="ghost"
              disabled={changePassword.isPending || !currentPw || !newPw}>
              <KeyRound size={15} aria-hidden="true" focusable={false} /> {t('Update password')}
            </Button>
            <span
              id="acc-pw-msg"
              className={pwMsg ? (pwMsg.ok ? styles.msgOk : styles.msgErr) : undefined}
              role="status"
            >
              {pwMsg?.text}
            </span>
          </div>
        </form>
      )}

      {/* App passwords (for OPDS readers / KOReader sync over HTTP Basic) */}
      <section className={styles.card}>
        <h2 className={styles.cardTitle}><Smartphone size={16} aria-hidden="true" focusable={false} /> {t('App passwords')}</h2>
        <p className={styles.hint}>
          {t('Use these to connect OPDS readers or KOReader sync without your main password.')}
        </p>

        {newToken && (
          <div className={styles.tokenBox} role="status">
            <p className={styles.tokenLabel}>
              {t('New password for “{label}” — copy it now, it won’t be shown again:', { label: newToken.label })}
            </p>
            <div className={styles.tokenRow}>
              <code className={styles.token}>{newToken.token}</code>
              <button type="button" className={styles.copyBtn}
                onClick={() => navigator.clipboard?.writeText(newToken.token)}>
                <Copy size={14} aria-hidden="true" focusable={false} /> {t('Copy')}
              </button>
            </div>
          </div>
        )}

        {account.app_passwords.length > 0 && (
          <ul className={styles.appPwList}>
            {account.app_passwords.map((ap) => (
              <li key={ap.id} className={styles.appPwItem}>
                <span className={styles.appPwName}>{ap.label}</span>
                <button type="button" className={styles.revokeBtn}
                  disabled={revokeAppPw.isPending}
                  onClick={() => revokeAppPw.mutate(ap.id)}
                  aria-label={t('Revoke {label}', { label: ap.label })}>
                  <Trash2 size={14} aria-hidden="true" focusable={false} />
                </button>
              </li>
            ))}
          </ul>
        )}

        <form className={styles.appPwForm} onSubmit={onCreateAppPw}>
          <input type="text" className={styles.input} value={appPwLabel}
            onChange={(e) => setAppPwLabel(e.target.value)}
            aria-label={t('App password label')}
            placeholder={t('Label (e.g. KOReader on phone)')} maxLength={64} />
          <Button type="submit" variant="ghost" disabled={createAppPw.isPending || !appPwLabel.trim()}>
            <KeyRound size={15} aria-hidden="true" focusable={false} /> {t('Generate')}
          </Button>
        </form>
        <span className={appPwMsg ? (appPwMsg.ok ? styles.msgOk : styles.msgErr) : undefined} role="status">
          {appPwMsg?.text}
        </span>
      </section>
    </main>
  );
}
