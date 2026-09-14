import { useEffect, useState } from 'react';
import { Link } from 'wouter';
import {
  ChevronLeft, Sparkles, AlertTriangle, KeyRound, Download, Check, X, Loader2,
} from 'lucide-react';
import { useBook } from '../lib/queries';
import {
  useReflowEstimate, useReflowJobs, useStartReflow, useCancelReflow,
  requiredUsd, sampleRoutedPages, suggestedCap, usd,
  type ReflowJob, type ReflowMode,
} from '../lib/reflow';
import { Button } from '../components/Button';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { ApiError, resourceUrl } from '../lib/api';
import { useT, type TFunction } from '../lib/i18n';
import styles from './Reflow.module.css';

/** The assessment, in the reader's language.
 *
 *  The server writes its own copy of these sentences into the EPUB's report page,
 *  which travels inside the book and is not in the reader's UI language. This one
 *  is for the person deciding whether to spend money, so it is translated. */
function verdictSentence(verdict: string, t: TFunction): string {
  switch (verdict) {
    case 'BORN_DIGITAL':
      return t('This PDF has a real text layer. Most pages will convert without asking a model anything.');
    case 'OCR_LAYER':
      return t('Every page is a picture of the page with OCR text behind it. The words are readable, but the structure has to be rebuilt.');
    case 'THIN_TEXT':
      return t('There is very little text on each page. This is usually a sparse scan, or two printed pages photographed as one.');
    case 'NO_TEXT_LAYER':
      return t('There is no text in this PDF at all, only images. Every page has to be read by the model, which costs the most.');
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
  failed: t('Failed'),
  // Not the classic task list's "Cancelled": a conversion has two ways of
  // stopping early and the bill is different, so each says which one it was.
  cancelled: t('Stopped before it finished'),
}[status] ?? status);

export function Reflow({ id }: { id: string }) {
  const t = useT();
  const { data: book } = useBook(id);
  const estimateQ = useReflowEstimate(id);
  const est = estimateQ.data;

  const [mode, setMode] = useState<ReflowMode>('sample');
  const [tier, setTier] = useState<string>('');
  const [samplePages, setSamplePages] = useState<number>(20);
  const [cap, setCap] = useState<string>('');
  const [capTouched, setCapTouched] = useState(false);
  const [consent, setConsent] = useState(false);
  const [replaceEpub, setReplaceEpub] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = useStartReflow(id);
  const cancel = useCancelReflow();

  // Defaults come from the server: which tier the administrator chose, how long a
  // sample is, and whether this book is expensive enough to sample first.
  useEffect(() => {
    if (!est) return;
    setTier((current) => current || est.default_tier);
    setSamplePages(est.sample_pages_default);
    setMode(est.sample_suggested ? 'sample' : 'full');
  }, [est]);

  const needed = est && tier ? requiredUsd(est, tier, mode, samplePages) : 0;

  // The cap follows the estimate until the user types one of their own, and then
  // it is theirs: silently rewriting a number somebody set is how a job spends
  // more than they meant it to.
  useEffect(() => {
    if (!est || capTouched) return;
    setCap(suggestedCap(needed, est.hard_cap_usd).toFixed(2));
  }, [est, needed, capTouched]);

  // Consent is to one figure. Change the figure and it has to be given again.
  useEffect(() => { setConsent(false); }, [needed, mode, tier, samplePages]);

  const jobsQ = useReflowJobs(id);
  const active = jobsQ.data?.active ?? [];
  const running = active.length > 0;
  // The server lists jobs newest first (ledger.read_summaries).
  const latest = jobsQ.data?.items?.[0] ?? null;

  const capNumber = Number.parseFloat(cap);
  const capValid = Number.isFinite(capNumber) && capNumber > 0
    && (!est || capNumber <= est.hard_cap_usd + 1e-9);
  const capTooLow = capValid && capNumber + 1e-9 < needed;
  const blocked = !est?.configured || running || !consent || !capValid || capTooLow
    || start.isPending;

  const onStart = () => {
    if (!est || blocked) return;
    setError(null);
    start.mutate({
      mode, model_tier: tier, sample_pages: samplePages, cost_cap_usd: capNumber,
      consent: true, replace_existing_epub: replaceEpub, include_report_page: true,
    }, {
      onError: (err) => setError(err instanceof ApiError ? err.message
        : t('The conversion could not be started.')),
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
            {t('No OpenRouter key is configured, so nothing can be converted yet.')}{' '}
            <Link href="/admin" className={styles.adminLink}>{t('Open admin settings')}</Link>
          </span>
        </div>
      )}

      <section className={styles.card} aria-labelledby="reflow-assessment">
        <h2 className={styles.cardTitle} id="reflow-assessment">{t('What is in this PDF')}</h2>
        <p className={styles.verdict}>{verdictSentence(est.verdict, t)}</p>
        <dl className={styles.facts}>
          <Fact label={t('Pages')} value={String(est.pages)} />
          <Fact label={t('Pages a model will read')}
            value={`${est.routed_pages_estimate} (${Math.round(est.routed_share * 100)}%)`} />
          <Fact label={t('Text layer')}
            value={est.text_layer ? t('Yes') : t('No')} />
        </dl>
        <p className={styles.note}>
          {t('Every other page is converted by reading the PDF itself, which costs nothing. A page only goes to a model when the layout cannot be settled without one.')}
        </p>
      </section>

      <section className={styles.card} aria-labelledby="reflow-cost">
        <h2 className={styles.cardTitle} id="reflow-cost">{t('What it will cost')}</h2>
        <div className={styles.tiers} role="radiogroup" aria-label={t('Model')}>
          {est.tiers.map((choice) => (
            <label key={choice.tier}
              className={choice.tier === tier ? styles.tierOn : styles.tier}>
              <input type="radio" name="reflow-tier" value={choice.tier}
                checked={choice.tier === tier} className={styles.radio}
                onChange={() => setTier(choice.tier)} />
              <span className={styles.tierLabel}>{choice.label}</span>
              <span className={styles.tierPrice}>
                {usd(est.estimate_usd[choice.tier] ?? 0)}
              </span>
              <span className={styles.tierModel}>{choice.model}</span>
            </label>
          ))}
        </div>
        <p className={styles.targetLine}>
          {t('The whole book, at the model you picked:')}{' '}
          <strong>{usd(est.estimate_usd[tier] ?? 0)}</strong>
          {' · '}
          {t('Your administrator set a target of {amount} a book.')
            .replace('{amount}', usd(est.target_usd))}
        </p>
        {est.over_target && (
          <p className={styles.overTarget} role="status">
            <AlertTriangle size={15} aria-hidden="true" focusable={false} />
            {t('This book costs more than the target. Convert a sample first and see whether the result is worth the rest.')}
          </p>
        )}
        <p className={styles.note}>
          {t('Prices measured on {date}. What you are charged is capped below — a conversion stops when it reaches the cap, and keeps the pages already paid for.')
            .replace('{date}', est.priced_on)}
        </p>
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
              {t('About {pages} of them will need a model.')
                .replace('{pages}', String(sampleRoutedPages(est, samplePages)))}
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

        <label className={styles.field}>
          <span className={styles.label}>{t('Stop after spending')}</span>
          <input className={styles.inputNarrow} type="number" min="0.01" step="0.01"
            max={est.hard_cap_usd} value={cap}
            onChange={(e) => { setCapTouched(true); setCap(e.target.value); }} />
          <span className={styles.fieldHint}>
            {t('Your administrator allows at most {amount} for one conversion.')
              .replace('{amount}', usd(est.hard_cap_usd))}
          </span>
        </label>
        {capTooLow && (
          <p className={styles.capWarn} role="status">
            {t('That is below the estimate, so the conversion would stop before it finished.')}
          </p>
        )}

        <label className={styles.consent}>
          <input type="checkbox" className={styles.check} checked={consent}
            disabled={!est.configured} onChange={(e) => setConsent(e.target.checked)} />
          <span>
            {t('I agree to spend up to {amount} of my own OpenRouter credit on this conversion.')
              .replace('{amount}', usd(needed))}
          </span>
        </label>

        {error && <p className={styles.error} role="alert">{error}</p>}

        <Button onClick={onStart} disabled={blocked}>
          <Sparkles size={15} aria-hidden="true" focusable={false} />
          {mode === 'sample' ? t('Convert the sample') : t('Convert the book')}
        </Button>
      </section>

      {running && active.map((task) => (
        <section key={task.task_id} className={styles.card} aria-live="polite">
          <h2 className={styles.cardTitle}>
            <Loader2 size={16} className={styles.spin} aria-hidden="true" focusable={false} />
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
  const finished = job.status === 'done' || job.status === 'capped';
  const adopted = job.gate?.PASS ?? 0;
  const refused = (job.gate?.FAIL ?? 0) + (job.gate?.NOT_APPLICABLE ?? 0);
  const sent = adopted + refused;
  const share = sent ? Math.round((adopted / sent) * 100) : 0;

  return (
    <section className={styles.card} aria-labelledby="reflow-result">
      <h2 className={styles.cardTitle} id="reflow-result">
        {finished
          ? (job.mode === 'sample' ? t('Your sample is ready') : t('The book was converted'))
          : STATUS_LABEL(job.status, t)}
      </h2>

      {job.error && <p className={styles.error} role="alert">{job.error}</p>}
      {job.status === 'capped' && (
        <p className={styles.capWarn} role="status">
          {t('This stopped at the {amount} cap. The pages converted before it was reached are in the file; the rest were left as they were.')
            .replace('{amount}', usd(job.cap_usd))}
        </p>
      )}

      <dl className={styles.facts}>
        <Fact label={t('Spent')} value={usd(job.spend_usd)} />
        <Fact label={t('Pages sent to a model')} value={String(sent)} />
        <Fact label={t('Pages the check accepted')} value={`${adopted} (${share}%)`} />
        {job.reused > 0 && (
          <Fact label={t('Pages reused from an earlier run')} value={String(job.reused)} />
        )}
      </dl>
      {refused > 0 && (
        <p className={styles.note}>
          {refused === 1
            ? t('One page did not pass the word check and kept the text read straight out of the PDF. Nothing was rewritten.')
            : t('{count} pages did not pass the word check and kept the text read straight out of the PDF. Nothing was rewritten.')
              .replace('{count}', String(refused))}
        </p>
      )}

      <div className={styles.actions}>
        {job.sample_url && job.sample_ready && (
          <a className={styles.download} href={resourceUrl(job.sample_url)}>
            <Download size={15} aria-hidden="true" focusable={false} /> {t('Download the sample')}
          </a>
        )}
        {job.mode === 'sample' && finished && (
          <Button variant="ghost" onClick={onConvertAll}>
            <Check size={15} aria-hidden="true" focusable={false} /> {t('Convert the whole book')}
          </Button>
        )}
        {job.mode === 'full' && finished && (
          <Link href={`/book/${bookId}`} className={styles.download}>
            {t('Open the book')}
          </Link>
        )}
      </div>
    </section>
  );
}
