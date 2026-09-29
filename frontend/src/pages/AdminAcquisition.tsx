import { useId, useState } from 'react';
import { Link } from 'wouter';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ChevronLeft, CheckCircle2, Globe, Plus, Users } from 'lucide-react';
import {
  approveAcquisitionJob,
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
import { ApiError } from '../lib/api';
import { useT } from '../lib/i18n';
import { useMe } from '../lib/queries';
import { useAnnouncer } from '../lib/a11y/announcer';
import { SpinnerCentered } from '../components/Spinner';
import styles from './AdminAcquisition.module.css';

function errorCode(error: unknown): string | undefined {
  if (error instanceof ApiError && error.detail && typeof error.detail.code === 'string') {
    return error.detail.code;
  }
  return undefined;
}

const EMPTY_CONNECTION: AcquisitionConnectionInput & { label: string } = {
  label: '', endpoint: '', auth_kind: 'none', username: '', secret: '',
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

  const [draft, setDraft] = useState(EMPTY_CONNECTION);
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
    mutationFn: () => createAcquisitionConnection(draft.label.trim(), {
      endpoint: draft.endpoint.trim(),
      auth_kind: draft.auth_kind,
      username: draft.auth_kind === 'basic' ? draft.username : '',
      secret: draft.auth_kind === 'none' ? '' : draft.secret,
    }),
    onSuccess: () => {
      setDraft(EMPTY_CONNECTION);
      setFormError(null);
      refresh('connections');
      announce(t('Catalog added. Test it, then switch it on.'));
    },
    onError: (error) => {
      setFormError(errorCode(error) === 'needs_review'
        ? t('Legacy acquisition permissions need review before a catalog can be added.')
        : t('Check the address and the sign-in details.'));
    },
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
          ? t('The source did not answer with a catalog we can read.')
          : t('The connection test failed.') },
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

  const runtime = settings.data?.runtime;
  const rows = connections.data?.connections ?? [];
  const ownerNames = new Map((grants.data?.users ?? []).map((user) => [user.id, user.name]));

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
        {t('Let people request books from an OPDS catalog. A requested book is imported into this library through the normal ingest path, credited to the account that asked for it.')}
      </p>

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

        {runtime && (
          <p className={styles.status} data-ok={runtime.available}>
            {runtime.available
              ? t('Ready to run requests.')
              : t('Requests cannot run yet: {reasons}', { reasons: runtime.reasons.join(', ') })}
          </p>
        )}
      </section>

      <section className={styles.card} aria-labelledby={`${formId}-connections`}>
        <h2 id={`${formId}-connections`}>{t('Catalogs')}</h2>

        {actionErrors.connection && <p className={styles.bad} role="alert">{actionErrors.connection}</p>}

        {connections.isLoading ? <SpinnerCentered size={24} /> : rows.length === 0 ? (
          <p className={styles.hint}>{t('No catalogs yet.')}</p>
        ) : (
          <ul className={styles.connections} role="list">
            {rows.map((connection) => {
              const result = probes[connection.id];
              return (
                <li key={connection.id} className={styles.connection}>
                  <div className={styles.connectionMain}>
                    <p className={styles.connectionLabel}>{connection.label}</p>
                    {result && 'error' in result ? (
                      <p className={styles.bad}>{result.error}</p>
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
                    <button
                      type="button"
                      className={styles.secondary}
                      disabled={probe.isPending}
                      onClick={() => probe.mutate(connection.id)}
                    >
                      {t('Test connection')}
                    </button>
                    <label className={styles.inlineSwitch}>
                      <input
                        type="checkbox"
                        checked={connection.enabled}
                        disabled={toggleConnection.isPending || migration !== 'ready'}
                        onChange={(event) => toggleConnection.mutate({ id: connection.id, enabled: event.target.checked })}
                      />
                      <span>{t('Available to users')}</span>
                    </label>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <form
          className={styles.form}
          onSubmit={(event) => { event.preventDefault(); addConnection.mutate(); }}
        >
          <h3>{t('Add a catalog')}</h3>
          <div className={styles.fields}>
            <label className={styles.field}>
              <span>{t('Name')}</span>
              <input
                value={draft.label}
                maxLength={200}
                required
                onChange={(event) => setDraft({ ...draft, label: event.target.value })}
              />
            </label>
            <label className={styles.field}>
              <span>{t('Catalog address')}</span>
              <input
                type="url"
                inputMode="url"
                value={draft.endpoint}
                required
                placeholder="https://…"
                onChange={(event) => setDraft({ ...draft, endpoint: event.target.value })}
              />
            </label>
            <label className={styles.field}>
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
                <option value="bearer">{t('Token')}</option>
              </select>
            </label>
            {draft.auth_kind === 'basic' && (
              <label className={styles.field}>
                <span>{t('Username')}</span>
                <input
                  value={draft.username}
                  autoComplete="off"
                  onChange={(event) => setDraft({ ...draft, username: event.target.value })}
                />
              </label>
            )}
            {draft.auth_kind !== 'none' && (
              <label className={styles.field}>
                <span>{draft.auth_kind === 'basic' ? t('Password') : t('Token')}</span>
                <input
                  type="password"
                  value={draft.secret}
                  autoComplete="new-password"
                  onChange={(event) => setDraft({ ...draft, secret: event.target.value })}
                />
              </label>
            )}
          </div>
          {formError && <p className={styles.bad} role="alert">{formError}</p>}
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
            <span>{t('Add catalog')}</span>
          </button>
        </form>
      </section>

      <section className={styles.card} aria-labelledby={`${formId}-access`}>
        <h2 id={`${formId}-access`}>
          <Users size={16} aria-hidden="true" focusable={false} />
          {t('Who can use it')}
        </h2>
        {!enabled ? (
          <p className={styles.hint}>{t('Turn the feature on to grant access.')}</p>
        ) : grants.isLoading ? <SpinnerCentered size={24} /> : (
          <>
            {actionErrors.grant && <p className={styles.bad} role="alert">{actionErrors.grant}</p>}
            <ul className={styles.grants} role="list">
            {(grants.data?.users ?? []).map((user) => (
              <li key={user.id} className={styles.grantRow}>
                <span className={styles.grantName}>{user.name}</span>
                <label className={styles.inlineSwitch}>
                  <input
                    type="checkbox"
                    checked={user.access}
                    disabled={grant.isPending}
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
                    disabled={grant.isPending || !user.access}
                    onChange={(event) => grant.mutate({
                      id: user.id, access: user.access, auto_approve: event.target.checked,
                    })}
                  />
                  <span>{t('No approval needed')}</span>
                </label>
              </li>
            ))}
            </ul>
          </>
        )}
      </section>

      {enabled && (queue.data?.jobs.length ?? 0) > 0 && (
        <section className={styles.card} aria-labelledby={`${formId}-queue`}>
          <h2 id={`${formId}-queue`}>{t('Waiting for approval')}</h2>
          {actionErrors.queue && <p className={styles.bad} role="alert">{actionErrors.queue}</p>}
          <ul className={styles.grants} role="list">
            {(queue.data?.jobs ?? []).map((job) => {
              // Approving is a decision about a person as much as a book, so
              // name the requester. The grants list is the only place their
              // display name exists; an account removed since asking falls
              // back to the id rather than silently reading as nobody.
              const requester = ownerNames.get(job.owner_id);
              return (
                <li key={job.id} className={styles.grantRow}>
                  <span className={styles.queueName}>
                    {job.title || t('Untitled book')}
                    <span className={styles.queueRequester}>
                      {requester
                        ? t('Requested by {name}', { name: requester })
                        : t('Requested by a removed account')}
                    </span>
                  </span>
                  <button
                    type="button"
                    className={styles.primary}
                    disabled={approve.isPending}
                    onClick={() => approve.mutate(job.id)}
                  >
                    {t('Approve')}
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
