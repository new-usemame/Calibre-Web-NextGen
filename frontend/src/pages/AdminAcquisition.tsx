import { useId, useRef, useState } from 'react';
import { Link } from 'wouter';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ChevronLeft, CheckCircle2, Globe, Plus, Users } from 'lucide-react';
import {
  approveAcquisitionJob,
  rejectAcquisitionJob,
  getAcquisitionConnection,
  editAcquisitionConnection,
  deleteAcquisitionConnection,
  createAcquisitionConnection,
  getAcquisitionApprovalQueue,
  getAcquisitionConnections,
  getAcquisitionGrants,
  getAcquisitionSettings,
  probeAcquisitionConnection,
  setAcquisitionConnectionEnabled,
  setAcquisitionEnabled,
  setAcquisitionGrant,
  type AcquisitionConnectionInput,
  type AcquisitionProbe,
} from '../lib/acquisition';
import { useAcquisitionErrorText, useRuntimeReasonText } from '../lib/acquisitionCopy';
import {
  acquisitionRequesterLabel,
  acquisitionSectionView,
  connectionSwitchDisabled,
  isRowPending,
} from '../lib/acquisitionViewState';
import { ApiError } from '../lib/api';
import { useT } from '../lib/i18n';
import { useMe } from '../lib/queries';
import { useAnnouncer } from '../lib/a11y/announcer';
import { SpinnerCentered } from '../components/Spinner';
import { SectionError } from '../components/SectionError';
import styles from './AdminAcquisition.module.css';

function errorCode(error: unknown): string | undefined {
  if (error instanceof ApiError && error.detail && typeof error.detail.code === 'string') {
    return error.detail.code;
  }
  return undefined;
}

const EMPTY_CONNECTION: AcquisitionConnectionInput & { label: string } = {
  label: '', endpoint: '', auth_kind: 'none', username: '', secret: '',
  allow_private_network: false, allow_mobi: false, category: '7020', client_id: '', preset: 'newznab',
  remote_path: '', local_path: '', download_origins: [],
};

/** Where a failed action's message belongs. Each one is rendered beside the
 *  control that caused it: a message at the top of the page for a switch far
 *  down it is a message the administrator does not connect to what they did. */
type ActionScope = 'feature' | 'connection' | 'grant' | 'queue';

export function AdminAcquisition() {
  const t = useT();
  const announce = useAnnouncer();
  const queryClient = useQueryClient();
  const formId = useId();
  const reasonText = useRuntimeReasonText();
  const protocolErrorText = useAcquisitionErrorText();

  const [draft, setDraft] = useState(EMPTY_CONNECTION);
  const [adapter, setAdapter] = useState('opds');
  const isClient = ['sabnzbd', 'nzbget', 'qbittorrent', 'transmission'].includes(adapter);
  const passwordClient = ['nzbget', 'qbittorrent', 'transmission'].includes(adapter);
  const [editing, setEditing] = useState<string | null>(null);
  const [loadedPolicy, setLoadedPolicy] = useState<{ endpoint: string; local: boolean; revision: number } | null>(null);
  const [downloadOrigins, setDownloadOrigins] = useState('');
  const [trackerOrigins, setTrackerOrigins] = useState('');
  const [deleting, setDeleting] = useState<string | null>(null);
  const formHeading = useRef<HTMLHeadingElement>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionErrors, setActionErrors] = useState<Partial<Record<ActionScope, string>>>({});
  const [probes, setProbes] = useState<Record<string, AcquisitionProbe | { error: string }>>({});

  const failed = (scope: ActionScope, message: string) =>
    setActionErrors((current) => ({ ...current, [scope]: message }));
  const succeeded = (scope: ActionScope) =>
    setActionErrors((current) => {
      if (!(scope in current)) return current;
      const next = { ...current };
      delete next[scope];
      return next;
    });

  const settings = useQuery({ queryKey: ['acquisition', 'admin', 'settings'], queryFn: getAcquisitionSettings });
  const connections = useQuery({ queryKey: ['acquisition', 'admin', 'connections'], queryFn: getAcquisitionConnections });
  const { data: me } = useMe();

  const enabled = !!settings.data?.enabled;
  const migration = settings.data?.migration_status;

  // The grants and approval endpoints answer 404 while the feature is off, so
  // don't ask for them until it is on.
  const grants = useQuery({
    queryKey: ['acquisition', 'admin', 'grants'],
    queryFn: getAcquisitionGrants,
    enabled,
  });
  const queue = useQuery({
    queryKey: ['acquisition', 'admin', 'queue'],
    queryFn: getAcquisitionApprovalQueue,
    enabled,
    refetchInterval: enabled ? 10000 : false,
  });

  const refresh = (...keys: string[]) => {
    for (const key of keys) void queryClient.invalidateQueries({ queryKey: ['acquisition', 'admin', key] });
  };

  const toggleFeature = useMutation({
    mutationFn: setAcquisitionEnabled,
    onSuccess: (state) => {
      succeeded('feature');
      queryClient.setQueryData(['acquisition', 'admin', 'settings'], state);
      refresh('grants', 'queue', 'connections');
      // The nav gate and the user page both read /me and the user bootstrap.
      void queryClient.invalidateQueries({ queryKey: ['me'] });
      void queryClient.invalidateQueries({ queryKey: ['acquisition', 'bootstrap'] });
      announce(state.enabled ? t('Book sources are on.') : t('Book sources are off.'));
    },
    onError: (error) => {
      failed('feature', errorCode(error) === 'needs_review'
        ? t('Legacy acquisition permissions need review before this can be switched on.')
        : t('The setting could not be changed.'));
      // The switch is drawn from server state, so re-read it rather than leave
      // the checkbox showing a change that did not happen.
      refresh('settings');
    },
  });

  const addConnection = useMutation({
    mutationFn: async () => {
      const value: Partial<AcquisitionConnectionInput> = {
        endpoint: draft.endpoint.trim(), auth_kind: draft.auth_kind,
        username: draft.auth_kind === 'basic' ? draft.username : '',
      };
      if (!editing || loadedPolicy?.local !== draft.allow_private_network || loadedPolicy?.endpoint !== draft.endpoint.trim()) {
        value.allow_private_network = draft.allow_private_network;
      }
      if (!editing || draft.secret) value.secret = (adapter === 'opds' || adapter === 'transmission') && draft.auth_kind === 'none' ? '' : draft.secret;
      if (adapter === 'opds' || isClient) value.allow_mobi = draft.allow_mobi === true;
      if (adapter === 'newznab') Object.assign(value, { category: draft.category, client_id: draft.client_id, preset: draft.preset, download_origins: downloadOrigins.split(',').map((item) => item.trim()).filter(Boolean), tracker_origins: trackerOrigins.split(',').map((item) => item.trim()).filter(Boolean) });
      if (isClient) Object.assign(value, { category: draft.category, remote_path: draft.remote_path, local_path: draft.local_path });
      if (editing) await editAcquisitionConnection(editing, draft.label.trim(), value, loadedPolicy?.revision);
      else await createAcquisitionConnection(draft.label.trim(), value as AcquisitionConnectionInput, adapter);
    },
    onSuccess: () => {
      setDraft(EMPTY_CONNECTION);
      setEditing(null); setLoadedPolicy(null); setDownloadOrigins(''); setTrackerOrigins('');
      setAdapter('opds');
      setFormError(null);
      refresh('connections');
      announce(t('Connection saved. Test it, then switch it on.'));
    },
    onError: (error) => {
      const code = errorCode(error);
      if (code === 'credential_required_for_new_origin') {
        setFormError(t('Re-enter the credential when changing the connection to a different server.'));
      } else if (code === 'connection_changed') {
        setFormError(t('Connection changed. Reload its settings before saving.'));
      } else if (code === 'conflict') {
        setFormError(t('Finish or cancel outstanding requests before editing this connection.'));
      } else if (code === 'needs_review') {
        setFormError(t('Legacy acquisition permissions need review before a catalog can be added.'));
      } else if (code === 'private_network_not_allowed') {
        setFormError(t('That address cannot be reached on a local network. Loopback, link-local and cloud metadata addresses are never allowed.'));
      } else {
        setFormError(t('Check the address and the sign-in details.'));
      }
    },
  });

  const loadConnection = useMutation({
    mutationFn: getAcquisitionConnection,
    onSuccess: (row) => {
      succeeded('connection');
      setEditing(row.id); setAdapter(row.adapter); setFormError(null);
      setLoadedPolicy({ endpoint: row.config.endpoint, local: !!row.config.private_origins?.length, revision: row.revision });
      setDownloadOrigins(row.config.download_origins?.join(', ') ?? '');
      setTrackerOrigins(row.config.tracker_origins?.join(', ') ?? '');
      setDraft({ ...EMPTY_CONNECTION, ...row.config, label: row.label, secret: '',
        allow_private_network: !!row.config.private_origins?.length });
      requestAnimationFrame(() => { formHeading.current?.focus(); formHeading.current?.scrollIntoView({ block: 'center' }); });
    },
    onError: () => failed('connection', t('The connection settings could not be loaded.')),
  });
  const removeConnection = useMutation({
    mutationFn: deleteAcquisitionConnection,
    onSuccess: () => { setDeleting(null); refresh('connections'); announce(t('Connection deleted.')); },
    onError: () => failed('connection', t('The connection could not be deleted. Finish or cancel outstanding requests first.')),
  });
  const reject = useMutation({
    mutationFn: rejectAcquisitionJob,
    onSuccess: () => { succeeded('queue'); refresh('queue'); announce(t('Request rejected.')); },
    onError: () => { failed('queue', t('The request could not be rejected. It may have already moved on.')); refresh('queue'); },
  });

  const toggleConnection = useMutation({
    mutationFn: (variables: { id: string; enabled: boolean }) =>
      setAcquisitionConnectionEnabled(variables.id, variables.enabled),
    onSuccess: () => { succeeded('connection'); refresh('connections'); },
    onError: (error) => {
      const code = errorCode(error);
      failed('connection',
        // Switching a catalog on re-checks the migration, so this is the
        // likeliest refusal, not a missing row.
        code === 'needs_review' ? t('Legacy acquisition permissions need review before a catalog can be made available.')
          : code === 'not_found' ? t('That catalog no longer exists.')
            : t('The catalog could not be changed.'));
      refresh('connections');
    },
  });

  const probe = useMutation({
    mutationFn: probeAcquisitionConnection,
    onSuccess: (result, id) => {
      setProbes((current) => ({ ...current, [id]: result }));
      announce(t('Connection works.'));
    },
    onError: (error, id) => {
      setProbes((current) => ({
        ...current,
        [id]: { error: errorCode(error) === 'source_unavailable'
          ? t('The connection did not answer with a response we can read.')
          : errorCode(error) ? protocolErrorText(errorCode(error)!) : t('The connection test failed.') },
      }));
    },
  });

  const grant = useMutation({
    mutationFn: (variables: { id: number; access: boolean; auto_approve: boolean }) =>
      setAcquisitionGrant(variables.id, { access: variables.access, auto_approve: variables.auto_approve }),
    onSuccess: (_result, variables) => {
      succeeded('grant');
      refresh('grants');
      // An administrator granting themselves access changes their own nav gate.
      // /me is what decides whether "Find books" is there at all, so without
      // this they would not see the entry until a reload.
      if (me && variables.id === me.id) {
        void queryClient.invalidateQueries({ queryKey: ['me'] });
        void queryClient.invalidateQueries({ queryKey: ['acquisition', 'bootstrap'] });
      }
    },
    onError: () => {
      // Every AdmissionError flattens to invalid_request on the wire, so the
      // client cannot tell "auto-approve without access" (which this page's
      // own checkboxes already prevent from being sent) from "the feature was
      // switched off underneath you" — which is the one that actually happens.
      // Don't assert a cause the response does not carry.
      failed('grant', t('That permission could not be changed. Check that book sources are still switched on, then try again.'));
      refresh('grants');
    },
  });

  const approve = useMutation({
    mutationFn: approveAcquisitionJob,
    onSuccess: () => { succeeded('queue'); refresh('queue'); announce(t('Request approved.')); },
    onError: (error) => {
      const code = errorCode(error);
      failed('queue',
        code === 'forbidden' ? t('That account is no longer allowed to request books.')
          : code === 'not_found' ? t('That request is no longer waiting.')
            : code === 'conflict' ? t('That request has already moved on.')
              : t('The request could not be approved.'));
      refresh('queue');
    },
  });

  if (settings.isLoading) return <SpinnerCentered />;

  // Without the settings payload every control below is drawn from `undefined`
  // — the feature reads as off, the migration reads as not-ready, and every
  // switch is disabled. That is indistinguishable from a correctly configured
  // server with the feature deliberately off, so say what happened instead of
  // showing a page that quietly lies about the state of the instance.
  //
  // Only when there is nothing to draw, though. A refresh-on-focus that fails
  // behind a page the administrator is already using must not replace it: the
  // settings it is showing are stale, not absent, and that case is handled
  // below with a message that leaves the page standing.
  // `hasData` is tested on the payload, never on `isSuccess`. query-core sets
  // `status: 'error'` on a background failure while deliberately keeping the
  // previous `data` — so `isSuccess` goes false with perfectly good settings
  // still in hand, and keying off it would tear the page down for exactly the
  // transient failure this branch exists to survive.
  const settingsView = acquisitionSectionView({
    isLoading: settings.isLoading,
    isError: settings.isError,
    hasData: settings.data !== undefined,
    isEmpty: false,
  });

  if (settingsView.body === 'error') {
    return (
      <div className={styles.container}>
        <Link href="/admin" className={styles.back}>
          <ChevronLeft size={16} aria-hidden="true" focusable={false} />
          <span>{t('Admin')}</span>
        </Link>
        <header className={styles.heading}>
          <Globe aria-hidden="true" focusable={false} />
          <h1>{t('Book sources')}</h1>
        </header>
        <SectionError
          message={errorCode(settings.error) === 'forbidden'
            ? t('This account is no longer an administrator.')
            : t('These settings could not be loaded, so nothing on this page can be trusted yet.')}
          onRetry={() => void settings.refetch()}
          retrying={settings.isFetching}
        />
      </div>
    );
  }

  const runtime = settings.data?.runtime;
  const rows = connections.data?.connections ?? [];
  const connectionsView = acquisitionSectionView({
    isLoading: connections.isLoading,
    isError: connections.isError,
    hasData: connections.data !== undefined,
    isEmpty: rows.length === 0,
  });

  const grantRows = grants.data?.users ?? [];
  const grantsView = acquisitionSectionView({
    enabled,
    isLoading: grants.isLoading,
    isError: grants.isError,
    hasData: grants.data !== undefined,
    isEmpty: grantRows.length === 0,
  });

  const queueRows = queue.data?.jobs ?? [];
  const queueView = acquisitionSectionView({
    enabled,
    isLoading: queue.isLoading,
    isError: queue.isError,
    hasData: queue.data !== undefined,
    isEmpty: queueRows.length === 0,
  });

  // Only an actual directory is evidence about who exists. Built from the
  // payload rather than from the map being non-empty, so the queue — which
  // loads independently and can answer first — cannot report a live account as
  // deleted while the directory is still on its way. A stale-but-present
  // directory still answers the question better than a guess does.
  const ownerNames = new Map(grantRows.map((user) => [user.id, user.name]));

  return (
    <div className={styles.container}>
      <Link href="/admin" className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" focusable={false} />
        <span>{t('Admin')}</span>
      </Link>

      <header className={styles.heading}>
        <Globe aria-hidden="true" focusable={false} />
        <h1>{t('Book sources')}</h1>
      </header>
      <p className={styles.lede}>
        {t('Let people request books from an OPDS catalog or an indexer connected to a download client. A requested book is imported into this library through the normal ingest path, credited to the account that asked for it.')}
      </p>

      {/* Reached only with settings still on screen from an earlier, good
          read. The page stays up — losing it to a transient refresh failure
          would cost the administrator more than the staleness does — but the
          switches below are no longer confirmed by the server, so say so. */}
      {settingsView.showError && (
        <SectionError
          message={t('These settings could not be refreshed, so what is shown may be out of date.')}
          onRetry={() => void settings.refetch()}
          retrying={settings.isFetching}
        />
      )}

      {migration === 'needs_review' && (
        <div className={styles.notice} role="status">
          <AlertTriangle size={16} aria-hidden="true" focusable={false} />
          <p>{t('This database carries permissions from the old experimental Store. They need review before book sources can be switched on.')}</p>
        </div>
      )}
      {migration === 'unavailable' && (
        <div className={styles.notice} role="status">
          <AlertTriangle size={16} aria-hidden="true" focusable={false} />
          <p>{t('The acquisition tables are not ready. Restart the server, then reload this page.')}</p>
        </div>
      )}

      <section className={styles.card} aria-labelledby={`${formId}-feature`}>
        <h2 id={`${formId}-feature`}>{t('Feature')}</h2>
        <label className={styles.switchRow}>
          <input
            type="checkbox"
            checked={enabled}
            disabled={migration !== 'ready' || toggleFeature.isPending}
            onChange={(event) => toggleFeature.mutate(event.target.checked)}
          />
          <span>
            <span className={styles.switchLabel}>{t('Allow requests from book sources')}</span>
            <span className={styles.hint}>{t('Off by default. Nothing is fetched and no permission changes until you turn this on.')}</span>
          </span>
        </label>

        {actionErrors.feature && <p className={styles.bad} role="alert">{actionErrors.feature}</p>}

        {runtime && (runtime.available ? (
          <p className={styles.status} data-ok={true}>{t('Ready to run requests.')}</p>
        ) : (
          <div className={styles.status} data-ok={false}>
            <p>{t('Requests cannot run yet:')}</p>
            {/* The administrator is the person who can actually fix these, so
                they get the sentence, not the machine code they used to get.
                role="list" is explicit because the global reset sets
                list-style:none, which drops list semantics in Safari/VoiceOver
                and would announce these as loose text. */}
            <ul className={styles.reasons} role="list">
              {runtime.reasons.map((reason) => <li key={reason}>{reasonText(reason)}</li>)}
            </ul>
          </div>
        ))}
      </section>

      <section className={styles.card} aria-labelledby={`${formId}-connections`}>
        <h2 id={`${formId}-connections`}>{t('Catalogs and download clients')}</h2>

        {actionErrors.connection && <p className={styles.bad} role="alert">{actionErrors.connection}</p>}

        {connectionsView.showError && (
          <SectionError
            message={t('The catalogs could not be loaded.')}
            onRetry={() => void connections.refetch()}
            retrying={connections.isFetching}
          />
        )}

        {connectionsView.body === 'loading' ? <SpinnerCentered size={24} />
          : connectionsView.body === 'empty' ? (
          <p className={styles.hint}>{t('No catalogs yet.')}</p>
        ) : connectionsView.body === 'ready' ? (
          <ul className={styles.connections} role="list">
            {rows.map((connection) => {
              const result = probes[connection.id];
              return (
                <li key={connection.id} className={styles.connection}>
                  <div className={styles.connectionMain}>
                    <p className={styles.connectionLabel}>{connection.label}</p>
                    <p className={styles.hint}>{connection.adapter.toUpperCase()}</p>
                    {result && 'error' in result ? (
                      // A failed test announces, the same way a successful one
                      // does. Without this the administrator who is listening
                      // rather than looking hears confirmation of success and
                      // silence on failure.
                      <p className={styles.bad} role="alert">{result.error}</p>
                    ) : result ? (
                      <p className={styles.ok}>
                        <CheckCircle2 size={14} aria-hidden="true" focusable={false} />
                        {t('{title} — {protocol}{search}', {
                          title: result.title || t('Untitled catalog'),
                          protocol: result.protocol.toUpperCase(),
                          search: result.search_advertised ? t(', search supported') : '',
                        })}
                      </p>
                    ) : (
                      <p className={styles.hint}>{t('Not tested in this session.')}</p>
                    )}
                  </div>
                  <div className={styles.connectionActions}>
                    <button type="button" className={styles.secondary}
                      aria-label={t('Edit {name}', { name: connection.label })}
                      disabled={loadConnection.isPending}
                      onClick={() => loadConnection.mutate(connection.id)}>{t('Edit')}</button>
                    <button type="button" className={styles.secondary}
                      aria-label={t('Delete {name}', { name: connection.label })}
                      onClick={() => setDeleting(connection.id)}>{t('Delete')}</button>
                    <button
                      type="button"
                      className={styles.secondary}
                      // Only this catalog's test, not every catalog's.
                      disabled={isRowPending(probe.isPending, probe.variables, connection.id)}
                      onClick={() => probe.mutate(connection.id)}
                    >
                      {t('Test connection')}
                    </button>
                    <label className={styles.inlineSwitch}>
                      <input
                        type="checkbox"
                        checked={connection.enabled}
                        // Withdrawing a catalog is a safety control: the server
                        // refuses only the enable direction while the migration
                        // is unresolved, so only that direction is blocked here.
                        disabled={connectionSwitchDisabled({
                          currentlyEnabled: connection.enabled,
                          migrationStatus: migration,
                          pending: isRowPending(
                            toggleConnection.isPending, toggleConnection.variables?.id, connection.id,
                          ),
                        })}
                        onChange={(event) => toggleConnection.mutate({ id: connection.id, enabled: event.target.checked })}
                      />
                      <span>{['sabnzbd', 'nzbget', 'qbittorrent', 'transmission'].includes(connection.adapter) ? t('Enabled') : t('Available to users')}</span>
                    </label>
                  </div>
                  {deleting === connection.id && <div className={styles.connectionActions}>
                    <p>{t('Delete this connection and its saved credentials? Request history is kept.')}</p>
                    <button type="button" className={styles.secondary} disabled={removeConnection.isPending}
                      onClick={() => removeConnection.mutate(connection.id)}>{t('Confirm deletion')}</button>
                    <button type="button" className={styles.secondary} onClick={() => setDeleting(null)}>{t('Keep connection')}</button>
                  </div>}
                </li>
              );
            })}
          </ul>
        ) : null}

        <form
          className={styles.form}
          onSubmit={(event) => { event.preventDefault(); addConnection.mutate(); }}
        >
          <h3 ref={formHeading} tabIndex={-1}>{editing ? t('Edit connection') : t('Add a connection')}</h3>
          {editing && <p className={styles.hint}>{t('Leave the credential blank to keep it. Saving switches the connection off and expires old selections; test it before enabling it again.')}</p>}
          <div className={styles.fields}>
            <label className={styles.field}>
              <span>{t('Connection type')}</span>
              <select aria-invalid={formError ? true : undefined} aria-describedby={formError ? `${formId}-error` : undefined} value={adapter} disabled={!!editing} onChange={(event) => {
                setAdapter(event.target.value);
                setDraft({ ...EMPTY_CONNECTION, label: draft.label, category: ['opds', 'newznab'].includes(event.target.value) ? '7020' : 'books', auth_kind: ['nzbget', 'qbittorrent', 'transmission'].includes(event.target.value) ? 'basic' : 'none' });
              }}>
                <option value="opds">{t('OPDS catalog')}</option>
                <option value="newznab">{t('Newznab / Torznab indexer')}</option>
                <option value="sabnzbd">{t('SABnzbd download client')}</option>
                <option value="nzbget">{t('NZBGet download client')}</option>
                <option value="qbittorrent">{t('qBittorrent download client')}</option>
                <option value="transmission">{t('Transmission download client')}</option>
              </select>
            </label>
            {adapter === 'newznab' && <>
              <label className={styles.field}><span>{t('Indexer preset')}</span>
                <select aria-invalid={formError ? true : undefined} aria-describedby={formError ? `${formId}-error` : undefined} value={draft.preset} onChange={(event) => setDraft({ ...draft, preset: event.target.value as AcquisitionConnectionInput['preset'] })}>
                  <option value="newznab">Newznab</option><option value="torznab">Torznab</option>
                  <option value="prowlarr">Prowlarr</option><option value="jackett">Jackett</option>
                </select>
              </label>
              <label className={styles.field}><span>{t('Additional local download origins')}</span>
                <input value={downloadOrigins}
                  placeholder="http://indexer:8090"
                  aria-describedby={`${formId}-origins-hint`}
                  onChange={(event) => setDownloadOrigins(event.target.value)} />
                <span id={`${formId}-origins-hint`}>{t('If Prowlarr redirects to another source on your local network, list its exact origin here. Separate origins with commas. Credentials stay scoped to their original origin.')}</span>
              </label>
              <label className={styles.field}><span>{t('Allowed torrent tracker origins')}</span>
                <input value={trackerOrigins} placeholder="https://tracker.example, udp://tracker.example:6969" aria-invalid={formError ? true : undefined} aria-describedby={`${formId}-trackers-hint${formError ? ` ${formId}-error` : ''}`} onChange={(event) => setTrackerOrigins(event.target.value)} />
                <span id={`${formId}-trackers-hint`}>{t('List the exact HTTP, HTTPS or UDP tracker origins you trust, separated by commas. Torrents using other trackers are blocked. Source credentials are never sent to trackers.')}</span>
              </label>
              <label className={styles.field}><span>{t('Download client')}</span>
                <select aria-invalid={formError ? true : undefined} aria-describedby={formError ? `${formId}-error` : undefined} value={draft.client_id} required onChange={(event) => setDraft({ ...draft, client_id: event.target.value })}>
                  <option value="">{t('Choose a download client connection')}</option>
                  {rows.filter((row) => ['sabnzbd', 'nzbget', 'qbittorrent', 'transmission'].includes(row.adapter)).map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}
                </select>
              </label>
            </>}
            {adapter !== 'opds' && <label className={styles.field}><span>{isClient ? t('Client category or label') : t('Book category ID')}</span>
              <input aria-invalid={formError ? true : undefined} aria-describedby={formError ? `${formId}-error` : undefined} value={draft.category} required onChange={(event) => setDraft({ ...draft, category: event.target.value })} />
            </label>}
            {isClient && <>
              <label className={styles.field}><span>{t('Completed folder as the download client sees it')}</span>
                <input aria-invalid={formError ? true : undefined} aria-describedby={formError ? `${formId}-error` : undefined} value={draft.remote_path} required placeholder="/downloads/complete" onChange={(event) => setDraft({ ...draft, remote_path: event.target.value })} />
              </label>
              <label className={styles.field}><span>{t('Same completed folder inside CWNG')}</span>
                <input aria-invalid={formError ? true : undefined} aria-describedby={formError ? `${formId}-error` : undefined} value={draft.local_path} required placeholder="/downloads/complete" onChange={(event) => setDraft({ ...draft, local_path: event.target.value })} />
              </label>
            </>}
            <label className={styles.field}>
              <span>{t('Name')}</span>
              <input
                aria-invalid={formError ? true : undefined}
                aria-describedby={formError ? `${formId}-error` : undefined}
                value={draft.label}
                maxLength={200}
                required
                onChange={(event) => setDraft({ ...draft, label: event.target.value })}
              />
            </label>
            <label className={styles.field}>
              <span>{adapter === 'opds' ? t('Catalog address') : t('API endpoint')}</span>
              <input
                aria-invalid={formError ? true : undefined}
                aria-describedby={formError ? `${formId}-error` : undefined}
                type="url"
                inputMode="url"
                value={draft.endpoint}
                required
                placeholder="https://…"
                onChange={(event) => setDraft({ ...draft, endpoint: event.target.value })}
              />
            </label>
            {(adapter === 'opds' || adapter === 'transmission') && <label className={styles.field}>
              <span>{t('Sign-in')}</span>
              <select
                value={draft.auth_kind}
                onChange={(event) => setDraft({
                  ...draft,
                  auth_kind: event.target.value as AcquisitionConnectionInput['auth_kind'],
                })}
              >
                <option value="none">{t('None')}</option>
                <option value="basic">{t('Username and password')}</option>
                {adapter === 'opds' && <option value="bearer">{t('Token')}</option>}
              </select>
            </label>}
            {draft.auth_kind === 'basic' && (
              <label className={styles.field}>
                <span>{t('Username')}</span>
                <input
                aria-invalid={formError ? true : undefined}
                aria-describedby={formError ? `${formId}-error` : undefined}
                  value={draft.username}
                  autoComplete="off"
                  onChange={(event) => setDraft({ ...draft, username: event.target.value })}
                />
              </label>
            )}
            {((adapter !== 'opds' && !passwordClient) || draft.auth_kind !== 'none') && (
              <label className={styles.field}>
                <span>{adapter !== 'opds' && !passwordClient ? t('API key') : draft.auth_kind === 'basic' ? t('Password') : t('Token')}</span>
                <input
                aria-invalid={formError ? true : undefined}
                aria-describedby={formError ? `${formId}-error` : undefined}
                  type="password"
                  value={draft.secret}
                  autoComplete="new-password"
                  onChange={(event) => setDraft({ ...draft, secret: event.target.value })}
                />
              </label>
            )}
          </div>
          <label className={styles.switchRow}>
            <input
                aria-invalid={formError ? true : undefined}
                aria-describedby={formError ? `${formId}-error` : undefined}
              type="checkbox"
              checked={draft.allow_private_network ?? false}
              onChange={(event) => setDraft({
                ...draft, allow_private_network: event.target.checked,
              })}
            />
            <span>
              <span className={styles.switchLabel}>
                {t('This connection is on my home or local network')}
              </span>
              <span className={styles.hint}>
                {t('Turn this on for a connection running on your own network, such as a catalog, indexer or download client on a 192.168 or 10.x address. It applies to this connection only. Loopback, link-local and cloud metadata addresses are never allowed.')}
              </span>
            </span>
          </label>
          {adapter === 'opds' && <label className={styles.switchRow}>
            <input type="checkbox" aria-label={t('Allow direct DRM-free MOBI 6 books')}
              aria-describedby={`${formId}-mobi-hint`}
              checked={draft.allow_mobi === true}
              onChange={(event) => setDraft({ ...draft, allow_mobi: event.target.checked })} />
            <span>
              <span className={styles.switchLabel}>{t('Allow direct DRM-free MOBI 6 books')}</span>
              <span id={`${formId}-mobi-hint`} className={styles.hint}>
                {t('Applies only to this catalog. The server upload-format setting must also allow MOBI. Normal conversion settings apply; without conversion, MOBI can be downloaded but cannot be opened in the web reader. Download clients have a separate format opt-in.')}
              </span>
            </span>
          </label>}
          {isClient && <label className={styles.switchRow}>
            <input type="checkbox" aria-label={t('Allow completed DRM-free MOBI 6 books')}
              aria-describedby={`${formId}-client-mobi-hint`}
              checked={draft.allow_mobi === true}
              onChange={(event) => setDraft({ ...draft, allow_mobi: event.target.checked })} />
            <span>
              <span className={styles.switchLabel}>{t('Allow completed DRM-free MOBI 6 books')}</span>
              <span id={`${formId}-client-mobi-hint`} className={styles.hint}>
                {t('Applies only to this download client. The server upload-format setting must also allow MOBI. Normal conversion settings apply; without conversion, MOBI can be downloaded but cannot be opened in the web reader.')}
              </span>
            </span>
          </label>}
          {isClient && <p className={styles.hint}>{t('CWNG copies completed books allowed by this connection and the server format settings. It does not move files, delete client jobs, or change seeding limits. NZBGet needs credentials that can read configuration, queue and history. qBittorrent needs an existing category. Transmission uses the configured folder and label.')}</p>}
          {formError && <p id={`${formId}-error`} className={styles.bad} role="alert">{formError}</p>}
          <p className={styles.hint}>
            {t('A new catalog is added switched off. Test it first, then make it available.')}
          </p>
          <button
            type="submit"
            className={styles.primary}
            disabled={addConnection.isPending || migration !== 'ready'
              || !draft.label.trim() || !draft.endpoint.trim()}
          >
            <Plus size={15} aria-hidden="true" focusable={false} />
            <span>{editing ? t('Save connection') : t('Add connection')}</span>
          </button>
          {editing && <button type="button" className={styles.secondary} onClick={() => {
            setEditing(null); setLoadedPolicy(null); setDownloadOrigins(''); setTrackerOrigins(''); setDraft(EMPTY_CONNECTION); setAdapter('opds'); setFormError(null);
          }}>{t('Cancel editing')}</button>}
        </form>
      </section>

      <section className={styles.card} aria-labelledby={`${formId}-access`}>
        <h2 id={`${formId}-access`}>
          <Users size={16} aria-hidden="true" focusable={false} />
          {t('Who can use it')}
        </h2>
        {actionErrors.grant && <p className={styles.bad} role="alert">{actionErrors.grant}</p>}
        {grantsView.showError && (
          <SectionError
            message={t('The list of people could not be loaded.')}
            onRetry={() => void grants.refetch()}
            retrying={grants.isFetching}
          />
        )}
        {grantsView.body === 'idle' ? (
          <p className={styles.hint}>{t('Turn the feature on to grant access.')}</p>
        ) : grantsView.body === 'loading' ? <SpinnerCentered size={24} />
          : grantsView.body === 'empty' ? (
            <p className={styles.hint}>{t('There are no other accounts on this server yet.')}</p>
          ) : grantsView.body === 'ready' ? (
            <ul className={styles.grants} role="list">
            {grantRows.map((user) => {
              // One grants mutation serves every row; only the account being
              // changed is busy.
              const busy = isRowPending(grant.isPending, grant.variables?.id, user.id);
              return (
              <li key={user.id} className={styles.grantRow}>
                <span className={styles.grantName}>{user.name}</span>
                <label className={styles.inlineSwitch}>
                  <input
                    type="checkbox"
                    checked={user.access}
                    disabled={busy}
                    onChange={(event) => grant.mutate({
                      id: user.id,
                      access: event.target.checked,
                      // Auto-approve cannot outlive access; the server rejects
                      // that combination, so drop it in the same call.
                      auto_approve: event.target.checked && user.auto_approve,
                    })}
                  />
                  <span>{t('Can browse and request')}</span>
                </label>
                <label className={styles.inlineSwitch}>
                  <input
                    type="checkbox"
                    checked={user.auto_approve}
                    disabled={busy || !user.access}
                    onChange={(event) => grant.mutate({
                      id: user.id, access: user.access, auto_approve: event.target.checked,
                    })}
                  />
                  <span>{t('No approval needed')}</span>
                </label>
              </li>
              );
            })}
            </ul>
          ) : null}
      </section>

      {/* Shown when there is something to approve OR when we could not find
          out. The old condition was "more than zero rows", and a failed read
          produces zero rows — so a 502 on this endpoint silently removed the
          entire approval queue and the administrator was never told that
          people were waiting. An genuinely empty queue stays hidden. */}
      {enabled && (queueView.body === 'ready' || queueView.showError) && (
        <section className={styles.card} aria-labelledby={`${formId}-queue`}>
          <h2 id={`${formId}-queue`}>{t('Waiting for approval')}</h2>
          {actionErrors.queue && <p className={styles.bad} role="alert">{actionErrors.queue}</p>}
          {queueView.showError && (
            <SectionError
              message={t('The approval queue could not be loaded, so requests may be waiting unseen.')}
              onRetry={() => void queue.refetch()}
              retrying={queue.isFetching}
            />
          )}
          <ul className={styles.grants} role="list">
            {queueRows.map((job) => {
              // Approving is a decision about a person as much as a book, so
              // name the requester. The grants list is the only place their
              // display name exists, and it is a separate request that can
              // still be in flight — so "we do not know yet" is kept distinct
              // from "this account is gone". Claiming the latter about a live
              // user is a statement an administrator may act on.
              const requester = acquisitionRequesterLabel({
                ownerId: job.owner_id, names: ownerNames, resolved: grants.data !== undefined,
              });
              return (
                <li key={job.id} className={styles.grantRow}>
                  <span className={styles.queueName}>
                    {job.title || t('Untitled book')}
                    <span className={styles.queueRequester}>
                      {requester.kind === 'named'
                        ? t('Requested by {name}', { name: requester.name })
                        : requester.kind === 'removed'
                          ? t('Requested by a removed account')
                          : grants.isError
                            ? t('Could not look up who asked')
                            : t('Looking up who asked…')}
                    </span>
                  </span>
                  <button
                    type="button"
                    className={styles.primary}
                    disabled={isRowPending(approve.isPending, approve.variables, job.id) || isRowPending(reject.isPending, reject.variables, job.id)}
                    onClick={() => approve.mutate(job.id)}
                  >
                    {t('Approve')}
                  </button>
                  <button type="button" className={styles.secondary}
                    disabled={isRowPending(approve.isPending, approve.variables, job.id) || isRowPending(reject.isPending, reject.variables, job.id)}
                    onClick={() => reject.mutate(job.id)}>{t('Reject')}</button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
