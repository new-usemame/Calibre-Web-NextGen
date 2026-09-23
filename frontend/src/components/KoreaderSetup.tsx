import { useEffect, useRef, useState } from 'react';
import { Check, Copy, Download, KeyRound, Pencil, Usb, X } from 'lucide-react';
import { apiUrl, ApiError, type KoreaderPairRequest } from '../lib/api';
import {
  useAnswerKoreaderPair, useKoreaderSetupBundle, useLookupKoreaderPair,
} from '../lib/queries';
import {
  displayUserCode, formatTypedCode, isOwnComputerAddress, minutesAgo, normalizeUserCode,
  pairingDeepLink, saveDownload,
} from '../lib/koreaderPairing';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useT } from '../lib/i18n';
import styles from '../pages/Devices.module.css';

type Answer = { request: KoreaderPairRequest; approved: boolean };

/** Drop ?pair and ?code once a code is answered, so a reload does not ask again. */
function forgetDeepLink() {
  const params = new URLSearchParams(window.location.search);
  if (!params.has('pair') && !params.has('code')) return;
  params.delete('pair');
  params.delete('code');
  const query = params.toString();
  window.history.replaceState(window.history.state, '',
    `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`);
}

/** The KOReader half of the e-reader pairing section: a ready-made plugin for
 *  a device on USB, a code for a device on Wi-Fi, and the manual route. */
export function KoreaderSetup({ enabled, serverUrl, onCopy, copied, onApproved }: {
  enabled: boolean;
  serverUrl: string;
  onCopy: (value: string) => void;
  copied: boolean;
  onApproved: () => void;
}) {
  const t = useT();
  const announce = useAnnouncer();
  const lookup = useLookupKoreaderPair();
  const answer = useAnswerKoreaderPair();
  const bundle = useKoreaderSetupBundle();
  const [code, setCode] = useState('');
  const [codeError, setCodeError] = useState('');
  const [request, setRequest] = useState<KoreaderPairRequest | null>(null);
  const [answered, setAnswered] = useState<Answer | null>(null);
  const [address, setAddress] = useState<string | null>(null);
  const [editingAddress, setEditingAddress] = useState(false);
  const [downloaded, setDownloaded] = useState<string | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const pairRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef<HTMLDivElement>(null);
  const lookupRef = useRef(lookup.mutate);
  lookupRef.current = lookup.mutate;

  // What the e-reader is told to use: this page's address unless changed.
  const deviceAddress = address ?? serverUrl;

  const errorText = (error: unknown): string => {
    const reason = error instanceof ApiError ? error.detail?.code : undefined;
    if (reason === 'not_found') {
      return t('No e-reader is waiting with this code. Check it, or get a new code on the e-reader.');
    }
    if (reason === 'already_decided') return t('This code has already been approved or declined.');
    if (reason === 'rate_limit_exceeded') return t('Too many attempts. Wait a minute and try again.');
    if (reason === 'koreader_sync_disabled') return t('KOReader sync is not enabled on this server.');
    if (reason === 'invalid_server') return t('Enter the address as http://host:port or https://host.');
    if (reason === 'plugin_unavailable') return t('The KOReader plugin is not part of this installation.');
    return t('Something went wrong. Try again.');
  };

  const find = (typed: string) => {
    const normalized = normalizeUserCode(typed);
    setAnswered(null);
    setRequest(null);
    if (!normalized) {
      setCodeError(t('Enter the 8 letters and digits the e-reader shows, like K7M4-QX2P.'));
      return;
    }
    setCodeError('');
    lookupRef.current(normalized, {
      onSuccess: (found) => setRequest(found),
      onError: (error) => setCodeError(errorText(error)),
    });
  };

  // <server>/pair lands here with ?pair=1, and with &code= from the QR code.
  // Open on the code box; look a scanned code up, but never answer it.
  useEffect(() => {
    if (!enabled) return undefined;
    const link = pairingDeepLink(window.location.search);
    if (!link.open) return undefined;
    const frame = window.requestAnimationFrame(() => {
      pairRef.current?.scrollIntoView({ block: 'start' });
      codeRef.current?.focus();
    });
    if (link.code) {
      setCode(displayUserCode(link.code));
      find(link.code);
    }
    return () => window.cancelAnimationFrame(frame);
    // Once per page load: the deep link is read when the page opens.
  }, [enabled]);

  // A found request takes focus, so a screen reader reads who is asking. The
  // Approve button deliberately does not: a second Enter must not approve.
  useEffect(() => {
    if (request) requestRef.current?.focus();
  }, [request]);

  const decide = (approve: boolean) => {
    if (!request) return;
    answer.mutate({ code: request.user_code, approve }, {
      onSuccess: (result) => {
        setAnswered({ request: result, approved: approve });
        setRequest(null);
        setCode('');
        forgetDeepLink();
        if (approve) {
          announce(t('Approved. {name} is finishing setup; its library appears in a few seconds.', { name: result.device_name }));
          onApproved();
        } else {
          announce(t('Declined. {name} was not connected.', { name: result.device_name }));
        }
      },
      onError: (error) => {
        setRequest(null);
        setCodeError(errorText(error));
      },
    });
  };

  const download = () => {
    bundle.mutate(deviceAddress, {
      onSuccess: ({ blob, filename }) => {
        const name = filename ?? 'cwngsync-ready-made.zip';
        saveDownload(blob, name);
        setDownloaded(name);
        setEditingAddress(false);
        announce(t('Downloaded {file}. It holds a password for this account: delete the zip once the plugin is on the e-reader.', { file: name }));
      },
    });
  };

  const finishEditing = () => {
    const typed = (address ?? '').trim();
    setAddress(typed === '' || typed === serverUrl ? null : typed);
    setEditingAddress(false);
  };

  if (!enabled) {
    return (
      <div>
        <h3>{t('KOReader')}</h3>
        <p role="status" className={styles.pairingStatus}>{t('KOReader sync is not enabled on this server.')}</p>
      </div>
    );
  }

  return (
    <div>
      <h3>{t('KOReader')}</h3>
      <p className={styles.pairingIntro}>
        {t('Two ways to connect. Each gives the e-reader its own app password, which you can revoke on your account page.')}
      </p>

      <p className={styles.pairingLabel} id="koreader-address-label">{t('Your e-reader will connect to')}</p>
      {editingAddress ? (
        <form className={styles.pairingUrlRow} onSubmit={(event) => { event.preventDefault(); finishEditing(); }}>
          <input className={styles.addressInput} aria-labelledby="koreader-address-label"
            value={deviceAddress} autoComplete="url" inputMode="url" spellCheck={false} autoFocus
            onChange={(event) => setAddress(event.target.value)} />
          <button type="submit">{t('Done')}</button>
        </form>
      ) : (
        <div className={styles.pairingUrlRow}>
          <code>{deviceAddress}</code>
          <button type="button" onClick={() => onCopy(deviceAddress)}>
            <Copy size={16} aria-hidden="true" focusable={false} />
            {copied ? t('Copied') : t('Copy server address')}
          </button>
          <button type="button" onClick={() => setEditingAddress(true)}>
            <Pencil size={16} aria-hidden="true" focusable={false} /> {t('Change')}
          </button>
        </div>
      )}
      {isOwnComputerAddress(deviceAddress) && (
        <p className={styles.pairingAlert}>
          {t('This is this computer\'s own address, which the e-reader cannot reach. Choose Change and enter the address the e-reader can use, such as http://192.168.1.20:8083.')}
        </p>
      )}

      <div className={styles.koreaderOptions}>
        <div className={styles.koreaderOption}>
          <h4><Usb size={16} aria-hidden="true" focusable={false} /> {t('Ready-made plugin (USB)')}</h4>
          <p>{t('For an e-reader you can plug into this computer. The plugin already knows your server and account: nothing to type on the e-reader.')}</p>
          <button type="button" className={`${styles.primaryButton} ${styles.koreaderAction}`}
            disabled={bundle.isPending} onClick={download}>
            <Download size={16} aria-hidden="true" focusable={false} />
            {bundle.isPending ? t('Preparing…') : t('Download ready-made plugin')}
          </button>
          {bundle.error && <p role="alert" className={styles.pairingAlert}>{errorText(bundle.error)}</p>}
          <ol>
            <li>{t('Unzip the download and copy the cwngsync.koplugin folder into koreader/plugins on the e-reader (on a Kobo, .adds/koreader/plugins).')}</li>
            <li>{t('Eject the e-reader and restart KOReader with Wi-Fi on. Your library appears in a moment.')}</li>
          </ol>
          {downloaded && (
            <p role="status" className={styles.koreaderDone}>
              <Check size={16} aria-hidden="true" focusable={false} />
              {t('Downloaded {file}. It holds a password for this account: delete the zip once the plugin is on the e-reader.', { file: downloaded })}
            </p>
          )}
        </div>

        <div className={styles.koreaderOption} ref={pairRef} id="koreader-pair">
          <h4><KeyRound size={16} aria-hidden="true" focusable={false} /> {t('Pair with a code')}</h4>
          <ol>
            <li>{t('On the e-reader, open Tools ▸ CWNG library ▸ Connect this device and choose Connect with a code.')}</li>
            <li>{t('When it asks for your CWNG address, type the address above.')}</li>
            <li>{t('Enter the code the e-reader shows, or scan its QR code with your phone.')}</li>
          </ol>
          <form className={styles.codeForm} onSubmit={(event) => { event.preventDefault(); find(code); }}>
            <label htmlFor="koreader-code" className={styles.pairingLabel}>{t('Code on the e-reader')}</label>
            <div className={styles.pairingUrlRow}>
              <input ref={codeRef} id="koreader-code" className={styles.codeInput}
                value={code} placeholder="K7M4-QX2P" autoComplete="off" autoCapitalize="characters"
                spellCheck={false} inputMode="text" maxLength={12}
                aria-invalid={codeError ? true : undefined}
                aria-describedby={codeError ? 'koreader-code-error' : undefined}
                onChange={(event) => { setCode(formatTypedCode(event.target.value)); setCodeError(''); }} />
              <button type="submit" disabled={lookup.isPending}>
                {lookup.isPending ? t('Checking…') : t('Continue')}
              </button>
            </div>
          </form>
          {codeError && <p id="koreader-code-error" role="alert" className={styles.pairingAlert}>{codeError}</p>}

          {request && (
            <div ref={requestRef} tabIndex={-1} className={styles.pairRequest}
              role="group" aria-labelledby="koreader-request-title" aria-describedby="koreader-request-detail">
              <p id="koreader-request-title" className={styles.pairRequestTitle}>
                {t('{name} wants to connect to your account.', { name: request.device_name })}
              </p>
              <p id="koreader-request-detail" className={styles.pairingStatus}>
                {t('Asked {when} from {address}.', {
                  when: minutesAgo(request.requested_at, Date.now(), document.documentElement.lang || undefined)
                    ?? t('just now'),
                  address: request.ip ?? t('an unknown address'),
                })}
                {' '}{t('Approve only if this is your e-reader.')}
              </p>
              <div className={styles.pairRequestActions}>
                <button type="button" className={styles.primaryButton} disabled={answer.isPending}
                  onClick={() => decide(true)}>
                  <Check size={16} aria-hidden="true" focusable={false} /> {t('Approve')}
                </button>
                <button type="button" className={styles.button} disabled={answer.isPending}
                  onClick={() => decide(false)}>
                  <X size={16} aria-hidden="true" focusable={false} /> {t('Deny')}
                </button>
              </div>
            </div>
          )}

          {answered && (
            <p role="status" className={answered.approved ? styles.koreaderDone : styles.pairingStatus}>
              {answered.approved && <Check size={16} aria-hidden="true" focusable={false} />}
              {answered.approved
                ? t('Approved. {name} is finishing setup; its library appears in a few seconds.', { name: answered.request.device_name })
                : t('Declined. {name} was not connected.', { name: answered.request.device_name })}
            </p>
          )}
        </div>
      </div>

      <details className={styles.manualSetup}>
        <summary>{t('Set up by hand')}</summary>
        <ol>
          <li><a href={apiUrl('/kosync')}>{t('Install or update the NextGen Sync plugin.')}</a></li>
          <li>{t('On the e-reader, open Tools ▸ CWNG library ▸ Connect this device and choose Sign in with username and password.')}</li>
          <li>{t('Type the address above, then this account\'s username and its password or one of its app passwords.')}</li>
        </ol>
      </details>
    </div>
  );
}
