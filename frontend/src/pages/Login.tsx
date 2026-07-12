import React, { useState, useEffect } from 'react';
import { Link } from 'wouter';
import { BookMarked, KeyRound, Eye, EyeOff } from 'lucide-react';
import { Button } from '../components/Button';
import { BrandName } from '../components/BrandName';
import { Spinner } from '../components/Spinner';
import { VisuallyHidden } from '../components/VisuallyHidden';
import { useLogin, useAuthConfig, useRegister, useForgotPassword } from '../lib/queries';
import { ApiError } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './Login.module.css';

type Mode = 'login' | 'register' | 'forgot';

export function Login() {
  const t = useT();
  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);
  const [email, setEmail] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  const login = useLogin();
  const register = useRegister();
  const forgot = useForgotPassword();
  const { data: cfg } = useAuthConfig();

  // #609: the classic login page titles itself with the configured instance name.
  useEffect(() => {
    if (cfg?.instance_name) document.title = cfg.instance_name;
  }, [cfg?.instance_name]);

  const reset = () => { setErrorMsg(null); setOkMsg(null); };

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    reset();
    login.mutate({ username, password, remember }, {
      onError: (err) =>
        setErrorMsg(err instanceof ApiError && err.status === 401
          ? t('Invalid username or password.') : t('Sign in failed. Please try again.')),
    });
  };

  const handleRegister = (e: React.FormEvent) => {
    e.preventDefault();
    reset();
    register.mutate({ name: username, email }, {
      onSuccess: (r) => { setOkMsg(r.message); setMode('login'); },
      onError: (err) => setErrorMsg(err instanceof ApiError ? err.message : t('Registration failed.')),
    });
  };

  const handleForgot = (e: React.FormEvent) => {
    e.preventDefault();
    reset();
    forgot.mutate(username, {
      onSuccess: (r) => { setOkMsg(r.message); setMode('login'); },
      onError: (err) => setErrorMsg(err instanceof ApiError ? err.message : t('Request failed.')),
    });
  };

  const standardDisabled = !!cfg?.standard_login_disabled;
  const canRegister = !!cfg?.public_registration && !!cfg?.mail_configured;
  const canForgot = !!cfg?.mail_configured;
  const providers = cfg?.oauth_providers ?? [];

  const headingText = mode === 'register' ? t('Create your account')
    : mode === 'forgot' ? t('Reset your password')
      : t('Sign in');

  return (
    <main className={styles.page} id="main" tabIndex={-1}>
      <div className={styles.card}>
        {/* A real page heading for screen readers / document outline (SC 1.3.1). */}
        <VisuallyHidden as="h1">{headingText}</VisuallyHidden>
        <div className={styles.brandMark}>
          <BookMarked size={32} className={styles.brandIcon} aria-hidden="true" focusable={false} />
          <span className={styles.brandText}><BrandName name={cfg?.instance_name} accentClassName={styles.brandAccent} /></span>
        </div>
        <p className={styles.tagline}>
          {mode === 'register' ? t('Create your account')
            : mode === 'forgot' ? t('Reset your password')
              : t('Your personal digital library')}
        </p>

        {okMsg && <div className={styles.ok} role="status">{okMsg}</div>}

        {/* LOGIN */}
        {mode === 'login' && !standardDisabled && (
          <form className={styles.form} onSubmit={handleLogin} noValidate>
            <label className={styles.field}>
              <span className={styles.label}>{t('Username')}</span>
              <input type="text" className={styles.input} value={username}
                onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
            </label>
            <label className={styles.field}>
              <span className={styles.label}>{t('Password')}</span>
              <div className={styles.passwordWrap}>
                <input type={showPassword ? 'text' : 'password'} className={styles.input} value={password}
                  onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
                <button type="button" className={styles.revealBtn}
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? t('Hide password') : t('Show password')}
                  aria-pressed={showPassword}>
                  {showPassword
                    ? <EyeOff size={16} aria-hidden="true" focusable={false} />
                    : <Eye size={16} aria-hidden="true" focusable={false} />}
                </button>
              </div>
            </label>
            <label className={styles.checkboxRow}>
              <input type="checkbox" className={styles.checkbox} checked={remember}
                onChange={(e) => setRemember(e.target.checked)} />
              <span>{t('Remember me')}</span>
            </label>
            {errorMsg && <div className={styles.error} role="alert">{errorMsg}</div>}
            <Button type="submit" variant="primary" className={styles.submitBtn} disabled={login.isPending}>
              {login.isPending ? (<><Spinner size={16} /> {t('Signing in…')}</>) : t('Sign in')}
            </Button>
          </form>
        )}

        {/* REGISTER */}
        {mode === 'register' && (
          <form className={styles.form} onSubmit={handleRegister} noValidate>
            {!cfg?.register_email && (
              <label className={styles.field}>
                <span className={styles.label}>{t('Username')}</span>
                <input type="text" className={styles.input} value={username}
                  onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
              </label>
            )}
            <label className={styles.field}>
              <span className={styles.label}>{t('Email')}</span>
              <input type="email" className={styles.input} value={email}
                onChange={(e) => setEmail(e.target.value)} autoComplete="email"
                autoFocus={!!cfg?.register_email} required />
            </label>
            {errorMsg && <div className={styles.error} role="alert">{errorMsg}</div>}
            <Button type="submit" variant="primary" className={styles.submitBtn} disabled={register.isPending}>
              {register.isPending ? (<><Spinner size={16} /> {t('Registering…')}</>) : t('Create account')}
            </Button>
          </form>
        )}

        {/* FORGOT */}
        {mode === 'forgot' && (
          <form className={styles.form} onSubmit={handleForgot} noValidate>
            <label className={styles.field}>
              <span className={styles.label}>{t('Username')}</span>
              <input type="text" className={styles.input} value={username}
                onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
            </label>
            {errorMsg && <div className={styles.error} role="alert">{errorMsg}</div>}
            <Button type="submit" variant="primary" className={styles.submitBtn} disabled={forgot.isPending}>
              {forgot.isPending ? (<><Spinner size={16} /> {t('Sending…')}</>) : t('Email me a reset')}
            </Button>
          </form>
        )}

        {/* Every configured passwordless/federated method shares one compact,
            wrapping group. Provider labels come from the server's configured
            human-facing names, never internal ids such as "generic". */}
        {mode === 'login' && (cfg?.remote_login || providers.length > 0) && (
          <div className={styles.loginWith} role="group" aria-label={t('Login with')}>
            <span className={styles.loginWithLabel}>{t('Login with')}</span>
            <div className={styles.loginWithActions}>
              {cfg?.remote_login && (
                <Link href="/magic-link" className={styles.loginWithBtn}>
                  <KeyRound size={15} aria-hidden="true" focusable={false} />
                  {t('Magic link')}
                </Link>
              )}
              {providers.map((p) => (
                <a key={p.id} href={p.url} className={styles.loginWithBtn}>{p.name}</a>
              ))}
            </div>
          </div>
        )}

        {/* Mode switches */}
        <div className={styles.switches}>
          {mode !== 'login' && (
            <button type="button" className={styles.linkBtn} onClick={() => { setMode('login'); reset(); }}>
              {t('← Back to sign in')}
            </button>
          )}
          {mode === 'login' && canForgot && (
            <button type="button" className={styles.linkBtn} onClick={() => { setMode('forgot'); reset(); }}>
              {t('Forgot password?')}
            </button>
          )}
          {mode === 'login' && canRegister && (
            <button type="button" className={styles.linkBtn} onClick={() => { setMode('register'); reset(); }}>
              {t('Create an account')}
            </button>
          )}
        </div>
      </div>
    </main>
  );
}
