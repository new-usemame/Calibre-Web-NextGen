import { useState } from 'react';
import { Shield, Trash2, Mail, UserPlus, ExternalLink, Settings, Database, Server, Clock, FileText, Sliders, BarChart3, Files, Lock, RefreshCw } from 'lucide-react';
import { useEffect } from 'react';
import {
  useAdminUsers, useUpdateAdminUser, useDeleteAdminUser, useCreateAdminUser, useMe,
  useAdminConfig, useUpdateAdminConfig, useMailConfig, useUpdateMailConfig,
  useSecurityConfig, useUpdateSecurityConfig,
} from '../lib/queries';
import type { SecurityConfig, SecurityUpdate } from '../lib/queries';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import type { AdminUser } from '../lib/api';
import { ApiError, resourceUrl } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './Admin.module.css';

// Remaining legacy server-configuration pages — these are the deep, rarely-touched
// infrastructure surfaces (DB path, ingest/convert internals, scheduled tasks)
// that are not part of the day-to-day user/auth flows. Login/authentication
// security (LDAP/OAuth/SSL/reverse-proxy) and SMTP are rebuilt natively below.
const SERVER_SETTINGS: { href: string; label: string; icon: typeof Settings }[] = [
  { href: '/admin/view', label: 'Full user table & restrictions', icon: Shield },
  { href: '/admin/config', label: 'Basic configuration', icon: Settings },
  { href: '/admin/viewconfig', label: 'UI / display configuration', icon: Sliders },
  { href: '/admin/dbconfig', label: 'Database & library path', icon: Database },
  { href: '/admin/scheduledtasks', label: 'Scheduled tasks', icon: Clock },
  { href: '/cwa-settings', label: 'CWA settings (ingest/convert)', icon: Server },
  { href: '/cwa-stats-show', label: 'Statistics dashboard', icon: BarChart3 },
  { href: '/admin/logfile', label: 'Logs', icon: FileText },
  { href: '/duplicates', label: 'Duplicate books', icon: Files },
];

// Default-role checkboxes auto-granted to new OAuth users. Keys MUST match
// _OAUTH_DEFAULT_ROLE_BITS in cps/api/admin_security.py.
const OAUTH_DEFAULT_ROLE_FIELDS: { key: string; label: string }[] = [
  { key: 'download', label: 'Download' },
  { key: 'viewer', label: 'View' },
  { key: 'upload', label: 'Upload' },
  { key: 'edit', label: 'Edit metadata' },
  { key: 'delete', label: 'Delete books' },
  { key: 'passwd', label: 'Change password' },
  { key: 'edit_shelf', label: 'Edit public shelves' },
];

// Order + labels for the role toggles shown per user.
const ROLE_FIELDS: { key: string; label: string }[] = [
  { key: 'admin', label: 'Admin' },
  { key: 'upload', label: 'Upload' },
  { key: 'edit', label: 'Edit metadata' },
  { key: 'download', label: 'Download' },
  { key: 'delete_books', label: 'Delete books' },
  { key: 'edit_shelfs', label: 'Edit public shelves' },
  { key: 'passwd', label: 'Change password' },
  { key: 'viewer', label: 'Viewer' },
];

export function Admin() {
  const t = useT();
  const { data, isLoading, error } = useAdminUsers();
  const updateUser = useUpdateAdminUser();
  const deleteUser = useDeleteAdminUser();
  const createUser = useCreateAdminUser();
  const me = useMe().data;
  const [banner, setBanner] = useState<{ ok: boolean; text: string } | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({ name: '', password: '', email: '', upload: false });

  if (isLoading) return <SpinnerCentered size={40} />;
  if (error || !data) {
    return (
      <main className={styles.container}>
        <EmptyState message={error instanceof Error ? error.message : 'Could not load users.'} />
      </main>
    );
  }

  const toggleRole = (user: AdminUser, key: string, value: boolean) => {
    setBanner(null);
    updateUser.mutate(
      { id: user.id, roles: { [key]: value } },
      {
        onError: (err) =>
          setBanner({ ok: false, text: err instanceof ApiError ? err.message : 'Update failed.' }),
      },
    );
  };

  const onCreate = (e: React.FormEvent) => {
    e.preventDefault();
    setBanner(null);
    createUser.mutate(
      {
        name: form.name.trim(),
        password: form.password,
        email: form.email.trim() || undefined,
        roles: { download: true, viewer: true, upload: form.upload },
      },
      {
        onSuccess: (u) => {
          setBanner({ ok: true, text: `Created ${u.name}.` });
          setForm({ name: '', password: '', email: '', upload: false });
          setShowNew(false);
        },
        onError: (err) =>
          setBanner({ ok: false, text: err instanceof ApiError ? err.message : 'Create failed.' }),
      },
    );
  };

  const onDelete = (user: AdminUser) => {
    if (!window.confirm(`Delete user "${user.name}"? Their shelves and reading data are removed too.`)) return;
    setBanner(null);
    deleteUser.mutate(user.id, {
      onSuccess: () => setBanner({ ok: true, text: `Deleted ${user.name}.` }),
      onError: (err) =>
        setBanner({ ok: false, text: err instanceof ApiError ? err.message : 'Delete failed.' }),
    });
  };

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <Shield size={22} className={styles.headerIcon} />
        <h1 className={styles.title}>{t('User administration')}</h1>
        <span className={styles.count}>{data.items.length}</span>
        <button
          type="button"
          className={styles.addBtn}
          onClick={() => { setShowNew((v) => !v); setBanner(null); }}
        >
          <UserPlus size={16} /> {t('New user')}
        </button>
      </div>

      <p className={banner ? (banner.ok ? styles.msgOk : styles.msgErr) : undefined} role="status">{banner?.text}</p>

      {showNew && (
        <form className={styles.newForm} onSubmit={onCreate}>
          <div className={styles.newRow}>
            <label className={styles.field}>
              <span>{t('Username')}</span>
              <input
                value={form.name} required autoFocus
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
            <label className={styles.field}>
              <span>{t('Password')}</span>
              <input
                type="password" value={form.password} required
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            </label>
            <label className={styles.field}>
              <span>{t('Email (optional)')}</span>
              <input
                type="email" value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </label>
          </div>
          <div className={styles.newActions}>
            <label className={styles.roleToggle}>
              <input
                type="checkbox" checked={form.upload}
                onChange={(e) => setForm({ ...form, upload: e.target.checked })}
              />
              {t('Can upload books')}
            </label>
            <button type="submit" className={styles.submitBtn} disabled={createUser.isPending}>
              {createUser.isPending ? t('Creating…') : t('Create user')}
            </button>
          </div>
        </form>
      )}

      <div className={styles.users}>
        {data.items.map((user) => {
          const isSelf = me?.id === user.id;
          return (
            <section key={user.id} className={styles.card}>
              <div className={styles.cardHead}>
                <div>
                  <p className={styles.name}>
                    {user.name}
                    {isSelf && <span className={styles.youBadge}>{t('you')}</span>}
                  </p>
                  {user.email && (
                    <p className={styles.email}><Mail size={12} /> {user.email}</p>
                  )}
                </div>
                {!isSelf && !user.is_guest && (
                  <button className={styles.deleteBtn} onClick={() => onDelete(user)}
                    disabled={deleteUser.isPending} aria-label={`Delete ${user.name}`}>
                    <Trash2 size={15} />
                  </button>
                )}
              </div>

              <div className={styles.roles}>
                {ROLE_FIELDS.map(({ key, label }) => (
                  <label key={key} className={styles.roleToggle}>
                    <input
                      type="checkbox"
                      checked={!!user.roles[key]}
                      disabled={updateUser.isPending}
                      onChange={(e) => toggleRole(user, key, e.target.checked)}
                    />
                    {t(label)}
                  </label>
                ))}
              </div>
            </section>
          );
        })}
      </div>

      <AdminConfigForm />
      <MailConfigForm />
      <SecurityConfigForm />

      <div className={styles.settingsHead}>
        <Settings size={18} className={styles.headerIcon} />
        <h2 className={styles.settingsTitle}>{t('More server configuration')}</h2>
      </div>
      <p className={styles.settingsHint}>
        {t('These open the full configuration pages. Changes there apply to the whole server.')}
      </p>
      <div className={styles.settingsGrid}>
        {SERVER_SETTINGS.map(({ href, label, icon: Icon }) => (
          <a
            key={href}
            href={resourceUrl(href)}
            target="_blank"
            rel="noopener noreferrer"
            className={styles.settingsCard}
          >
            <Icon size={18} className={styles.settingsIcon} aria-hidden="true" />
            <span className={styles.settingsLabel}>{t(label)}</span>
            <ExternalLink size={13} className={styles.settingsExt} aria-hidden="true" />
          </a>
        ))}
      </div>
    </main>
  );
}

/** Native UI-configuration form (books/page, default language/locale, theme,
 *  random count, title, announcement). The deep security config (LDAP/OAuth/
 *  SMTP/SSL) stays on the legacy pages linked below. */
function AdminConfigForm() {
  const t = useT();
  const { data: cfg } = useAdminConfig();
  const update = useUpdateAdminConfig();
  const [form, setForm] = useState<Record<string, string | number>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!cfg) return;
    setForm({
      config_calibre_web_title: cfg.config_calibre_web_title,
      config_books_per_page: cfg.config_books_per_page,
      config_random_books: cfg.config_random_books,
      config_authors_max: cfg.config_authors_max,
      config_theme: cfg.config_theme,
      config_default_language: cfg.config_default_language,
      config_default_locale: cfg.config_default_locale,
      config_server_announcement: cfg.config_server_announcement,
    });
  }, [cfg]);

  if (!cfg) return null;
  const set = (k: string, v: string | number) => setForm((f) => ({ ...f, [k]: v }));

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    update.mutate(form, {
      onSuccess: () => setMsg({ ok: true, text: t('Settings saved.') }),
      onError: (err) => setMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Could not save.') }),
    });
  };

  return (
    <form className={styles.newForm} onSubmit={onSubmit}>
      <div className={styles.settingsHead} style={{ marginTop: 0 }}>
        <Settings size={18} className={styles.headerIcon} />
        <h2 className={styles.settingsTitle}>{t('Library settings')}</h2>
      </div>
      <div className={styles.newRow}>
        <label className={styles.field}>
          <span>{t('Site title')}</span>
          <input value={String(form.config_calibre_web_title ?? '')}
            onChange={(e) => set('config_calibre_web_title', e.target.value)} />
        </label>
        <label className={styles.field}>
          <span>{t('Books per page')}</span>
          <input type="number" min={1} value={String(form.config_books_per_page ?? '')}
            onChange={(e) => set('config_books_per_page', e.target.value)} />
        </label>
        <label className={styles.field}>
          <span>{t('Random books shown')}</span>
          <input type="number" min={0} value={String(form.config_random_books ?? '')}
            onChange={(e) => set('config_random_books', e.target.value)} />
        </label>
        <label className={styles.field}>
          <span>{t('Max authors shown')}</span>
          <input type="number" min={0} value={String(form.config_authors_max ?? '')}
            onChange={(e) => set('config_authors_max', e.target.value)} />
        </label>
      </div>
      <div className={styles.newRow}>
        <label className={styles.field}>
          <span>{t('Theme')}</span>
          <select value={String(form.config_theme ?? 1)} onChange={(e) => set('config_theme', e.target.value)}>
            <option value="0">{t('Light')}</option>
            <option value="1">{t('Dark')}</option>
          </select>
        </label>
        <label className={styles.field}>
          <span>{t('Default interface language')}</span>
          <select value={String(form.config_default_locale ?? 'en')}
            onChange={(e) => set('config_default_locale', e.target.value)}>
            {cfg.locales.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </label>
        <label className={styles.field}>
          <span>{t('Default book language')}</span>
          <select value={String(form.config_default_language ?? 'all')}
            onChange={(e) => set('config_default_language', e.target.value)}>
            {cfg.languages.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </label>
      </div>
      <label className={styles.field}>
        <span>{t('Server announcement (shown to all users)')}</span>
        <input value={String(form.config_server_announcement ?? '')}
          onChange={(e) => set('config_server_announcement', e.target.value)} />
      </label>
      <div className={styles.newActions}>
        <button type="submit" className={styles.submitBtn} disabled={update.isPending}>
          {update.isPending ? t('Saving…') : t('Save settings')}
        </button>
        <span className={msg ? (msg.ok ? styles.msgOk : styles.msgErr) : undefined} role="status">{msg?.text}</span>
      </div>
    </form>
  );
}

/** Native SMTP / email server settings. Password is write-only: blank = keep
 *  the existing one. (Security-review gated before merge — writes a secret.) */
function MailConfigForm() {
  const t = useT();
  const { data: cfg } = useMailConfig();
  const update = useUpdateMailConfig();
  const [form, setForm] = useState<Record<string, string | number>>({});
  const [pw, setPw] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!cfg) return;
    setForm({
      mail_server: cfg.mail_server, mail_port: cfg.mail_port, mail_use_ssl: cfg.mail_use_ssl,
      mail_login: cfg.mail_login, mail_from: cfg.mail_from, mail_size_mb: cfg.mail_size_mb,
    });
  }, [cfg]);

  if (!cfg) return null;
  const set = (k: string, v: string | number) => setForm((f) => ({ ...f, [k]: v }));

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    update.mutate({ ...form, ...(pw ? { mail_password: pw } : {}) }, {
      onSuccess: () => { setMsg({ ok: true, text: t('Email settings saved.') }); setPw(''); },
      onError: (err: unknown) => setMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Could not save.') }),
    });
  };

  return (
    <form className={styles.newForm} onSubmit={onSubmit}>
      <div className={styles.settingsHead} style={{ marginTop: 0 }}>
        <Mail size={18} className={styles.headerIcon} />
        <h2 className={styles.settingsTitle}>{t('Email (SMTP) server')}</h2>
      </div>
      <div className={styles.newRow}>
        <label className={styles.field}>
          <span>{t('SMTP server')}</span>
          <input value={String(form.mail_server ?? '')} onChange={(e) => set('mail_server', e.target.value)} />
        </label>
        <label className={styles.field}>
          <span>{t('Port')}</span>
          <input type="number" value={String(form.mail_port ?? '')} onChange={(e) => set('mail_port', e.target.value)} />
        </label>
        <label className={styles.field}>
          <span>{t('Encryption')}</span>
          <select value={String(form.mail_use_ssl ?? 0)} onChange={(e) => set('mail_use_ssl', e.target.value)}>
            <option value="0">{t('None')}</option>
            <option value="1">{t('STARTTLS')}</option>
            <option value="2">{t('SSL/TLS')}</option>
          </select>
        </label>
      </div>
      <div className={styles.newRow}>
        <label className={styles.field}>
          <span>{t('Login')}</span>
          <input value={String(form.mail_login ?? '')} onChange={(e) => set('mail_login', e.target.value)} />
        </label>
        <label className={styles.field}>
          <span>{cfg.has_password ? t('Password (leave blank to keep)') : t('Password')}</span>
          <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" />
        </label>
        <label className={styles.field}>
          <span>{t('From address')}</span>
          <input value={String(form.mail_from ?? '')} onChange={(e) => set('mail_from', e.target.value)} />
        </label>
      </div>
      <div className={styles.newActions}>
        <button type="submit" className={styles.submitBtn} disabled={update.isPending}>
          {update.isPending ? t('Saving…') : t('Save email settings')}
        </button>
        <span className={msg ? (msg.ok ? styles.msgOk : styles.msgErr) : undefined} role="status">{msg?.text}</span>
      </div>
    </form>
  );
}

/** Native deep authentication / security config: login type (standard / LDAP /
 *  OAuth), full LDAP + OAuth provider settings, server SSL, reverse-proxy header
 *  login, and remote (magic-link) login. Secrets are write-only — blank keeps the
 *  existing one. Validation is enforced server-side by the shared legacy helpers,
 *  so the error messages here match the legacy form exactly. Changing the login
 *  type / LDAP / OAuth needs a server restart, surfaced as a banner on save.
 *  (Security-review gated before merge — writes auth/secret config.) */
function SecurityConfigForm() {
  const t = useT();
  const { data: cfg } = useSecurityConfig();
  const update = useUpdateSecurityConfig();
  const [f, setF] = useState<SecurityConfig | null>(null);
  const [ldapPw, setLdapPw] = useState('');
  const [oauthSecret, setOauthSecret] = useState('');
  const [providerSecrets, setProviderSecrets] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [reboot, setReboot] = useState(false);

  useEffect(() => { if (cfg) setF(cfg); }, [cfg]);
  if (!f) return null;

  const setTop = <K extends keyof SecurityConfig>(k: K, v: SecurityConfig[K]) =>
    setF((s) => (s ? { ...s, [k]: v } : s));
  const setLdap = (k: keyof SecurityConfig['ldap'], v: string | number | boolean) =>
    setF((s) => (s ? { ...s, ldap: { ...s.ldap, [k]: v } } : s));
  const setOauth = (k: keyof SecurityConfig['oauth'], v: string | boolean) =>
    setF((s) => (s ? { ...s, oauth: { ...s.oauth, [k]: v } } : s));
  const setGen = (k: keyof SecurityConfig['oauth']['generic'], v: string | boolean) =>
    setF((s) => (s ? { ...s, oauth: { ...s.oauth, generic: { ...s.oauth.generic, [k]: v } } } : s));
  const setGenRole = (roleKey: string, v: boolean) =>
    setF((s) => (s ? { ...s, oauth: { ...s.oauth, generic: { ...s.oauth.generic,
      default_roles: { ...s.oauth.generic.default_roles, [roleKey]: v } } } } : s));
  const setProviderClientId = (name: string, v: string) =>
    setF((s) => (s ? { ...s, oauth: { ...s.oauth,
      providers: s.oauth.providers.map((p) => p.name === name ? { ...p, client_id: v } : p) } } : s));
  const setSsl = (k: keyof SecurityConfig['ssl'], v: string | boolean) =>
    setF((s) => (s ? { ...s, ssl: { ...s.ssl, [k]: v } } : s));
  const setRp = (k: keyof SecurityConfig['reverse_proxy'], v: string | boolean) =>
    setF((s) => (s ? { ...s, reverse_proxy: { ...s.reverse_proxy, [k]: v } } : s));

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    const body: SecurityUpdate = {
      login_type: f.login_type,
      remote_login: f.remote_login,
      ssl: { ...f.ssl },
      reverse_proxy: { ...f.reverse_proxy },
      oauth: {
        redirect_host: f.oauth.redirect_host,
        disable_standard_login: f.oauth.disable_standard_login,
        enable_group_admin_management: f.oauth.enable_group_admin_management,
      },
    };
    if (f.login_type === 1) {
      const { ...ldap } = f.ldap;
      body.ldap = { ...ldap, ...(ldapPw ? { serv_password: ldapPw } : {}) };
    }
    if (f.login_type === 2) {
      body.oauth!.generic = { ...f.oauth.generic, ...(oauthSecret ? { client_secret: oauthSecret } : {}) };
      body.oauth!.providers = f.oauth.providers.map((p) => ({
        name: p.name, client_id: p.client_id,
        ...(providerSecrets[p.name] ? { client_secret: providerSecrets[p.name] } : {}),
      }));
    }
    update.mutate(body, {
      onSuccess: (data) => {
        setMsg({ ok: true, text: t('Security settings saved.') });
        setLdapPw(''); setOauthSecret(''); setProviderSecrets({});
        setReboot(Boolean(data.reboot_required));
      },
      onError: (err: unknown) => setMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Could not save.') }),
    });
  };

  return (
    <form className={styles.newForm} onSubmit={onSubmit}>
      <div className={styles.settingsHead} style={{ marginTop: 0 }}>
        <Lock size={18} className={styles.headerIcon} />
        <h2 className={styles.settingsTitle}>{t('Authentication & security')}</h2>
      </div>

      <div className={styles.newRow}>
        <label className={styles.field}>
          <span>{t('Login method')}</span>
          <select value={String(f.login_type)} onChange={(e) => setTop('login_type', Number(e.target.value))}>
            {f.login_types.map((o) => <option key={o.id} value={o.id}>{t(o.name)}</option>)}
          </select>
        </label>
        <label className={styles.checkField}>
          <input type="checkbox" checked={f.remote_login}
            onChange={(e) => setTop('remote_login', e.target.checked)} />
          <span>{t('Allow remote (magic-link) login')}</span>
        </label>
      </div>

      {f.login_type === 1 && (
        <fieldset className={styles.fieldset}>
          <legend>{t('LDAP')}</legend>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('Server host')}</span>
              <input value={f.ldap.provider_url} onChange={(e) => setLdap('provider_url', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Port')}</span>
              <input type="number" value={String(f.ldap.port)} onChange={(e) => setLdap('port', Number(e.target.value))} /></label>
            <label className={styles.field}><span>{t('Encryption')}</span>
              <select value={String(f.ldap.encryption)} onChange={(e) => setLdap('encryption', Number(e.target.value))}>
                {f.ldap_encryption_levels.map((o) => <option key={o.id} value={o.id}>{t(o.name)}</option>)}
              </select></label>
            <label className={styles.field}><span>{t('Authentication')}</span>
              <select value={String(f.ldap.authentication)} onChange={(e) => setLdap('authentication', Number(e.target.value))}>
                {f.ldap_auth_levels.map((o) => <option key={o.id} value={o.id}>{t(o.name)}</option>)}
              </select></label>
          </div>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('Service account (DN)')}</span>
              <input value={f.ldap.serv_username} onChange={(e) => setLdap('serv_username', e.target.value)} /></label>
            <label className={styles.field}>
              <span>{f.ldap.has_password ? t('Service password (leave blank to keep)') : t('Service password')}</span>
              <input type="password" value={ldapPw} autoComplete="new-password" onChange={(e) => setLdapPw(e.target.value)} /></label>
            <label className={styles.field}><span>{t('Base DN')}</span>
              <input value={f.ldap.dn} onChange={(e) => setLdap('dn', e.target.value)} /></label>
          </div>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('User object filter')}</span>
              <input value={f.ldap.user_object} onChange={(e) => setLdap('user_object', e.target.value)} placeholder="uid=%s" /></label>
            <label className={styles.field}><span>{t('Member user filter')}</span>
              <input value={f.ldap.member_user_object} onChange={(e) => setLdap('member_user_object', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Group name')}</span>
              <input value={f.ldap.group_name} onChange={(e) => setLdap('group_name', e.target.value)} /></label>
          </div>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('Group object filter')}</span>
              <input value={f.ldap.group_object_filter} onChange={(e) => setLdap('group_object_filter', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Group members field')}</span>
              <input value={f.ldap.group_members_field} onChange={(e) => setLdap('group_members_field', e.target.value)} /></label>
          </div>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('CA certificate path')}</span>
              <input value={f.ldap.cacert_path} onChange={(e) => setLdap('cacert_path', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Certificate path')}</span>
              <input value={f.ldap.cert_path} onChange={(e) => setLdap('cert_path', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Key path')}</span>
              <input value={f.ldap.key_path} onChange={(e) => setLdap('key_path', e.target.value)} /></label>
          </div>
          <div className={styles.newRow}>
            <label className={styles.checkField}><input type="checkbox" checked={f.ldap.openldap}
              onChange={(e) => setLdap('openldap', e.target.checked)} /><span>{t('OpenLDAP server')}</span></label>
            <label className={styles.checkField}><input type="checkbox" checked={f.ldap.auto_create_users}
              onChange={(e) => setLdap('auto_create_users', e.target.checked)} /><span>{t('Auto-create users on first login')}</span></label>
          </div>
        </fieldset>
      )}

      {f.login_type === 2 && (
        <fieldset className={styles.fieldset}>
          <legend>{t('OAuth / OpenID Connect')}</legend>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('Redirect host (full URL)')}</span>
              <input value={f.oauth.redirect_host} onChange={(e) => setOauth('redirect_host', e.target.value)} placeholder="https://books.example.com" /></label>
            <label className={styles.field}><span>{t('Login button text')}</span>
              <input value={f.oauth.generic.login_button} onChange={(e) => setGen('login_button', e.target.value)} /></label>
          </div>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('Client ID')}</span>
              <input value={f.oauth.generic.client_id} onChange={(e) => setGen('client_id', e.target.value)} /></label>
            <label className={styles.field}>
              <span>{f.oauth.generic.has_secret ? t('Client secret (leave blank to keep)') : t('Client secret')}</span>
              <input type="password" value={oauthSecret} autoComplete="new-password" onChange={(e) => setOauthSecret(e.target.value)} /></label>
          </div>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('Metadata URL (OIDC auto-discovery)')}</span>
              <input value={f.oauth.generic.metadata_url} onChange={(e) => setGen('metadata_url', e.target.value)}
                placeholder="https://idp/.well-known/openid-configuration" /></label>
            <label className={styles.field}><span>{t('Issuer / base URL')}</span>
              <input value={f.oauth.generic.base_url} onChange={(e) => setGen('base_url', e.target.value)} /></label>
          </div>
          <p className={styles.settingsHint} style={{ margin: '0 0 8px' }}>
            {t('Set a metadata URL to auto-fill the endpoints below, or enter them manually.')}
          </p>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('Authorize URL')}</span>
              <input value={f.oauth.generic.authorize_url} onChange={(e) => setGen('authorize_url', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Token URL')}</span>
              <input value={f.oauth.generic.token_url} onChange={(e) => setGen('token_url', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Userinfo URL')}</span>
              <input value={f.oauth.generic.userinfo_url} onChange={(e) => setGen('userinfo_url', e.target.value)} /></label>
          </div>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('Scopes')}</span>
              <input value={f.oauth.generic.scope} onChange={(e) => setGen('scope', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Username claim')}</span>
              <input value={f.oauth.generic.username_mapper} onChange={(e) => setGen('username_mapper', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Email claim')}</span>
              <input value={f.oauth.generic.email_mapper} onChange={(e) => setGen('email_mapper', e.target.value)} /></label>
            <label className={styles.field}><span>{t('Admin group')}</span>
              <input value={f.oauth.generic.admin_group} onChange={(e) => setGen('admin_group', e.target.value)} /></label>
          </div>
          <div className={styles.newRow}>
            <label className={styles.field}><span>{t('Group claim')}</span>
              <input value={f.oauth.generic.group_claim} onChange={(e) => setGen('group_claim', e.target.value)} placeholder="groups" /></label>
            <label className={styles.field}><span>{t('Allowed groups (comma-separated)')}</span>
              <input value={f.oauth.generic.allowed_groups} onChange={(e) => setGen('allowed_groups', e.target.value)}
                placeholder="calibre-user, calibre-admin" /></label>
            <label className={styles.checkField}><input type="checkbox" checked={f.oauth.generic.require_group}
              onChange={(e) => setGen('require_group', e.target.checked)} /><span>{t('Require group membership')}</span></label>
          </div>
          <div className={styles.newRow}>
            <span className={styles.settingsHint} style={{ margin: 0, alignSelf: 'center' }}>{t('Default roles for new OAuth users:')}</span>
            {OAUTH_DEFAULT_ROLE_FIELDS.map(({ key, label }) => (
              <label key={key} className={styles.checkField}>
                <input type="checkbox" checked={Boolean(f.oauth.generic.default_roles?.[key])}
                  onChange={(e) => setGenRole(key, e.target.checked)} /><span>{t(label)}</span>
              </label>
            ))}
          </div>
          <div className={styles.newRow}>
            <label className={styles.checkField}><input type="checkbox" checked={f.oauth.disable_standard_login}
              onChange={(e) => setOauth('disable_standard_login', e.target.checked)} /><span>{t('Disable standard password login')}</span></label>
            <label className={styles.checkField}><input type="checkbox" checked={f.oauth.enable_group_admin_management}
              onChange={(e) => setOauth('enable_group_admin_management', e.target.checked)} /><span>{t('Manage admin role from OAuth group')}</span></label>
          </div>
          {f.oauth.providers.length > 0 && (
            <>
              <p className={styles.settingsHint} style={{ margin: '4px 0 0' }}>
                {t('Built-in providers (leave blank to disable):')}
              </p>
              {f.oauth.providers.map((p) => (
                <div className={styles.newRow} key={p.name}>
                  <label className={styles.field}><span>{p.name[0].toUpperCase() + p.name.slice(1)} {t('Client ID')}</span>
                    <input value={p.client_id} onChange={(e) => setProviderClientId(p.name, e.target.value)} /></label>
                  <label className={styles.field}>
                    <span>{p.has_secret ? t('Client secret (leave blank to keep)') : t('Client secret')}</span>
                    <input type="password" autoComplete="new-password" value={providerSecrets[p.name] || ''}
                      onChange={(e) => setProviderSecrets((s) => ({ ...s, [p.name]: e.target.value }))} /></label>
                </div>
              ))}
            </>
          )}
        </fieldset>
      )}

      <fieldset className={styles.fieldset}>
        <legend>{t('Server SSL / HTTPS')}</legend>
        <div className={styles.newRow}>
          <label className={styles.checkField}><input type="checkbox" checked={f.ssl.use_https}
            onChange={(e) => setSsl('use_https', e.target.checked)} /><span>{t('Serve over HTTPS')}</span></label>
          <label className={styles.field}><span>{t('Certificate file path')}</span>
            <input value={f.ssl.certfile} onChange={(e) => setSsl('certfile', e.target.value)} /></label>
          <label className={styles.field}><span>{t('Key file path')}</span>
            <input value={f.ssl.keyfile} onChange={(e) => setSsl('keyfile', e.target.value)} /></label>
        </div>
      </fieldset>

      <fieldset className={styles.fieldset}>
        <legend>{t('Reverse-proxy header login')}</legend>
        <div className={styles.newRow}>
          <label className={styles.checkField}><input type="checkbox" checked={f.reverse_proxy.enabled}
            onChange={(e) => setRp('enabled', e.target.checked)} /><span>{t('Trust authentication header')}</span></label>
          <label className={styles.field}><span>{t('Header name')}</span>
            <input value={f.reverse_proxy.header_name} onChange={(e) => setRp('header_name', e.target.value)} placeholder="Remote-User" /></label>
          <label className={styles.checkField}><input type="checkbox" checked={f.reverse_proxy.auto_create_users}
            onChange={(e) => setRp('auto_create_users', e.target.checked)} /><span>{t('Auto-create users')}</span></label>
        </div>
      </fieldset>

      {reboot && (
        <div className={styles.rebootBanner}>
          <RefreshCw size={15} />
          <span>{t('Saved. Restart the server for the login changes to take effect.')}</span>
        </div>
      )}
      <div className={styles.newActions}>
        <button type="submit" className={styles.submitBtn} disabled={update.isPending}>
          {update.isPending ? t('Saving…') : t('Save security settings')}
        </button>
        <span className={msg ? (msg.ok ? styles.msgOk : styles.msgErr) : undefined} role="status">{msg?.text}</span>
      </div>
    </form>
  );
}
