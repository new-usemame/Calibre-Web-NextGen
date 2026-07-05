import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'wouter';
import {
  BookMarked, KeyRound, Copy, Check, RefreshCw, ArrowLeft,
  ShieldCheck, Smartphone, Clock,
} from 'lucide-react';
import { Spinner } from '../components/Spinner';
import { Button } from '../components/Button';
import { BrandName } from '../components/BrandName';
import { useMagicLinkStart, useMagicLinkPoll, useAuthConfig } from '../lib/queries';
import { useT } from '../lib/i18n';
import styles from './MagicLink.module.css';

const POLL_MS = 3000;

type Phase = 'starting' | 'waiting' | 'success' | 'expired' | 'error';

function fmtCountdown(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, '0')}`;
}

export function MagicLink() {
  const t = useT();
  const [, navigate] = useLocation();
  const start = useMagicLinkStart();
  const poll = useMagicLinkPoll();
  const { data: cfg } = useAuthConfig();

  const [phase, setPhase] = useState<Phase>('starting');
  const [token, setToken] = useState<string | null>(null);
  const [verifyUrl, setVerifyUrl] = useState('');
  const [qrcode, setQrcode] = useState('');
  const [expiresAt, setExpiresAt] = useState(0);
  const [remaining, setRemaining] = useState(0);
  const [copied, setCopied] = useState(false);

  // Ref so the countdown reads the live expiry without re-subscribing.
  const expiresRef = useRef(expiresAt);
  expiresRef.current = expiresAt;

  const begin = useCallback(() => {
    setPhase('starting');
    setCopied(false);
    start.mutate(undefined, {
      onSuccess: (s) => {
        setToken(s.token);
        setVerifyUrl(s.verify_url);
        setQrcode(s.qrcode);
        const at = Date.now() + s.expires_in_minutes * 60 * 1000;
        setExpiresAt(at);
        setRemaining(at - Date.now());
        setPhase('waiting');
      },
      onError: () => setPhase('error'),
    });
  }, [start]);

  // Start a session on mount (once).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { begin(); }, []);

  // Countdown tick.
  useEffect(() => {
    if (phase !== 'waiting') return;
    const id = setInterval(() => {
      const left = expiresRef.current - Date.now();
      setRemaining(left);
      if (left <= 0) setPhase('expired');
    }, 1000);
    return () => clearInterval(id);
  }, [phase]);

  // Poll loop while waiting. Deps are intentionally only [phase, token]: the
  // mutation object's identity changes on every render (isPending toggling),
  // and including it would tear down + recreate this effect mid-request, whose
  // stale `cancelled` closure would then swallow the success. We capture
  // mutateAsync/navigate from the phase-entry render (both stable enough) and
  // await each poll instead of relying on per-call onSuccess for control flow.
  const pollAsync = poll.mutateAsync;
  useEffect(() => {
    if (phase !== 'waiting' || !token) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (cancelled) return;
      try {
        const r = await pollAsync(token);
        if (cancelled) return;
        if (r.status === 'success') {
          // App's me-cache is seeded by the hook; nudge to the library so the
          // authenticated tree mounts on "/".
          setPhase('success');
          setTimeout(() => navigate('/'), 400);
          return;
        }
        if (r.status === 'expired' || r.status === 'not_found') {
          setPhase('expired');
          return;
        }
      } catch {
        /* transient — fall through to reschedule */
      }
      if (!cancelled) timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
    return () => { cancelled = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, token]);

  const onCopy = useCallback(() => {
    if (!verifyUrl) return;
    const done = () => { setCopied(true); setTimeout(() => setCopied(false), 1800); };
    try {
      navigator.clipboard?.writeText(verifyUrl).then(done, () => {});
    } catch { /* clipboard unavailable */ }
  }, [verifyUrl]);

  return (
    <main className={styles.page} id="main" tabIndex={-1}>
      <div className={styles.card}>
        <div className={styles.brandMark}>
          <BookMarked size={28} className={styles.brandIcon} aria-hidden="true" focusable={false} />
          <span className={styles.brandText}>
            <BrandName name={cfg?.instance_name} accentClassName={styles.brandAccent} />
          </span>
        </div>

        <div className={styles.titleRow}>
          <KeyRound size={18} className={styles.titleIcon} />
          <h1 className={styles.title}>{t('Sign in with a magic link')}</h1>
        </div>

        {phase === 'starting' && (
          <div className={styles.center}>
            <Spinner size={26} />
            <p className={styles.muted}>{t('Preparing your magic link…')}</p>
          </div>
        )}

        {phase === 'waiting' && (
          <>
            <p className={styles.lead}>
              {t('On another device where you’re already signed in, scan this code or open the link below to authorise this device.')}
            </p>

            {qrcode ? (
              <div className={styles.qrWrap}>
                <img src={qrcode} alt={t('Magic link QR code')} className={styles.qr} width={180} height={180} />
              </div>
            ) : (
              <div className={styles.qrFallback}>
                <Smartphone size={28} />
                <span>{t('Open the link below on your signed-in device.')}</span>
              </div>
            )}

            <button type="button" className={styles.linkBox} onClick={onCopy} title={t('Copy link')}>
              <span className={styles.linkText}>{verifyUrl}</span>
              <span className={styles.copyIcon}>
                {copied ? <Check size={16} /> : <Copy size={16} />}
              </span>
            </button>
            {copied && <span className={styles.copied}>{t('Copied to clipboard')}</span>}

            <div className={styles.statusRow}>
              <Spinner size={15} />
              <span>{t('Waiting for you to authorise…')}</span>
            </div>

            <div className={styles.expiry}>
              <Clock size={13} />
              <span>{t('Expires in')} {fmtCountdown(remaining)}</span>
            </div>
          </>
        )}

        {phase === 'success' && (
          <div className={styles.center}>
            <div className={styles.successIcon}><ShieldCheck size={30} /></div>
            <p className={styles.successText}>{t('Authorised — signing you in…')}</p>
          </div>
        )}

        {(phase === 'expired' || phase === 'error') && (
          <div className={styles.center}>
            <p className={styles.muted}>
              {phase === 'expired'
                ? t('This magic link expired. Generate a new one to try again.')
                : t('Couldn’t start a magic link. Please try again.')}
            </p>
            <Button variant="primary" className={styles.retryBtn} onClick={begin} disabled={start.isPending}>
              <RefreshCw size={15} /> {t('Generate a new link')}
            </Button>
          </div>
        )}

        <Link href="/" className={styles.back}>
          <ArrowLeft size={15} aria-hidden="true" focusable={false} /> {t('Back to sign in')}
        </Link>
      </div>
    </main>
  );
}
