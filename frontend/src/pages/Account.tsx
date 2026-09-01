import { useState, useEffect } from 'react';
import { Mail, Globe, KeyRound, Check, CheckCheck, Smartphone, Trash2, Copy, PenLine } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'wouter';
import {
  useAccount, useMe, useUpdateProfile, useChangePassword,
  useCreateAppPassword, useRevokeAppPassword,
  useKoboTwoWayAnnotations, useUpdateKoboTwoWayAnnotations, useSetKoboTwoWayBook,
  useUpdateLibraryMode,
} from '../lib/queries';
import { Avatar } from '../components/Avatar';
import { Button } from '../components/Button';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { ApiError } from '../lib/api';
import { apiGet } from '../lib/api';
import { UI_BODY_FONTS, UI_DISPLAY_FONTS } from '../lib/fonts';
import { THEMES, resolveTheme } from '../lib/themes';
import { useT } from '../lib/i18n';
import { authorityLabel, authorityTone, opaqueLabel } from '../lib/koboTwoWay';
import styles from './Account.module.css';
import { useAnnouncer } from '../lib/a11y/announcer';

const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin', upload: 'Upload', edit: 'Edit metadata', download: 'Download',
  delete_books: 'Delete books', edit_shelfs: 'Edit public shelves', viewer: 'Viewer',
  passwd: 'Change password',
};

/* Static tone → class map (CSS-module friendly; a computed key would defeat
 * grep-ability and dead-class checks). */
const STATE_TONE_CLASS: Record<string, string> = {
  muted: styles.stateMuted,
  info: styles.stateInfo,
  ok: styles.stateOk,
  warn: styles.stateWarn,
};

export function Account() {
  const t = useT();
  const announce = useAnnouncer();
  const { data: account, isLoading, error } = useAccount();
  const me = useMe().data;
  const avatar = me?.avatar;
  const updateProfile = useUpdateProfile();
  const changePassword = useChangePassword();
  const createAppPw = useCreateAppPassword();
  const revokeAppPw = useRevokeAppPassword();
  const devices = useQuery<{ devices: { public_id: string; label: string; annotation_count: number }[] }>({
    queryKey: ['annotation-devices'], queryFn: () => apiGet('/api/annotations/devices?active=true'),
  });
  const updateLibraryMode = useUpdateLibraryMode();
  const [libraryModeError, setLibraryModeError] = useState('');

  // Kobo two-way annotation sync (Stage 0 — a preference surface over a
  // feature that is still inert; nothing here makes a book sync).
  const twoWay = useKoboTwoWayAnnotations({
    enabled: !!me && !me.role?.anonymous && !!me.features?.kobo_two_way_annotations,
  });
  const updateTwoWay = useUpdateKoboTwoWayAnnotations();
  const setTwoWayBook = useSetKoboTwoWayBook();
  const [twoWayMsg, setTwoWayMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [twoWayBookMsg, setTwoWayBookMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Profile form
  const [email, setEmail] = useState('');
  const [kindleMail, setKindleMail] = useState('');
  const [kindleSubject, setKindleSubject] = useState('');
  const [mailBody, setMailBody] = useState('');
  const [koboSync, setKoboSync] = useState(false);
  const [opdsSync, setOpdsSync] = useState(false);
  const [locale, setLocale] = useState('');
  const [defaultLanguage, setDefaultLanguage] = useState('');
  const [uiFontBody, setUiFontBody] = useState('');
  const [uiFontDisplay, setUiFontDisplay] = useState('');
  const [theme, setTheme] = useState('dark');
  const [profileMsg, setProfileMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [themeMsg, setThemeMsg] = useState<{ ok: boolean; text: string } | null>(null);

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
    setMailBody(account.mail_body_text ?? '');
    setKoboSync(account.kobo_only_shelves_sync);
    setOpdsSync(account.opds_only_shelves_sync);
    setLocale(account.locale);
    setDefaultLanguage(account.default_language);
    setTheme(account.theme || 'dark');
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
        ...(account.mail_body_text !== null ? { mail_body_text: mailBody } : {}),
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

  const onThemeChange = (slug: string) => {
    const previousTheme = theme;
    setThemeMsg(null);
    setTheme(slug);
    document.documentElement.setAttribute('data-theme', resolveTheme(slug));
    localStorage.setItem('cwng.theme', slug);
    updateProfile.mutate(
      { theme: slug },
      {
        onSuccess: () => setThemeMsg({ ok: true, text: t('Theme saved.') }),
        onError: () => {
          // The server-side User.theme value is the source of truth. Keep the
          // live preview optimistic, but never leave an unsaved palette active.
          setTheme(previousTheme);
          document.documentElement.setAttribute('data-theme', resolveTheme(previousTheme));
          localStorage.setItem('cwng.theme', previousTheme);
          setThemeMsg({ ok: false, text: t('Could not save theme.') });
        },
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
  const selectedTheme = THEMES.find((o) => o.slug === theme);

  /* Non-optimistic on purpose: this preference guards a feature that can
   * destroy device-side annotations once it goes live, so the control shows
   * the SERVER state and snaps back on failure rather than pretending. */
  const onTwoWaySave = (patch: { enabled?: boolean; scope?: 'all' | 'selected' }) => {
    setTwoWayMsg(null);
    updateTwoWay.mutate(patch, {
      onSuccess: () => setTwoWayMsg({ ok: true, text: t('Two-way sync preference saved.') }),
      onError: (err) =>
        setTwoWayMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Could not save.') }),
    });
  };

  const onTwoWayBookToggle = (bookId: number, enabled: boolean) => {
    setTwoWayBookMsg(null);
    setTwoWayBook.mutate({ book_id: bookId, enabled }, {
      onError: (err) =>
        setTwoWayBookMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Could not save.') }),
    });
  };

  const chooseLibraryMode = (mode: 'monolibrary' | 'personal_library') => {
    if (mode === account.library_mode || updateLibraryMode.isPending) return;
    const confirmText = mode === 'monolibrary'
      ? t('Show the global library again? Your selection is kept exactly as you left it — switch back any time and it is still there. At its next update, your e-reader syncs the global library.')
      : account.my_library_seeded
        ? t('Switch back to My Library? Your library goes back to the books you had chosen — nothing was lost while you saw everything. At its next update, your e-reader returns to your selection.')
        : t('Start My Library? It begins as everything you can see now, so nothing changes until you remove books yourself. Your e-reader keeps the same books at its next update.');
    if (!window.confirm(confirmText)) return;
    setLibraryModeError('');
    updateLibraryMode.mutate(mode, {
      onSuccess: () => announce(t(mode === 'monolibrary'
        ? 'You now see the global library.' : 'Your library now shows your selection.')),
      onError: (err) => setLibraryModeError(err instanceof ApiError ? err.message : t('Could not save.')),
    });
  };

  return (
    <main className={styles.container}>
      <h1 className={styles.title}>{t('Account')}</h1>

      <section className={styles.card} aria-labelledby="account-ereaders-title">
        <h2 id="account-ereaders-title" className={styles.cardTitle}><Smartphone size={16} aria-hidden="true" focusable={false} /> {t('E-readers')}</h2>
        {devices.data?.devices.length ? (
          <ul className={styles.deviceSummary}>
            {devices.data.devices.map((device) => <li key={device.public_id}>{device.label} · {t('{n} highlights and notes', { n: device.annotation_count })}</li>)}
          </ul>
        ) : <p className={styles.muted}>{devices.isError ? t('Could not load e-readers.') : t('No e-readers yet.')}</p>}
        <div className={styles.deviceLinks}>
          <Link href="/account/devices" className={styles.manageDevices}>{t('Manage e-readers')}</Link>
          <Link href="/account/devices#kobo-pairing" className={styles.manageDevices}>
            {t('Pair a Kobo or KOReader')}
          </Link>
        </div>
      </section>

      <section className={styles.card} aria-labelledby="library-contents-title">
        <fieldset className={styles.scopeGroup}>
          <legend id="library-contents-title" className={styles.cardTitle}>{t('Library contents')}</legend>
          {account.can_switch_library_mode ? (
            <>
              <label className={styles.scopeOption}>
                <input type="radio" name="library-mode" value="monolibrary"
                  checked={account.library_mode === 'monolibrary'} disabled={updateLibraryMode.isPending}
                  onChange={() => chooseLibraryMode('monolibrary')} />
                <span className={styles.scopeText}><strong>{t('The global library')}</strong>
                  <small>{t('Everything on the server, including every new book added to it.')}</small></span>
              </label>
              <label className={styles.scopeOption}>
                <input type="radio" name="library-mode" value="personal_library"
                  checked={account.library_mode === 'personal_library'} disabled={updateLibraryMode.isPending}
                  onChange={() => chooseLibraryMode('personal_library')} />
                <span className={styles.scopeText}><strong>{t('My Library')}</strong>
                  <small>{t('Only the books you choose. Add them from the global library; remove them any time.')}</small></span>
              </label>
            </>
          ) : <p className={styles.muted}>{t('Your library contents are managed by an administrator.')}</p>}
        </fieldset>
        <span className={libraryModeError ? styles.msgErr : undefined} role="alert">{libraryModeError}</span>
      </section>

      {/* Kobo two-way annotation sync — Stage 0 (BETA). Both server gates stay
          off; this card reads/writes the user's preference and shows observed
          per-book state. It never starts a sync. */}
      {twoWay.data && (
        <section className={styles.card} aria-labelledby="kobo-two-way-title">
          <h2 id="kobo-two-way-title" className={styles.cardTitle}>
            <PenLine size={16} aria-hidden="true" focusable={false} /> {t('Kobo two-way annotation sync')}
            <span className={styles.betaPill}>{t('Beta')}</span>
          </h2>

          {!twoWay.data.kobo_available ? (
            <p className={styles.hint}>
              {t('Set up Kobo sync first — then this preference becomes available.')}
            </p>
          ) : (
            <>
              <label className={styles.toggle}>
                <input
                  type="checkbox"
                  checked={twoWay.data.enabled}
                  disabled={updateTwoWay.isPending}
                  onChange={(e) => onTwoWaySave({ enabled: e.target.checked })}
                />
                {t('Opt in to Kobo two-way annotation sync')}
              </label>
              <p className={styles.hint}>
                {t('Off by default. While in beta, opting in only records your preference and recovery evidence — it does not change what your Kobo receives yet.')}
              </p>
              {!twoWay.data.instance_enabled && (
                <p className={styles.hint}>
                  {t('The server-wide option is also off, so nothing can sync yet. An administrator can enable it in the classic server settings.')}
                </p>
              )}
              {twoWay.data.emergency_disabled && (
                <p className={styles.hint}>
                  {t('This feature is currently disabled at server level.')}
                </p>
              )}

              <fieldset className={styles.scopeGroup}>
                <legend className={styles.label}>{t('Which books sync?')}</legend>
                {/* The two options must be distinguishable by SYMBOL, not by
                    wording alone: multi-check = every book, single check =
                    picked books. */}
                <label className={styles.scopeOption}>
                  <input
                    type="radio"
                    name="kobo-two-way-scope"
                    checked={twoWay.data.scope === 'all'}
                    disabled={updateTwoWay.isPending}
                    onChange={() => onTwoWaySave({ scope: 'all' })}
                  />
                  <CheckCheck size={18} aria-hidden="true" focusable={false} className={styles.scopeIcon} />
                  <span className={styles.scopeText}>
                    <strong>{t('All books')}</strong>
                    <small>{t('Every book syncs as it becomes ready. You can exclude individual books below.')}</small>
                  </span>
                </label>
                <label className={styles.scopeOption}>
                  <input
                    type="radio"
                    name="kobo-two-way-scope"
                    checked={twoWay.data.scope === 'selected'}
                    disabled={updateTwoWay.isPending}
                    onChange={() => onTwoWaySave({ scope: 'selected' })}
                  />
                  <Check size={18} aria-hidden="true" focusable={false} className={styles.scopeIcon} />
                  <span className={styles.scopeText}>
                    <strong>{t('Selected books')}</strong>
                    <small>{t('Only the books you pick below sync. Switching here starts from none picked — you choose each book yourself.')}</small>
                  </span>
                </label>
              </fieldset>
              <span
                className={twoWayMsg ? (twoWayMsg.ok ? styles.msgOk : styles.msgErr) : undefined}
                role="status"
              >
                {twoWayMsg?.text}
              </span>

              <p className={styles.hint}>
                {t('Opting in does not sync a book straight away. Each book is set up and checked first — its real state is shown here.')}
              </p>
              {twoWay.data.books.length > 0 ? (
                <ul className={styles.twoWayBooks} role="list">
                  {twoWay.data.books.map((b) => (
                    <li key={b.book_id} className={styles.twoWayBook}>
                      <div className={styles.twoWayBookMain}>
                        <Link href={`/book/${b.book_id}/annotations`} className={styles.twoWayBookTitle}>
                          {b.title ?? t('Book {id}', { id: b.book_id })}
                        </Link>
                        <span className={STATE_TONE_CLASS[authorityTone(b)]}>
                          {authorityLabel(t, b, twoWay.data.scope)}
                        </span>
                        {opaqueLabel(t, b) && (
                          <span className={styles.stateWarn}>{opaqueLabel(t, b)}</span>
                        )}
                        {b.authority_status === 'quarantined' && b.quarantine_reason && (
                          <span className={styles.twoWayReason}>{b.quarantine_reason}</span>
                        )}
                      </div>
                      {b.can_toggle && (
                        <label className={styles.toggle}>
                          <input
                            type="checkbox"
                            checked={b.enabled}
                            disabled={setTwoWayBook.isPending}
                            onChange={(e) => onTwoWayBookToggle(b.book_id, e.target.checked)}
                          />
                          {twoWay.data.scope === 'selected' ? t('Picked') : t('Included')}
                        </label>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className={styles.muted}>{t('No books are ready yet — they appear here as they become ready.')}</p>
              )}
              <span
                className={twoWayBookMsg ? (twoWayBookMsg.ok ? styles.msgOk : styles.msgErr) : undefined}
                role="status"
              >
                {twoWayBookMsg?.text}
              </span>
            </>
          )}
        </section>
      )}

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

        {account.mail_body_text !== null && (
          <div className={styles.field}>
            <label className={styles.label} htmlFor="acc-mail-body">{t('Email Message Body')}</label>
            <textarea
              id="acc-mail-body"
              className={styles.input}
              rows={4}
              maxLength={1000}
              value={mailBody}
              onChange={(e) => setMailBody(e.target.value)}
              placeholder={t('This Email has been sent via Calibre-Web NextGen.')}
            />
          </div>
        )}
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

        <div className={styles.field}>
          <label className={styles.label} htmlFor="acc-theme">{t('Theme')}</label>
          <select id="acc-theme" className={styles.input}
            disabled={updateProfile.isPending}
            value={theme} onChange={(e) => onThemeChange(e.target.value)}>
            {THEMES.map((o) => <option key={o.slug} value={o.slug}>{t(o.label)}</option>)}
          </select>
          {selectedTheme?.hint && (
            <p className={styles.hint}>{t(selectedTheme.hint)}</p>
          )}
          <span
            id="acc-theme-msg"
            className={themeMsg ? (themeMsg.ok ? styles.msgOk : styles.msgErr) : undefined}
            role="status"
          >
            {themeMsg?.text}
          </span>
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
                {/* Same class as #1496: destructive, irreversible, and unguarded.
                    Revoking cuts off whatever device still holds the password, the
                    value can never be shown again, and these render as a list of
                    identical trash buttons — so a misclick lands on the wrong row
                    and the only recovery is generating a new one and reconfiguring
                    the device. */}
                <button type="button" className={styles.revokeBtn}
                  disabled={revokeAppPw.isPending}
                  onClick={() => {
                    if (revokeAppPw.isPending) return;
                    if (!window.confirm(
                      t('Revoke the app password "{label}"? Any device still using it loses access immediately, and the password cannot be recovered.', { label: ap.label })
                    )) return;
                    revokeAppPw.mutate(ap.id);
                  }}
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
