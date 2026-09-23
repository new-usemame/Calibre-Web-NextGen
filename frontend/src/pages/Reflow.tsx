import { useEffect, useState } from 'react';
import { Link } from 'wouter';
import {
  ChevronLeft, Sparkles, AlertTriangle, KeyRound, Download, Check, X, Loader2,
} from 'lucide-react';
import { useBook } from '../lib/queries';
import {
  useReflowEstimate, useReflowJobs, useStartReflow, useCancelReflow,
  heldUsd, holdRequiringAcknowledgment, jobCounts, ledgerUsd,
  selectedReview, preparationActive, preparationNote, usePrepareReflow, useReflowPreparation, useCancelPreparation, usd,
  type ReflowJob, type ReflowMode, type ReviewMode,
} from '../lib/reflow';
import { Button } from '../components/Button';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { ApiError, resourceUrl } from '../lib/api';
import { useT, type TFunction } from '../lib/i18n';
import styles from './Reflow.module.css';
import { useAnnouncer } from '../lib/a11y/announcer';

/** The assessment, in the reader's language.
 *
 *  The server writes its own copy of these sentences into the EPUB's report page,
 *  which travels inside the book and is not in the reader's UI language. This one
 *  is for the person deciding whether to spend money, so it is translated. */
function verdictSentence(verdict: string, t: TFunction): string {
  switch (verdict) {
    case 'BORN_DIGITAL':
      return t('This PDF has a native text layer. Extraction and punctuation may still differ from the printed page; original-source evidence remains available.');
    case 'OCR_LAYER':
      return t('Every page is a picture of the page with OCR text behind it. The words are readable, but the structure has to be rebuilt.');
    case 'THIN_TEXT':
      return t('There is very little text on each page. This is usually a sparse scan, or two printed pages photographed as one.');
    case 'NO_TEXT_LAYER':
      return t('There is no usable text in this PDF, only page images. Text recovery settings determine how those pages are read; review a sample for recognition errors.');
    case 'GARBAGE_TEXT':
      return t('This PDF has a text layer, but it is not readable words. It has to be treated as if there were no text at all.');
    default:
      return t('This PDF has not been assessed.');
  }
}

const STATUS_LABEL = (status: string, t: TFunction): string => ({
  waiting: t('Queued'),
  running: t('Converting'),
  done: t('Finished'),
  capped: t('Stopped at the cap'),
  incomplete: t('The model service stopped answering'),
  billing_unknown: t('A charge could not be confirmed'),
  interrupted: t('Interrupted by a restart'),
  limited: t('Source conversion ready; AI review limited'),
  failed: t('Failed'),
  // Not the classic task list's "Cancelled": a conversion has two ways of
  // stopping early and the bill is different, so each says which one it was.
  cancelled: t('Stopped before it finished'),
}[status] ?? status);

export function Reflow({ id }: { id: string }) {
  const t = useT();
  const announce = useAnnouncer();
  const { data: book } = useBook(id);
  const estimateQ = useReflowEstimate(id);
  const est = estimateQ.data;

  const [mode, setMode] = useState<ReflowMode>('sample');
  const [reviewMode, setReviewMode] = useState<ReviewMode>('deterministic');
  const [preparation, setPreparation] = useState<{ id: string; key: string } | null>(null);
  const [samplePages, setSamplePages] = useState<number>(20);
  const [cap, setCap] = useState<string>('');
  const [capTouched, setCapTouched] = useState(false);
  const [consent, setConsent] = useState(false);
  const [replaceEpub, setReplaceEpub] = useState(false);
  const [recovery, setRecovery] = useState<'auto' | 'textless' | 'off'>('auto');
  const [ocrLang, setOcrLang] = useState<string>('eng');
  const [error, setError] = useState<string | null>(null);

  const start = useStartReflow(id);
  const cancel = useCancelReflow();
  const prepare = usePrepareReflow(id);
  const cancelPreparation = useCancelPreparation(id);
  const sourceKey = JSON.stringify([id, est?.source_sha256, recovery, ocrLang]);
  const preparationId = preparation?.key === sourceKey ? preparation.id : undefined;
  const preparationQ = useReflowPreparation(id, preparationId);
  const preparing = preparationActive(preparationQ.data?.status) || prepare.isPending;
  const quote = preparationQ.data?.status === 'ready' && preparationQ.data.quote?.source_sha256 === est?.source_sha256
    ? preparationQ.data.quote : undefined;
  const paid = reviewMode === 'source_verified';
  const note = preparationNote(preparationQ.data, prepare.isPending, !!preparationQ.error, !!quote);
  const selected = selectedReview(quote, mode, samplePages);
  const needed = selected.bound;
  const capNumber = Number(cap);
  const authorised = Number.isFinite(capNumber) && capNumber > 0 ? capNumber : 0;
  const paidAvailable = !!est?.configured && !!est.review.quality_released;

  // Stale preparation can never supply consent. Cancel owned obsolete local work;
  // an in-flight HTTP reply is also bound to its original source/options key.
  useEffect(() => {
    if (preparation && preparation.key !== sourceKey) {
      cancelPreparation.mutate(preparation.id);
      setPreparation(null);
    }
  }, [sourceKey, preparation]);


  // Source assessment supplies the initial sample length and recognition language.
  useEffect(() => {
    if (!est) return;
    setSamplePages(est.sample_pages_default);
    setMode(est.sample_suggested ? 'sample' : 'full');
    setOcrLang(est.recovery.language);
  }, [est]);

  const rec = est?.recovery;
  const needsRecovery = !!rec && rec.ocr_candidates > 0;
  // A chosen recovery with no engine is refused by the server; say so here,
  // before the consent box is even offered.
  const engineBlocks = needsRecovery && recovery !== 'off' && !rec?.engine_available;
  const facsimile = needsRecovery && recovery === 'off';

  useEffect(() => {
    if (!est || capTouched) return;
    setCap(Math.min(est.hard_cap_usd, Math.max(.01, Math.ceil((needed - Number.EPSILON) * 100) / 100)).toFixed(2));
  }, [est, needed, capTouched]);

  useEffect(() => { setConsent(false); },
    [authorised, needed, mode, reviewMode, samplePages, sourceKey, quote?.identity, replaceEpub]);

  const jobsQ = useReflowJobs(id);
  const active = jobsQ.data?.active ?? [];
  const running = active.length > 0;
  // The server lists jobs newest first (ledger.read_summaries).
  const latest = jobsQ.data?.items?.[0] ?? null;
  useEffect(() => { if (latest && !running) announce(STATUS_LABEL(latest.status, t)); },
    [latest?.job_id, latest?.status, running, announce, t]);

  // An earlier job may still hold an unconfirmed charge. The next consent has
  // to name it and the start stays blocked until the reader acknowledges it --
  // the hold is not part of this job's cap, and starting again neither settles
  // nor erases it. It re-arms if the held amount ever changes.
  const hold = holdRequiringAcknowledgment(jobsQ.data?.items ?? []);
  const [holdAcknowledged, setHoldAcknowledged] = useState(false);
  useEffect(() => setHoldAcknowledged(false), [hold]);

  const capValid = Number.isFinite(capNumber) && capNumber > 0
    && (!est || capNumber <= est.hard_cap_usd + 1e-9);
  const capTooLow = paid && !!quote && capValid && capNumber + 1e-12 < needed;
  const blocked = running || !consent || engineBlocks || start.isPending
    || (mode === 'full' && !!est?.existing_epub && !replaceEpub)
    || (paid && (!paidAvailable || !quote || !capValid || (hold > 0 && !holdAcknowledged)));

  const onStart = () => {
    if (!est || blocked) return;
    setError(null);
    start.mutate({
      mode, review_mode: reviewMode, sample_pages: samplePages, cost_cap_usd: paid ? capNumber : 0,
      consent_contract: est.consent_contract, source_sha256: est.source_sha256,
      preparation_id: paid ? preparationId : undefined,
      consent: true, replace_existing_epub: replaceEpub, include_report_page: true,
      source_recovery: recovery, ocr_language: ocrLang,
    }, {
      onError: (err) => {
        setError(err instanceof ApiError ? err.message : t('The conversion could not be started.'));
        if (err instanceof ApiError && ['source_changed', 'estimate_stale', 'current_consent_required'].includes(String(err.detail?.code))) {
          setPreparation(null); setConsent(false); void estimateQ.refetch();
        }
      },
    });
  };

  if (estimateQ.isLoading) {
    return <SpinnerCentered />;
  }

  if (estimateQ.error || !est) {
    const message = estimateQ.error instanceof ApiError
      ? estimateQ.error.message
      : t('This book has no PDF to convert.');
    return (
      <div className={styles.container}>
        <Header id={id} title={book?.title ?? ''} t={t} />
        <EmptyState icon={AlertTriangle} title={t('Nothing to convert')} message={message} />
      </div>
    );
  }


  return (
    <div className={styles.container}>
      <Header id={id} title={est.title || book?.title || ''} t={t} />

      {!est.configured && (
        <div className={styles.notConfigured} role="status">
          <KeyRound size={16} aria-hidden="true" focusable={false} />
          <span>
            {t('Source conversion is available without a provider key. Optional AI formatting review needs an OpenRouter key.')}{' '}
            <Link href="/admin/config" className={styles.adminLink}>{t('Open admin settings')}</Link>
          </span>
        </div>
      )}

      <section className={styles.card} aria-labelledby="reflow-assessment">
        <h2 className={styles.cardTitle} id="reflow-assessment">{t('What is in this PDF')}</h2>
        <p className={styles.verdict}>{verdictSentence(est.verdict, t)}</p>
        <dl className={styles.facts}>
          <Fact label={t('Pages')} value={String(est.pages)} />
          <Fact label={t('Source text')}
            value={!needsRecovery
              ? t('Native text layer (not proofread)')
              : rec.damaged > 0
                ? t('Damaged layer — recovery required')
                : t('Pictures only — recovery required')} />
        </dl>
        <p className={styles.note}>
          {t('Every selected page gets a source conversion. Optional AI review can suggest supported heading and displayed quotation formatting; it does not rewrite words, repair OCR, or reconstruct tables and notes.')}
        </p>
      </section>

      {needsRecovery && rec && (
        <section className={styles.card} aria-labelledby="reflow-recovery">
          <h2 className={styles.cardTitle} id="reflow-recovery">{t('Source recovery')}</h2>
          <p className={styles.verdict}>
            {rec.damaged > 0
              ? t('{pages} pages have a text layer that is not readable words. They are read off the printed page with local text recognition — a transcription of the source, never a rewrite.')
                  .replace('{pages}', String(rec.damaged))
              : t('{pages} pages are only pictures of pages. They are read off the printed page with local text recognition — a transcription of the source, never a rewrite.')
                  .replace('{pages}', String(rec.image_only))}
          </p>
          <dl className={styles.facts}>
            <Fact label={t('Recognition engine')}
              value={rec.engine_available
                ? rec.engine_version
                : t('Not installed')} />
            <Fact label={t('Language data')} value={ocrLang} />
            <Fact label={t('Local time (no OpenRouter credit)')}
              value={t('about {seconds}s for {pages} pages')
                .replace('{seconds}', String(rec.estimated_seconds))
                .replace('{pages}', String(rec.ocr_candidates))} />
          </dl>
          {rec.non_latin_share >= 0.5 && (
            <p className={styles.capWarn} role="status">
              {t('Most of this PDF is not Latin-alphabet text. The selected recognition language will misread it unless a matching language pack is installed.')}
            </p>
          )}
          {!rec.engine_available && (
            <p className={styles.capWarn} role="alert">
              <AlertTriangle size={15} aria-hidden="true" focusable={false} />
              {' '}{rec.engine_detail}{' '}
              {t('An administrator must install it before text can be recovered, or you can keep a facsimile of the page images.')}
            </p>
          )}
          <div className={styles.modes} role="radiogroup" aria-label={t('Source recovery')}>
            <label className={recovery === 'auto' ? styles.modeOn : styles.mode}>
              <input type="radio" name="reflow-recovery" className={styles.radio}
                checked={recovery === 'auto'}
                disabled={!rec.engine_available}
                onChange={() => setRecovery('auto')} />
              <span className={styles.modeLabel}>{t('Recover the text')}</span>
              <span className={styles.modeHint}>
                {t('Local OCR runs before formatting. Uncertain readings remain disclosed with original-source access; some readings cannot be marked at an exact word.')}
              </span>
            </label>
            {rec.damaged > 0 && (
              <label className={recovery === 'textless' ? styles.modeOn : styles.mode}>
                <input type="radio" name="reflow-recovery" className={styles.radio}
                  checked={recovery === 'textless'}
                  disabled={!rec.engine_available}
                  onChange={() => setRecovery('textless')} />
                <span className={styles.modeLabel}>{t('Recover pictures only')}</span>
                <span className={styles.modeHint}>
                  {t('Read pages with no text at all. Existing text layers stay unchanged, including any extraction errors, with source evidence available.')}
                </span>
              </label>
            )}
            <label className={recovery === 'off' ? styles.modeOn : styles.mode}>
              <input type="radio" name="reflow-recovery" className={styles.radio}
                checked={recovery === 'off'}
                onChange={() => setRecovery('off')} />
              <span className={styles.modeLabel}>{t('No recovery — facsimile only')}</span>
              <span className={styles.modeHint}>
                {t('Keep the page images as they are. This is a facsimile of the book, not reflowed text.')}
              </span>
            </label>
          </div>
          {rec.engine_available && (
            <label className={styles.field}>
              <span className={styles.label}>{t('Recognition language')}</span>
              <input className={styles.inputNarrow} type="text" value={ocrLang}
                maxLength={64}
                onChange={(e) => setOcrLang(e.target.value.trim() || 'eng')} />
              <span className={styles.fieldHint}>
                {t('An installed language code, for example eng, deu, fra, ell.')}
              </span>
            </label>
          )}
        </section>
      )}

      <section className={styles.card} aria-labelledby="reflow-cost">
        <h2 className={styles.cardTitle} id="reflow-cost">{t('Formatting review')}</h2>
        <div className={styles.modes} role="radiogroup" aria-label={t('Formatting review')}>
          <label className={!paid ? styles.modeOn : styles.mode}>
            <input type="radio" name="reflow-review" className={styles.radio} checked={!paid}
              onChange={() => setReviewMode('deterministic')} />
            <span className={styles.modeLabel}>{t('Source conversion')}</span>
            <span className={styles.modeHint}>{t('No model requests or provider charges. Keep source text, images, navigation and uncertainty evidence.')}</span>
          </label>
          <label className={paid ? styles.modeOn : styles.mode}>
            <input type="radio" name="reflow-review" className={styles.radio} checked={paid}
              disabled={!paidAvailable} aria-describedby="reflow-review-availability"
              onChange={() => setReviewMode('source_verified')} />
            <span className={styles.modeLabel}>{t('Add AI formatting review')}</span>
            <span className={styles.modeHint}>{t('Luna proposes heading and quotation formatting. Terra reviews each proposal; only approved changes can reach the EPUB.')}</span>
          </label>
        </div>
        <p className={styles.note} id="reflow-review-availability">
          {!est.review.quality_released
            ? t('AI review is not available in this build. Source conversion remains available.')
            : !est.configured ? t('Configure an OpenRouter key to use optional AI review.')
              : t('Optional review uses the two models below, with no automatic alternative route.')}
        </p>
        <dl className={styles.facts}>
          <Fact label={t('Planned proposer')} value={est.review.proposer} />
          <Fact label={t('Planned reviewer')} value={est.review.verifier} />
          <Fact label={t('Provider route')} value={est.review.provider} />
        </dl>
        {paid && <>
          <p className={styles.note}>{t('Prepare a local estimate before consenting to review. This reads complete source context and may take time, but sends nothing to a model and reserves no provider credit.')}</p>
          <Button variant="ghost" disabled={preparing || engineBlocks || running} onClick={() => {
            setError(null);
            const key = sourceKey;
            prepare.mutate({ source_recovery: recovery, ocr_language: ocrLang }, {
              onSuccess: (data) => setPreparation({ id: data.preparation_id, key }),
              onError: (err) => setError(err instanceof ApiError ? err.message : t('Source estimate preparation failed. No model request was sent.')),
            });
          }}>{t('Prepare AI estimate')}</Button>
          {preparing && preparationId && <Button variant="ghost" disabled={cancelPreparation.isPending}
            onClick={() => cancelPreparation.mutate(preparationId, {
              onError: () => setError(t('The preparation could not be stopped. Try again.')),
            })}>{t('Stop preparation')}</Button>}
        </>}
        <p className={styles.note} role="status">
          {paid && (note.kind === 'queued'
            ? (note.ahead === 1 ? t('Waiting for one earlier source preparation to finish.')
              : t('Waiting for {count} earlier source preparations to finish.', { count: note.ahead }))
            : note.kind === 'preparing' ? t('Preparing source context: {page} of {pages}', {
              page: note.page, pages: note.pages ?? est.pages })
              : note.kind === 'cancelled' ? t('Source preparation stopped. No model request was sent.')
                : note.kind === 'timed_out' ? t('Source preparation stopped at its {minutes}-minute limit so that others can take a turn. Pages already recognized are kept: prepare again to continue.', { minutes: note.minutes })
                  : note.kind === 'failed' ? t('Source estimate preparation failed. No model request was sent.')
                    : note.kind === 'ready' ? t('Source estimate ready. No credit has been reserved.') : '')}
        </p>
        {paid && quote && <>
          <dl className={styles.facts}>
            <Fact label={t('Source pages prepared')} value={String(quote.source_context_pages)} />
            <Fact label={t('Eligible pages in this selection')} value={String(selected.eligible)} />
            <Fact label={t('Coverage-limited pages')} value={String(selected.limited)} />
            <Fact label={t('Unsupported pages')} value={String(selected.unsupported)} />
            <Fact label={t('Pages with no supported choices')} value={String(selected.noChoices)} />
            <Fact label={t('Full-review reservation ceiling')} value={ledgerUsd(needed)} />
          </dl>
          <p className={styles.note}>{t('This ceiling assumes a proposal and a review request for every eligible page, with no cached responses. It is not an expected bill or money already held. Actual charges and coverage may be lower; your cap limits new requests.')}</p>
          <p className={styles.note}>{t('Eligibility is not an improvement. Unsupported, limited, declined and unreviewed pages keep the source conversion and its uncertainty evidence.')}</p>
        </>}
      </section>

      <section className={styles.card} aria-labelledby="reflow-start">
        <h2 className={styles.cardTitle} id="reflow-start">{t('How much to convert')}</h2>

        <div className={styles.modes} role="radiogroup" aria-label={t('How much to convert')}>
          <label className={mode === 'sample' ? styles.modeOn : styles.mode}>
            <input type="radio" name="reflow-mode" checked={mode === 'sample'}
              className={styles.radio} onChange={() => setMode('sample')} />
            <span className={styles.modeLabel}>{t('A sample')}</span>
            <span className={styles.modeHint}>
              {samplePages === 1
                ? t('One page, downloaded as an EPUB to look at. Nothing is added to your library.')
                : t('{pages} pages, downloaded as an EPUB to look at. Nothing is added to your library.')
                  .replace('{pages}', String(samplePages))}
            </span>
          </label>
          <label className={mode === 'full' ? styles.modeOn : styles.mode}>
            <input type="radio" name="reflow-mode" checked={mode === 'full'}
              className={styles.radio} onChange={() => setMode('full')} />
            <span className={styles.modeLabel}>{t('The whole book')}</span>
            <span className={styles.modeHint}>
              {t('{pages} pages, added to this book as an EPUB format.')
                .replace('{pages}', String(est.pages))}
            </span>
          </label>
        </div>

        {mode === 'sample' && (
          <label className={styles.field}>
            <span className={styles.label}>{t('Pages in the sample')}</span>
            <input className={styles.inputNarrow} type="number" min={1}
              max={est.sample_pages_max} value={samplePages}
              onChange={(e) => setSamplePages(Math.max(1, Math.min(
                est.sample_pages_max, Number.parseInt(e.target.value, 10) || 1)))} />
            <span className={styles.fieldHint}>
              {paid
                ? t('The sample starts at the first body page and keeps the full source context for note and figure associations.')
                : t('The sample starts at the first body page and reads only the front of the book, so it stays quick however long the PDF is.')}
            </span>
          </label>
        )}

        {mode === 'full' && est.existing_epub && (
          <label className={styles.checkRow}>
            <input type="checkbox" className={styles.check} checked={replaceEpub}
              onChange={(e) => setReplaceEpub(e.target.checked)} />
            <span>{t('Replace the EPUB this book already has')}</span>
          </label>
        )}

        {paid && <label className={styles.field}>
          <span className={styles.label}>{t('Stop after spending')}</span>
          <input className={styles.inputNarrow} type="number" min="0.000001" step="0.000001"
            aria-invalid={capTouched && !capValid} aria-describedby="reflow-cap-help reflow-cap-error"
            max={est.hard_cap_usd} value={cap}
            onChange={(e) => { setCapTouched(true); setCap(e.target.value); }} />
          <span className={styles.fieldHint} id="reflow-cap-help">
            {t('Your administrator allows at most {amount} for one conversion.')
              .replace('{amount}', usd(est.hard_cap_usd))}
          </span>
        </label>}
        <p id="reflow-cap-error" className={styles.error} role="alert">{paid && capTouched && !capValid ? t('Enter a positive cap within the administrator limit.') : ''}</p>
        {capTooLow && (
          <p className={styles.capWarn} role="status">
            {t('This cap may cover only part of the AI review. The complete source conversion will still be produced; pages left unreviewed will be counted explicitly.')}
          </p>
        )}

        {paid && hold > 0 && (
          <>
            <p className={styles.capWarn} role="alert">
              {t('An earlier job left {amount} unconfirmed: a request was sent and its answer never came back, so the provider may still charge it. It is not part of this new cap, and starting again does not settle or erase it.')
                .replace('{amount}', ledgerUsd(hold))}
            </p>
            <label className={styles.consent}>
              <input type="checkbox" className={styles.check} checked={holdAcknowledged}
                onChange={(e) => setHoldAcknowledged(e.target.checked)} />
              <span>
                {t('I understand the unconfirmed {amount} from the earlier job may still be charged, and it is not covered by this consent.')
                  .replace('{amount}', ledgerUsd(hold))}
              </span>
            </label>
          </>
        )}

        <label className={styles.consent}>
          <input type="checkbox" className={styles.check} checked={consent}
            disabled={paid && (!paidAvailable || !quote)} onChange={(e) => setConsent(e.target.checked)} />
          <span>
            {paid ? t('I authorize Luna proposals and Terra reviews through OpenRouter openai/flex, up to {amount} for this job. Unreviewed pages keep the source conversion.', { amount: ledgerUsd(authorised) })
              : t('Create this source conversion without model requests or provider charges.')}
          </span>
        </label>

        <p className={styles.error} role="alert">{error}</p>

        <Button onClick={onStart} disabled={blocked}>
          <Sparkles size={15} aria-hidden="true" focusable={false} />
          {facsimile
            ? t('Build the facsimile EPUB')
            : mode === 'sample' ? t('Convert the sample') : t('Convert the book')}
        </Button>
        {facsimile && (
          <p className={styles.note} role="status">
            {t('This keeps the page images as they are — a facsimile of the book, not reflowed text.')}
          </p>
        )}
      </section>

      {running && active.map((task) => (
        <section key={task.task_id} className={styles.card} aria-live="polite">
          <h2 className={styles.cardTitle}>
            {/* The spin belongs on the wrapper: an SVG gets no compositor layer,
                so animating it there drops the whole thing onto the main thread
                while a conversion is busy. */}
            <span className={styles.spin} aria-hidden="true">
              <Loader2 size={16} aria-hidden="true" focusable={false} />
            </span>
            {STATUS_LABEL(task.status, t)}
          </h2>
          <div className={styles.bar}>
            <div className={styles.barFill} style={{ width: `${Math.round(task.progress * 100)}%` }} />
          </div>
          <p className={styles.progressMessage}>{task.message}</p>
          {task.cancellable && (
            <Button variant="ghost" onClick={() => cancel.mutate(task.task_id)}>
              <X size={15} aria-hidden="true" focusable={false} /> {t('Stop')}
            </Button>
          )}
        </section>
      ))}

      {latest && !running && (
        <JobResult job={latest} bookId={id} t={t} onConvertAll={() => {
          setMode('full'); setCapTouched(false);
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }} />
      )}
    </div>
  );
}

function Header({ id, title, t }: { id: string; title: string; t: TFunction }) {
  return (
    <header className={styles.header}>
      <Link href={`/book/${id}/edit`} className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" focusable={false} /> {t('Back to edit')}
      </Link>
      <h1 className={styles.title}>{t('Reflow PDF to EPUB')}</h1>
      <p className={styles.subtitle}>{title}</p>
    </header>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.fact}>
      <dt className={styles.factLabel}>{label}</dt>
      <dd className={styles.factValue}>{value}</dd>
    </div>
  );
}

/** What happened, in the two numbers that decide whether to buy the rest: how
 *  many pages the model's version was accepted for, and what it cost. */
function JobResult({ job, bookId, t, onConvertAll }: {
  job: ReflowJob; bookId: string; t: TFunction; onConvertAll: () => void;
}) {
  const produced = !!job.artifact || (job.structural == null && ['done', 'capped', 'incomplete'].includes(job.status));
  const whole = job.status === 'done';
  const legacy = jobCounts(job);
  const scope = job.structural;

  return (
    <section className={styles.card} aria-labelledby="reflow-result">
      <h2 className={styles.cardTitle} id="reflow-result">
        {whole
          ? (job.mode === 'sample' ? t('Your sample is ready') : t('The book was converted'))
          : STATUS_LABEL(job.status, t)}
      </h2>

      {job.error && <p className={styles.error} role="alert">{job.error}</p>}
      {job.status === 'capped' && (
        <p className={styles.capWarn} role="status">
          {t('AI review stopped at the {amount} cap. The complete source conversion remains in the file; approved changes are included and the remaining pages are unreviewed.')
            .replace('{amount}', usd(job.cap_usd))}
        </p>
      )}
      {job.status === 'incomplete' && (
        <p className={styles.capWarn} role="status">
          {t('AI review ended before all eligible pages were reviewed. A completed file retains the full source conversion and any approved changes. Compatible responses may be reused; unresolved requests are not retried automatically.')}
        </p>
      )}
      {job.status === 'billing_unknown' && (
        <p className={styles.capWarn} role="alert">
          {t('This stopped when a request’s answer never came back, so its charge could not be confirmed either way. Spent below is confirmed; the unconfirmed amount may still be charged by the provider and stays on record here until it is resolved. Reloading does not settle or erase it, and no new job covers it.')}
        </p>
      )}
      {job.status === 'interrupted' && (
        <p className={styles.capWarn} role="status">
          {t('The app restarted before this job recorded completion. Your original PDF is unchanged. Pages already converted may be reused if you retry. Any unresolved charges remain recorded for review; a lost response may still have been charged.')}
        </p>
      )}

      <dl className={styles.facts}>
        <Fact label={t('Confirmed spend')} value={ledgerUsd(job.spend_usd)} />
        {heldUsd(job) > 0 && (
          <Fact label={t('Unconfirmed, may still be charged')}
            value={ledgerUsd(heldUsd(job))} />
        )}
        <Fact label={t('Job cap')} value={ledgerUsd(job.cap_usd)} />
        {scope ? <>
          <Fact label={t('Source pages prepared')} value={scope.context === 'sample' && scope.source_pages
            ? t('{done} of {total}', { done: scope.source_context_pages, total: scope.source_pages })
            : String(scope.source_context_pages)} />
          <Fact label={t('Pages in this file')} value={String(scope.total_pages)} />
          {scope.eligibility_measured && <>
            <Fact label={t('Eligible pages')} value={String(scope.eligible)} />
            <Fact label={t('Coverage-limited pages')} value={String(scope.limited)} />
            <Fact label={t('Unsupported pages')} value={String(scope.unsupported)} />
            <Fact label={t('Pages with no supported choices')} value={String(scope.no_choices)} />
            <Fact label={t('Pages left unreviewed')} value={String(scope.unreviewed)} />
            <Fact label={t('Proposed operations')} value={String(scope.proposed_operations)} />
            <Fact label={t('Approved operations')} value={String(scope.approved_operations)} />
            <Fact label={t('Pages with approved changes')} value={String(scope.approved_pages)} />
            <Fact label={t('Proposer abstentions')} value={String(scope.proposer_abstained)} />
            <Fact label={t('Reviewer abstentions')} value={String(scope.verifier_abstained)} />
            <Fact label={t('Rejected pages')} value={String(scope.rejected)} />
            <Fact label={t('Requests attempted')} value={String(scope.attempted_stages)} />
            <Fact label={t('Models requested')} value={Object.keys(scope.requested_models ?? {}).join(', ') || t('None')} />
            <Fact label={t('Pages sent to a model')} value={String(scope.attempted_pages)} />
            <Fact label={t('Cached stage results')} value={String(scope.cached_stages)} />
          </>}
        </> : <>
          <Fact label={t('Legacy requests recorded')} value={String(legacy.sent)} />
          <Fact label={t('Legacy gate acceptances')} value={String(legacy.adopted)} />
        </>}
        {(job.recovery.mode_pages ?? 0) > 0 && (
          <Fact label={t('Pages read with local OCR')}
            value={String(job.recovery.mode_pages)} />
        )}
        {(job.recovery.uncertain_words ?? 0) > 0 && (
          <Fact label={t('Uncertain readings recorded')}
            value={String(job.recovery.uncertain_words)} />
        )}
      </dl>
      <p className={styles.note}>
        {scope?.eligibility_measured
          ? t('Approval counts describe formatting adopted after source and reviewer checks, not independent proof of correctness. An abstention or unchanged page is not an improvement.')
          : scope ? t('AI review was not requested. Eligibility was not measured; this file uses source conversion and preserves its uncertainty evidence.')
            : t('This older job predates the current two-stage review. Its counts are historical and do not establish current review coverage.')}
      </p>
      {job.artifact && <p className={styles.note}>
        {t('Filed EPUB SHA-256: {hash}', { hash: job.artifact.sha256 })}
      </p>}

      {job.sample_url && job.sample_expired && (
        <p className={styles.note}>
          {t('This sample is no longer kept: samples are removed {days} days after they are made. Make a new sample to see it again.',
            { days: job.sample_kept_days ?? 7 })}
        </p>
      )}

      <div className={styles.actions}>
        {job.sample_url && job.sample_ready && (
          <a className={styles.download} href={resourceUrl(job.sample_url)}>
            <Download size={15} aria-hidden="true" focusable={false} /> {t('Download the sample')}
          </a>
        )}
        {job.mode === 'sample' && produced && (
          <Button variant="ghost" onClick={onConvertAll}>
            <Check size={15} aria-hidden="true" focusable={false} /> {t('Convert the whole book')}
          </Button>
        )}
        {job.mode === 'full' && produced && (
          <Link href={`/book/${bookId}`} className={styles.download}>
            {t('Open the book')}
          </Link>
        )}
      </div>
    </section>
  );
}
