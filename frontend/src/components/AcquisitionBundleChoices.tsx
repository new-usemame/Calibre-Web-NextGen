import { useEffect, useId, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../lib/api';
import {
  getAcquisitionBundleChoices,
  selectAcquisitionBundleBook,
  type AcquisitionJob,
} from '../lib/acquisition';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import styles from './AcquisitionBundleChoices.module.css';

function stateLabel(state: string, t: (message: string) => string): string {
  switch (state) {
    case 'awaiting_approval': return t('Waiting for approval');
    case 'queued': return t('Queued');
    case 'resolving': return t('Contacting the source');
    case 'downloading': return t('Downloading');
    case 'staged': return t('Downloaded, waiting to import');
    case 'publishing': return t('Handing over to the library');
    case 'importing': return t('Importing');
    case 'imported': return t('Imported into the library');
    case 'failed': return t('Failed');
    case 'rejected': return t('Request rejected');
    case 'cancelled': return t('Request cancelled');
    default: return state;
  }
}

/** Explicit choices for one completed, owned multi-book download. Sibling jobs
 *  deliberately do not mount this panel; the original row remains its anchor. */
export function AcquisitionBundleChoices({
  job,
  ownerId,
  canAcquire,
  requestsPaused,
}: {
  job: AcquisitionJob;
  ownerId?: number;
  canAcquire: boolean;
  requestsPaused: boolean;
}) {
  const t = useT();
  const announce = useAnnouncer();
  const queryClient = useQueryClient();
  const panelId = useId();
  const disclosureRef = useRef<HTMLButtonElement>(null);
  const focusAfterChoice = useRef(false);
  const isParent = job.bundle_parent_id === job.id && job.bundle_selectable === true;
  const waiting = job.state === 'awaiting_selection';
  const [open, setOpen] = useState(waiting);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    if (waiting) setOpen(true);
  }, [waiting]);

  const choices = useQuery({
    queryKey: ['acquisition', 'bundle-books', ownerId ?? null, job.id],
    queryFn: () => getAcquisitionBundleChoices(job.id),
    enabled: isParent && open,
    retry: false,
  });

  const select = useMutation({
    mutationFn: (candidate: { generation: string; candidate_id: string }) =>
      selectAcquisitionBundleBook(job.id, candidate),
    onSuccess: (selectedJob) => {
      setActionMessage(null);
      focusAfterChoice.current = true;
      // The successful response already settles the parent's waiting state.
      // Keep its disclosure reachable even if the background list refresh fails.
      if (selectedJob.id === job.id) {
        queryClient.setQueryData<{ jobs: AcquisitionJob[] }>(['acquisition', 'jobs'],
          (current) => current && { ...current, jobs: current.jobs.map(
            (row) => row.id === job.id ? { ...row, ...selectedJob } : row) });
      }
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ['acquisition', 'jobs'] });
      announce(selectedJob.state === 'awaiting_approval'
        ? t('Book requested. An administrator has to approve it.')
        : t('Book added to your activity.'));
    },
    onError: async (error) => {
      const stale = error instanceof ApiError && error.status === 409;
      const message = stale
        ? t('That book choice was out of date. Refreshing the list…')
        : error instanceof ApiError && error.detail?.code === 'acquisition_unavailable'
          ? t('Requests are paused. You can still view the available books.')
          : t('That book could not be requested. Try again.');
      setActionMessage(message);
      announce(message, { assertive: true });
      if (stale) {
        const refreshed = await choices.refetch();
        const refreshedMessage = refreshed.isError
          ? t('The choice was out of date and the available books could not be refreshed. Try again.')
          : t('That book choice was out of date. The list was refreshed; choose again.');
        setActionMessage(refreshedMessage);
        announce(refreshedMessage, { assertive: true });
      }
      void queryClient.invalidateQueries({ queryKey: ['acquisition', 'jobs'] });
    },
  });

  useEffect(() => {
    if (focusAfterChoice.current && !open && !waiting && isParent) {
      disclosureRef.current?.focus();
      focusAfterChoice.current = false;
    }
  }, [open, waiting, isParent]);

  useEffect(() => {
    if (choices.isError) announce(t('The available books could not be loaded. Try again.'), { assertive: true });
  }, [announce, choices.isError, t]);

  useEffect(() => {
    const count = choices.data?.candidates.length;
    if (choices.isSuccess && count !== undefined) {
      announce(t('Found {count} available books.', { count }));
    }
  }, [announce, choices.data?.generation, choices.data?.candidates.length, choices.isSuccess, t]);

  if (!isParent) return null;

  const pendingCandidate = select.isPending ? select.variables?.candidate_id : undefined;
  return (
    <div className={styles.bundle}>
      {waiting ? (
        <h3 className={styles.heading}>{t('Choose a book from this download')}</h3>
      ) : (
        <button
          type="button"
          className={styles.disclosure}
          ref={disclosureRef}
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? t('Hide available books') : t('Choose another book')}
        </button>
      )}
      <div id={panelId} className={styles.panel} hidden={!open}>
          {actionMessage && <p className={styles.actionMessage}>{actionMessage}</p>}
          {choices.isLoading ? (
            <p className={styles.status} role="status">{t('Loading available books…')}</p>
          ) : choices.isError ? (
            <div className={styles.error}>
              <p>{t('The available books could not be loaded. Try again.')}</p>
              <button type="button" className={styles.secondary}
                onClick={() => void choices.refetch()} disabled={choices.isFetching}>
                {t('Try again')}
              </button>
            </div>
          ) : choices.data?.candidates.length ? (
            <ul className={styles.candidates} role="list">
              {choices.data.candidates.map((candidate) => {
                const alreadyRequested = !!candidate.job_id || !!candidate.state;
                const buttonLabel = canAcquire ? t('Import this book') : t('Request this book');
                const accessibleLabel = canAcquire
                  ? t('Import this book: {filename}', { filename: candidate.name })
                  : t('Request this book: {filename}', { filename: candidate.name });
                return (
                  <li className={styles.candidate} key={candidate.id}>
                    <div className={styles.details}>
                      <p className={styles.filename} title={candidate.name}>{candidate.name}</p>
                      <p className={styles.metadata}>
                        <span>{candidate.format}</span>
                        <span>{t('{size} bytes', { size: candidate.size.toLocaleString() })}</span>
                      </p>
                    </div>
                    {alreadyRequested ? (
                      <p className={styles.requested}>
                        <strong>{t('Requested')}</strong>
                        {candidate.state && <span>{stateLabel(candidate.state, t)}</span>}
                      </p>
                    ) : (
                      <button
                        type="button"
                        className={styles.primary}
                        aria-label={accessibleLabel}
                        disabled={requestsPaused || choices.isFetching || pendingCandidate === candidate.id}
                        onClick={() => choices.data && select.mutate({
                          generation: choices.data.generation,
                          candidate_id: candidate.id,
                        })}
                      >
                        {buttonLabel}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className={styles.status}>{t('No books are available in this download.')}</p>
          )}
      </div>
    </div>
  );
}
