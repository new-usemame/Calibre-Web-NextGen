import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link } from 'wouter';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, BookOpen, ChevronRight, Download, FolderOpen, RefreshCw, Search, Send, X,
} from 'lucide-react';
import {
  ACQUISITION_CANCELLABLE_STATES,
  cancelAcquisitionJob,
  createAcquisitionJob,
  getAcquisitionBootstrap,
  getAcquisitionCatalog,
  getAcquisitionJobs,
  isAcquisitionPending,
  retryAcquisitionJob,
  type AcquisitionCatalog,
  type AcquisitionJob,
  type AcquisitionNavigation,
  type AcquisitionPublication,
} from '../lib/acquisition';
import { useRuntimeReasonText } from '../lib/acquisitionCopy';
import { acquisitionSectionView, hasVisibleReceipt, isRowPending } from '../lib/acquisitionViewState';
import { ApiError } from '../lib/api';
import { useMe } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import styles from './FindBooks.module.css';

/** One step of the browse trail. `selection` is the server's opaque cursor;
 *  the root step has none, meaning "the connection's configured endpoint". */
interface Step {
  title: string;
  selection?: string;
  query?: string;
}

function useJobStateText(): (job: AcquisitionJob) => { label: string; tone: 'active' | 'ok' | 'bad' | 'muted' } {
  const t = useT();
  return useCallback((job: AcquisitionJob) => {
    if (job.cancel_requested && isAcquisitionPending(job.state)) {
      return { label: t('Cancelling…'), tone: 'muted' as const };
    }
    switch (job.state) {
      case 'awaiting_approval': return { label: t('Waiting for approval'), tone: 'muted' as const };
      case 'queued': return { label: t('Queued'), tone: 'active' as const };
      case 'resolving': return { label: t('Contacting the source'), tone: 'active' as const };
      case 'downloading': return { label: t('Downloading'), tone: 'active' as const };
      // Bytes are on disk but no book exists yet. Saying "done" here is the
      // exact confusion the import receipt exists to prevent.
      case 'staged': return { label: t('Downloaded, waiting to import'), tone: 'active' as const };
      case 'publishing': return { label: t('Handing over to the library'), tone: 'active' as const };
      case 'importing': return { label: t('Importing'), tone: 'active' as const };
      // The import succeeded either way. "In your library" is only true when
      // there is something this account can open; without a visible receipt id
      // it would promise a book the user cannot reach.
      case 'imported': return hasVisibleReceipt(job.result?.book_ids)
        ? { label: t('In your library'), tone: 'ok' as const }
        : { label: t('Imported into the library'), tone: 'ok' as const };
      case 'failed': return { label: t('Failed'), tone: 'bad' as const };
      case 'cancelled': return { label: t('Request cancelled'), tone: 'muted' as const };
      default: return { label: job.state, tone: 'muted' as const };
    }
  }, [t]);
}

function errorCode(error: unknown): string | undefined {
  if (error instanceof ApiError && error.detail && typeof error.detail.code === 'string') {
    return error.detail.code;
  }
  return undefined;
}

export function FindBooks() {
  const t = useT();
  const announce = useAnnouncer();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const reasonText = useRuntimeReasonText();
  const jobStateText = useJobStateText();
  const searchInputId = useId();

  const [connectionId, setConnectionId] = useState<string | null>(null);
  const [trail, setTrail] = useState<Step[]>([]);
  const [draftQuery, setDraftQuery] = useState('');
  const [addToMyLibrary, setAddToMyLibrary] = useState(true);
  const [requestError, setRequestError] = useState<string | null>(null);
  // Cancel and retry act on the activity list, which is far down the page from
  // the catalog, so their failures get their own line next to the jobs.
  const [activityError, setActivityError] = useState<string | null>(null);

  // One idempotency key per offer for the lifetime of the page. A double-click,
  // or a retry after an ambiguous 5xx, therefore resolves to the SAME job on the
  // server instead of queueing the book twice.
  const requestKeys = useRef(new Map<string, string>());
  const keyFor = (offerId: string) => {
    const existing = requestKeys.current.get(offerId);
    if (existing) return existing;
    const fresh = (globalThis.crypto?.randomUUID?.()
      ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`);
    requestKeys.current.set(offerId, fresh);
    return fresh;
  };

  const bootstrap = useQuery({
    queryKey: ['acquisition', 'bootstrap'],
    queryFn: getAcquisitionBootstrap,
  });

  const connections = useMemo(
    () => bootstrap.data?.connections.filter((c) => c.enabled) ?? [],
    [bootstrap.data],
  );

  // Settle on a connection once they load, and recover if the chosen one is
  // withdrawn by an administrator while the page is open.
  useEffect(() => {
    if (!connections.length) {
      if (connectionId !== null) setConnectionId(null);
      return;
    }
    if (!connectionId || !connections.some((c) => c.id === connectionId)) {
      setConnectionId(connections[0].id);
      setTrail([]);
    }
  }, [connections, connectionId]);

  const current = trail.length ? trail[trail.length - 1] : undefined;

  const catalog = useQuery<AcquisitionCatalog>({
    queryKey: ['acquisition', 'catalog', connectionId, current?.selection ?? null, current?.query ?? null],
    queryFn: () => getAcquisitionCatalog(connectionId as string, {
      selection: current?.selection,
      query: current?.query,
    }),
    enabled: !!connectionId,
    retry: false,
  });

  const jobs = useQuery({
    queryKey: ['acquisition', 'jobs'],
    queryFn: getAcquisitionJobs,
    // Only poll while the server still owes an answer, so an idle page is
    // quiet. "Owes an answer" includes `awaiting_approval`: the administrator
    // may approve at any moment and the user must see it without reloading.
    refetchInterval: (query) => {
      const rows = query.state.data?.jobs ?? [];
      return rows.some((job) => isAcquisitionPending(job.state)) ? 3000 : false;
    },
  });

  const invalidateJobs = () => { void queryClient.invalidateQueries({ queryKey: ['acquisition', 'jobs'] }); };

  const request = useMutation({
    mutationFn: (variables: { offerId: string; format: string }) => createAcquisitionJob({
      connection_id: connectionId as string,
      offer_id: variables.offerId,
      idempotency_key: keyFor(variables.offerId),
      add_to_my_library: addToMyLibrary,
    }),
    onSuccess: (job) => {
      setRequestError(null);
      invalidateJobs();
      announce(job.state === 'awaiting_approval'
        ? t('Requested. An administrator has to approve it.')
        : t('Added to your activity.'));
    },
    onError: (error) => {
      const code = errorCode(error);
      setRequestError(
        code === 'selections_full'
          ? t('Too many open selections. Wait a little, then try again.')
          : code === 'acquisition_unavailable'
            ? t('Acquisition is not ready right now. Ask an administrator to check its status.')
            : code === 'not_found'
              ? t('That choice expired. Refresh the page and pick the file again.')
              : t('The request could not be made.'),
      );
    },
  });

  const cancel = useMutation({
    mutationFn: cancelAcquisitionJob,
    onSuccess: () => { setActivityError(null); invalidateJobs(); announce(t('Cancelling…')); },
    onError: (error) => {
      // A 409 means the job moved on while the button was on screen — the
      // refetch below replaces the row with its real state.
      setActivityError(errorCode(error) === 'conflict'
        ? t('That request has already moved on and can no longer be stopped.')
        : t('The request could not be stopped.'));
      invalidateJobs();
    },
  });
  const retry = useMutation({
    mutationFn: retryAcquisitionJob,
    onSuccess: () => { setActivityError(null); invalidateJobs(); announce(t('Retrying.')); },
    onError: (error) => {
      const code = errorCode(error);
      setActivityError(
        code === 'forbidden'
          ? t('This request needs an administrator to approve it again.')
          : code === 'conflict'
            ? t('That request is no longer in a state that can be retried.')
            : t('The request could not be retried.'),
      );
      invalidateJobs();
    },
  });

  const runtime = bootstrap.data?.runtime;
  const canAcquire = !!bootstrap.data?.can_acquire;
  const personalLibrary = me?.library_mode === 'personal_library';
  const searchCapability = catalog.data?.searches?.[0];

  const jobRows = jobs.data?.jobs ?? [];
  // "You have asked for nothing" and "we could not find out what you asked
  // for" are different sentences, and only the first one is reassuring.
  const jobsView = acquisitionSectionView({
    isLoading: jobs.isLoading,
    isError: jobs.isError,
    hasData: jobs.isSuccess,
    isEmpty: jobRows.length === 0,
  });

  const openSelection = (nav: AcquisitionNavigation) => {
    setTrail((steps) => [...steps, { title: nav.title || t('Catalog'), selection: nav.selection }]);
  };

  const runSearch = (event: React.FormEvent) => {
    event.preventDefault();
    const term = draftQuery.trim();
    if (!term || !searchCapability) return;
    setTrail((steps) => [...steps, {
      title: t('Search: {query}', { query: term }),
      selection: searchCapability.selection,
      query: term,
    }]);
  };

  if (bootstrap.isLoading) return <SpinnerCentered />;

  if (bootstrap.isError) {
    return (
      <div className={styles.container}>
        <EmptyState
          icon={AlertTriangle}
          title={t('Find books is unavailable')}
          message={t('This account cannot use book sources, or the feature is switched off.')}
        />
      </div>
    );
  }

  return (
    <div className={styles.container}>
      <header className={styles.heading}>
        <BookOpen aria-hidden="true" focusable={false} />
        <h1>{t('Find books')}</h1>
      </header>
      <p className={styles.lede}>
        {t('Browse the catalogs your administrator has added. A book you pick is imported into the library here — it is not just downloaded to your device.')}
      </p>

      {runtime && !runtime.available && (
        <div className={styles.notice} role="status">
          <AlertTriangle size={16} aria-hidden="true" focusable={false} />
          <div>
            <p className={styles.noticeTitle}>{t('Requests are paused')}</p>
            {/* role="list" is explicit: the global reset sets list-style:none,
                which makes Safari/VoiceOver stop announcing this as a list and
                read the reasons as loose sentences with no count. */}
            <ul className={styles.reasons} role="list">
              {runtime.reasons.map((reason) => <li key={reason}>{reasonText(reason)}</li>)}
            </ul>
          </div>
        </div>
      )}

      {!connections.length ? (
        <EmptyState
          icon={FolderOpen}
          title={t('No catalogs yet')}
          message={t('An administrator has not added a book source, or none is switched on.')}
        />
      ) : (
        <>
          <div className={styles.controls}>
            {connections.length > 1 && (
              <label className={styles.field}>
                <span>{t('Catalog')}</span>
                <select
                  value={connectionId ?? ''}
                  onChange={(event) => { setConnectionId(event.target.value); setTrail([]); }}
                >
                  {connections.map((connection) => (
                    <option key={connection.id} value={connection.id}>{connection.label}</option>
                  ))}
                </select>
              </label>
            )}

            {searchCapability ? (
              <form className={styles.search} onSubmit={runSearch} role="search">
                <label className={styles.srOnly} htmlFor={searchInputId}>{t('Search this catalog')}</label>
                <input
                  id={searchInputId}
                  type="search"
                  value={draftQuery}
                  onChange={(event) => setDraftQuery(event.target.value)}
                  placeholder={t('Search this catalog')}
                  maxLength={500}
                />
                <button type="submit" disabled={!draftQuery.trim()}>
                  <Search size={16} aria-hidden="true" focusable={false} />
                  <span>{t('Search')}</span>
                </button>
              </form>
            ) : (
              catalog.data && <p className={styles.muted}>{t('This catalog does not offer search.')}</p>
            )}
          </div>

          {trail.length > 0 && (
            <nav className={styles.crumbs} aria-label={t('Catalog trail')}>
              <button type="button" onClick={() => setTrail([])}>{t('Top of catalog')}</button>
              {trail.map((step, index) => (
                <span key={`${step.selection ?? 'root'}-${index}`} className={styles.crumb}>
                  <ChevronRight size={14} aria-hidden="true" focusable={false} />
                  {index === trail.length - 1 ? (
                    <span aria-current="page">{step.title}</span>
                  ) : (
                    <button type="button" onClick={() => setTrail((steps) => steps.slice(0, index + 1))}>
                      {step.title}
                    </button>
                  )}
                </span>
              ))}
            </nav>
          )}

          {personalLibrary && (
            <label className={styles.checkbox}>
              <input
                type="checkbox"
                checked={addToMyLibrary}
                onChange={(event) => setAddToMyLibrary(event.target.checked)}
              />
              <span>{t('Also add the book to My Library')}</span>
            </label>
          )}

          {requestError && <p className={styles.error} role="alert">{requestError}</p>}

          {catalog.isLoading && <SpinnerCentered />}

          {catalog.isError && (
            // role="alert": browsing is a keyboard-and-listening activity as
            // much as a visual one, and a page that silently swaps its results
            // for a failure leaves a screen-reader user waiting for a list
            // that is never coming.
            <div role="alert">
              <EmptyState
                icon={AlertTriangle}
                title={t('The catalog could not be read')}
                message={errorCode(catalog.error) === 'not_found'
                  ? t('That page expired. Go back to the top and try again.')
                  : t('The source did not answer with a catalog we can read.')}
              >
                <button type="button" className={styles.secondary} onClick={() => setTrail([])}>
                  {t('Back to top')}
                </button>
                <button
                  type="button"
                  className={styles.secondary}
                  onClick={() => void catalog.refetch()}
                  disabled={catalog.isFetching}
                >
                  <RefreshCw size={14} aria-hidden="true" focusable={false} />
                  <span>{t('Try again')}</span>
                </button>
              </EmptyState>
            </div>
          )}

          {catalog.data && (
            <CatalogView
              catalog={catalog.data}
              canAcquire={canAcquire}
              requestsPaused={!runtime?.available}
              pendingOffer={request.isPending ? request.variables?.offerId : undefined}
              onOpen={openSelection}
              onRequest={(offerId, format) => request.mutate({ offerId, format })}
            />
          )}
        </>
      )}

      <section className={styles.activity} aria-labelledby="acquisition-activity">
        <h2 id="acquisition-activity">{t('Your requests')}</h2>
        {activityError && <p className={styles.error} role="alert">{activityError}</p>}
        {/* A failed read used to arrive here as "Nothing requested yet.", so a
            user whose session had expired, or who hit a 502, was told their
            requests did not exist. They ask again; the idempotency key is
            per-page-load, so asking again after a reload really does queue a
            second job. */}
        {jobsView.showError && (
          <div className={styles.sectionError} role="alert">
            <p>{t('Your requests could not be loaded. They have not been lost.')}</p>
            <button
              type="button"
              className={styles.secondary}
              onClick={() => void jobs.refetch()}
              disabled={jobs.isFetching}
            >
              <RefreshCw size={14} aria-hidden="true" focusable={false} />
              <span>{t('Try again')}</span>
            </button>
          </div>
        )}
        {jobsView.body === 'loading' ? <SpinnerCentered size={24} />
          : jobsView.body === 'empty' ? (
          <p className={styles.muted}>{t('Nothing requested yet.')}</p>
        ) : jobsView.body === 'ready' ? (
          <ul className={styles.jobs} role="list">
            {jobRows.map((job) => {
              const state = jobStateText(job);
              const finished = job.state === 'imported';
              // Only where the server will actually accept it, so the button
              // never produces a 409 the user did not ask for.
              const cancellable = ACQUISITION_CANCELLABLE_STATES.has(job.state);
              return (
                <li key={job.id} className={styles.job} data-state={job.state}>
                  <div className={styles.jobMain}>
                    <p className={styles.jobTitle}>{job.title || t('Untitled book')}</p>
                    <p className={styles.jobMeta}>
                      <span className={styles.pill} data-tone={state.tone}>{state.label}</span>
                      {job.state === 'failed' && job.error_code && (
                        <span className={styles.muted}>
                          {job.error_code === 'source_busy'
                            ? t('The source asked us to wait.')
                            : t('The transfer did not complete.')}
                        </span>
                      )}
                      {finished && job.result?.disposition === 'existing_retained' && (
                        <span className={styles.muted}>{t('A copy was already in the library; it was kept.')}</span>
                      )}
                      {finished && job.result?.disposition === 'already_imported' && (
                        <span className={styles.muted}>{t('This book was already imported.')}</span>
                      )}
                    </p>
                  </div>
                  <div className={styles.jobActions}>
                    {/* Only a receipt produces a link, and only for books this
                        account is actually allowed to see. */}
                    {job.result?.book_ids.map((id) => (
                      <Link key={id} href={`/book/${id}`} className={styles.primaryLink}>
                        {t('Open book')}
                      </Link>
                    ))}
                    {job.state === 'failed' && (
                      <button
                        type="button"
                        className={styles.secondary}
                        disabled={isRowPending(retry.isPending, retry.variables, job.id)}
                        onClick={() => retry.mutate(job.id)}
                      >
                        {t('Try again')}
                      </button>
                    )}
                    {cancellable && !job.cancel_requested && (
                      <button
                        type="button"
                        className={styles.secondary}
                        disabled={isRowPending(cancel.isPending, cancel.variables, job.id)}
                        onClick={() => cancel.mutate(job.id)}
                      >
                        <X size={14} aria-hidden="true" focusable={false} />
                        <span>{t('Cancel')}</span>
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>
    </div>
  );
}

function CatalogView({ catalog, canAcquire, requestsPaused, pendingOffer, onOpen, onRequest }: {
  catalog: AcquisitionCatalog;
  canAcquire: boolean;
  requestsPaused: boolean;
  pendingOffer?: string;
  onOpen: (nav: AcquisitionNavigation) => void;
  onRequest: (offerId: string, format: string) => void;
}) {
  const t = useT();
  const sections = [
    { title: '', publications: catalog.publications, navigation: catalog.navigation },
    ...catalog.groups,
  ].filter((section) => section.publications.length || section.navigation.length);

  if (!sections.length) {
    return <EmptyState icon={FolderOpen} message={t('This catalog page is empty.')} />;
  }

  return (
    <>
      {catalog.facets.map((facet) => (
        <section key={facet.title} className={styles.facet}>
          <h2>{facet.title || t('Filter')}</h2>
          <ul className={styles.navList} role="list">
            {facet.navigation.map((nav) => (
              <li key={nav.selection}>
                <button type="button" className={styles.navChip} onClick={() => onOpen(nav)}>{nav.title}</button>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {sections.map((section, index) => (
        <section key={section.title || `section-${index}`} className={styles.section}>
          {section.title && <h2>{section.title}</h2>}
          {section.navigation.length > 0 && (
            <ul className={styles.navList} role="list">
              {section.navigation.map((nav) => (
                <li key={nav.selection}>
                  <button type="button" className={styles.navChip} onClick={() => onOpen(nav)}>
                    {nav.title || t('Browse')}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {section.publications.length > 0 && (
            <ul className={styles.publications} role="list">
              {section.publications.map((publication) => (
                <PublicationCard
                  key={publication.identity}
                  publication={publication}
                  canAcquire={canAcquire}
                  requestsPaused={requestsPaused}
                  pendingOffer={pendingOffer}
                  onOpen={onOpen}
                  onRequest={onRequest}
                />
              ))}
            </ul>
          )}
        </section>
      ))}

      {catalog.pagination.length > 0 && (
        <nav className={styles.pagination} aria-label={t('Catalog pages')}>
          {catalog.pagination.map((nav) => (
            <button key={nav.selection} type="button" className={styles.secondary} onClick={() => onOpen(nav)}>
              {nav.title || nav.relations.join(' ')}
            </button>
          ))}
        </nav>
      )}
    </>
  );
}

function PublicationCard({ publication, canAcquire, requestsPaused, pendingOffer, onOpen, onRequest }: {
  publication: AcquisitionPublication;
  canAcquire: boolean;
  requestsPaused: boolean;
  pendingOffer?: string;
  onOpen: (nav: AcquisitionNavigation) => void;
  onRequest: (offerId: string, format: string) => void;
}) {
  const t = useT();
  const authors = publication.authors.join(', ');
  return (
    <li className={styles.publication}>
      <h3>{publication.title}</h3>
      {authors && <p className={styles.authors}>{authors}</p>}
      {publication.languages.length > 0 && (
        <p className={styles.muted}>{publication.languages.join(', ')}</p>
      )}
      {publication.description && <p className={styles.description}>{publication.description}</p>}

      {publication.offers.length > 0 ? (
        <div className={styles.offers}>
          {publication.offers.map((offer) => (
            <button
              key={offer.identity}
              type="button"
              className={styles.primary}
              disabled={requestsPaused || pendingOffer === offer.offer_id}
              onClick={() => onRequest(offer.offer_id, offer.format)}
            >
              {canAcquire
                ? <Download size={15} aria-hidden="true" focusable={false} />
                : <Send size={15} aria-hidden="true" focusable={false} />}
              <span>
                {canAcquire
                  ? t('Download {format}', { format: offer.format })
                  : t('Request {format}', { format: offer.format })}
              </span>
            </button>
          ))}
        </div>
      ) : (
        // Buy / borrow / preview / templated links are deliberately not offered
        // here: they are not a complete file this server can import.
        <p className={styles.muted}>{t('No EPUB or PDF available from this catalog.')}</p>
      )}

      {publication.navigation.length > 0 && (
        <ul className={styles.navList} role="list">
          {publication.navigation.map((nav) => (
            <li key={nav.selection}>
              <button type="button" className={styles.navChip} onClick={() => onOpen(nav)}>
                {nav.title || t('More')}
              </button>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
