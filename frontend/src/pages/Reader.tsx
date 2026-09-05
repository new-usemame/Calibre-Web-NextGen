import { resumeCfi } from "../lib/readerResume";
import { useEffect, useRef, useState, useCallback } from 'react';
import { Link } from 'wouter';
import ePub from 'epubjs';
import {
  ChevronLeft, ChevronRight, X, List, Sun, Moon, Coffee, Loader2, Trash2,
  SlidersHorizontal, StickyNote, Highlighter, MoonStar, Maximize, Minimize,
  Search,
} from 'lucide-react';
import {
  type ReaderSettings, isWorthResending, useBook, useBookmark, useReaderSettings,
  useSaveBookmark, useSaveReaderSettings,
} from '../lib/queries';
import { apiPost, apiDelete, apiPatch, apiUrl, resourceUrl } from '../lib/api';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { VisuallyHidden } from '../components/VisuallyHidden';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import {
  DEFAULT_HIT_CAP, MIN_QUERY_LENGTH, searchBook, type SearchHit,
} from '../lib/reader/searchBook';
import { chapterLabelForHref, splitSearchExcerpt } from '../lib/reader/searchUi';
import { safeLocalStorageGet, safeLocalStorageSet } from '../lib/safeStorage';
import { getReaderContentUrl } from '../lib/readerTarget';
import styles from './Reader.module.css';

// Highlight colors as ARIA/label keys (SC 1.4.1: a color must never be conveyed
// by hue alone — every swatch + saved highlight carries the color's name).
const HILITE_ORDER = ['yellow', 'green', 'blue', 'red'] as const;
type HiliteColor = (typeof HILITE_ORDER)[number];

// Fill for a highlight the reader RENDERS. Wider than HILITE_ORDER on purpose:
// the palette the reader OFFERS is four colours, but a Kobo can hand us pink or
// grey (F-5769c9) and those have to paint as themselves rather than fall
// through to yellow. Rendered semi-transparent.
const HILITE_FILL: Record<string, string> = {
  yellow: '#e6c34a', red: '#d9534f', green: '#5cb85c', blue: '#5b9bd5',
  pink: '#e8afcf', grey: '#a0a0a0',
};

// What an unknown or absent colour paints as. Deliberately NOT a palette entry:
// a highlight still has to be visible, but falling back to yellow would make a
// colour we could not resolve indistinguishable from one the reader really did
// choose — the invented-colour bug this file's server side stopped doing.
const UNKNOWN_FILL = '#d0cbc2';

type ReaderTheme = 'light' | 'sepia' | 'dark' | 'black';

interface TocItem {
  label: string;
  href: string;
}

/** A saved highlight as the reader needs it: enough to list, jump to and edit. */
interface AnnRow {
  annotation_id: string;
  cfi_range: string | null;
  highlighted_text: string | null;
  note_text: string | null;
  highlight_color: string | null;
  /** 'webreader' | 'kobo' | 'koreader' | null — shown so a device highlight is
   *  identifiable, and because only some origins carry a usable CFI. */
  source: string | null;
  /** Public id of the device that MADE this highlight, or null. Resolved
   *  against the `devices` map in the same response — never rendered raw, and
   *  never used to filter: see the loader below. */
  origin_device_id?: string | null;
  /** 'cfi' | 'pdf_quad' | 'comic_page' | 'koreader_xpointer' | 'unanchored' |
   *  null. Only 'unanchored' concerns this list: such a row is a note ABOUT the
   *  book with no passage attached, so it must not be drawn as a highlight that
   *  has lost its anchor. NULL means legacy EPUB CFI. */
  position_type: string | null;
}

// epub.js ships loose types; the rendition/book objects are treated as `any`
// behind small typed wrappers so the rest of the component stays readable.
/* eslint-disable @typescript-eslint/no-explicit-any */

// !important on the body rules so a theme switch always wins over the book's own
// CSS and any previously-selected theme (without it, re-selecting a theme epub.js
// considers "already applied" can leave the prior background showing).
const THEMES: Record<ReaderTheme, { body: Record<string, string> }> = {
  light: { body: { background: '#fbf7ee !important', color: '#2a2a2a !important' } },
  sepia: { body: { background: '#f2e6cf !important', color: '#43381f !important' } },
  dark: { body: { background: '#15110c !important', color: '#cdc6bb !important' } },
  /*
   * A FOURTH theme, and not a duplicate of dark: the ground is pure black so an
   * OLED screen switches those pixels off, which is the whole point of a black
   * theme at night. `dark` is a warm near-black (#15110c) and still lights every
   * pixel.
   *
   * The classic reader has had this for years and stores it as `blackTheme`;
   * this reader mapped that value onto `dark`, so anyone who chose Black got the
   * brown-black instead and could not get back — the same shape as the column
   * preference that was being saved and ignored.
   *
   * Ink is the dark theme's #cdc6bb rather than pure white: 12.39:1 on black,
   * comfortably past the 4.5:1 AA floor, and it keeps the two dark themes
   * consistent so only the ground changes. Pure white measures 21:1 but haloes
   * badly on OLED in the dark, which is exactly when this theme gets used.
   */
  black: { body: { background: '#000000 !important', color: '#cdc6bb !important' } },
};

// #1303: Japanese and Traditional Chinese books progress right-to-left, which
// the EPUB declares as `page-progression-direction="rtl"` on <spine>. epub.js
// surfaces it as `metadata.direction` and uses it for layout, but `next()` and
// `prev()` always mean spine-FORWARD and spine-BACKWARD regardless — so it is
// the reader's job to decide which side of the screen each one belongs on. For
// an RTL book forward runs leftward, so the two zones swap. `packaging` is the
// current field; `package` is its deprecated alias, kept as a fallback because
// the classic reader still reads that one.
function isRtlBook(book: any): boolean {
  try {
    const metadata = book?.packaging?.metadata || book?.package?.metadata;
    return metadata?.direction === 'rtl';
  } catch {
    return false;
  }
}

/*
 * Fullscreen, with the vendor fallback that still matters and the feature test
 * that matters more.
 *
 * Safari only gained unprefixed Element.requestFullscreen in 16.4, so the webkit
 * spelling is still load-bearing for this project — the household reads on
 * Safari daily, and an unprefixed-only call would silently do nothing there.
 * moz/ms are not included: Firefox and Edge have shipped the standard names for
 * years, and the classic reader's copies of them are dead weight.
 *
 * The test is the important half. iOS Safari on iPhone has NO element
 * fullscreen at all (only video), so the control is hidden there rather than
 * rendered as a button that does nothing.
 */
interface FsDoc extends Document {
  webkitFullscreenEnabled?: boolean;
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => void;
}
interface FsElement extends HTMLElement {
  webkitRequestFullscreen?: () => void;
}

function fullscreenSupported(): boolean {
  const d = document as FsDoc;
  return !!(d.fullscreenEnabled || d.webkitFullscreenEnabled);
}
function fullscreenElement(): Element | null {
  const d = document as FsDoc;
  return d.fullscreenElement ?? d.webkitFullscreenElement ?? null;
}

const FONT_MIN = 75;
const FONT_MAX = 200;
// #1318: how many times a failed position save is re-sent before the reader is
// told. Three attempts over ~14s covers the SQLite contention window that causes
// these; past that it is not transient and silence would be the wrong answer.
const MAX_SAVE_RETRIES = 3;
const LS_THEME = 'cwng.reader.theme';
const LS_FONT = 'cwng.reader.font';

const THEME_TO_READER: Record<ReaderSettings['theme'], ReaderTheme> = {
  lightTheme: 'light', sepiaTheme: 'sepia', darkTheme: 'dark', blackTheme: 'black',
};
const READER_TO_THEME: Record<ReaderTheme, ReaderSettings['theme']> = {
  light: 'lightTheme', sepia: 'sepiaTheme', dark: 'darkTheme', black: 'blackTheme',
};
const FONT_FAMILY: Record<ReaderSettings['font'], string> = {
  default: '', Yahei: 'Microsoft YaHei, sans-serif', SimSun: 'SimSun, serif',
  KaiTi: 'KaiTi, serif', Arial: 'Arial, sans-serif',
};

function loadTheme(): ReaderTheme {
  const v = safeLocalStorageGet(LS_THEME);
  if (v === 'light' || v === 'sepia' || v === 'dark' || v === 'black') return v;
  // First reader visit follows the already-resolved per-user app palette.
  // Thereafter the reader's explicit page-theme choice remains independent.
  const appTheme = document.documentElement.getAttribute('data-theme');
  if (appTheme === 'light') return 'light';
  if (appTheme === 'sepia') return 'sepia';
  return 'dark';
}
function loadFont(): number {
  const v = Number(safeLocalStorageGet(LS_FONT));
  return v >= FONT_MIN && v <= FONT_MAX ? v : 100;
}

export function Reader({ id }: { id: string }) {
  const t = useT();
  const announce = useAnnouncer();
  const { data: book, isLoading, error } = useBook(id);
  const { data: savedBookmark, isFetched: isBookmarkFetched } = useBookmark(id, 'epub');
  const { data: settingsData, isFetched: isSettingsFetched } = useReaderSettings();
  const saveBookmark = useSaveBookmark(id);
  const saveSettings = useSaveReaderSettings();

  const viewerRef = useRef<HTMLDivElement>(null);
  const tocRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLElement>(null);
  const searchFieldRef = useRef<HTMLInputElement>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  // The in-flight scan, so the next one can wait for it to finish touching the
  // book's shared spine items. See the search effect for why abort alone is not
  // enough.
  const searchRunRef = useRef<Promise<unknown> | null>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  // Edit/remove popover for an existing highlight (#782). Separate from popRef
  // (the create-color popover) so each has its own focus-trap lifecycle; the two
  // are mutually exclusive — opening one closes the other.
  const hlPopRef = useRef<HTMLDivElement>(null);
  // Note composer (#325). A third overlay with its own trap, mutually exclusive
  // with the two popovers above.
  const notePopRef = useRef<HTMLDivElement>(null);
  // Highlights & notes drawer (#325) — the in-reader counterpart to the
  // standalone Highlights page, which until now you had to leave the book for.
  const annRef = useRef<HTMLElement>(null);
  // The element that goes fullscreen: the whole reader, not just the book, so
  // the top bar and page-turn zones come with it.
  const shellRef = useRef<HTMLDivElement>(null);
  const noteFieldRef = useRef<HTMLTextAreaElement>(null);
  // annotation_id -> note_text for every highlight in this book. Seeded from
  // data.json on mount so a tapped highlight can show its note without a
  // round-trip, and kept in step with every create/edit/remove.
  const notesRef = useRef<Map<string, string>>(new Map());
  /* public_id -> label, from the same response as the rows. An OLDER backend
   * omits the envelope entirely, which is how a client can tell; an empty map
   * then means every row is simply unlabelled rather than wrong. */
  const [devices, setDevices] = useState<Record<string, { label?: string }>>({});
  const renditionRef = useRef<any>(null);
  const bookRef = useRef<any>(null);

  // Localized color names for highlight swatches + accessible labels.
  const colorLabel = (c: HiliteColor) =>
    ({ yellow: t('Yellow'), green: t('Green'), blue: t('Blue'), red: t('Red') })[c];
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const settingsSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const settingsPendingRef = useRef<Partial<ReaderSettings>>({});
  const lastCfiRef = useRef<string | null>(null);
  const lastPercentRef = useRef<number | null>(null);
  const saveRetries = useRef(0);
  // #1318 single-flight bookkeeping: one save in flight at a time, a coalesce
  // flag for relocations that happen during it, and a latch so a persistent
  // failure is announced once rather than on every page turn.
  const saveInFlight = useRef(false);
  const saveCoalesced = useRef(false);
  const saveFailureAnnounced = useRef(false);
  // Hold the freshest saved CFI so it survives re-renders without re-running the effect.
  const [remoteResume, setRemoteResume] = useState<{ cfi: string; percentage: number } | null>(null);
  const savedCfiRef = useRef<string | null>(null);
  /*
   * True while the book sits where a JUMP put it rather than where the reader
   * read to. Going to a saved highlight is "show me that passage", not "this is
   * my place now" — but epub.js cannot tell the two apart: `display()` reports a
   * relocation exactly like a page turn does, and the handler below used to
   * persist every one of them.
   *
   * Two things went wrong because of that. The mild one: tapping a highlight to
   * re-read it moved the reader's bookmark to the highlight. The severe one:
   * the same save carries a percentage, and the server finishes a book at
   * FINISHED_PERCENT_THRESHOLD (99.0, kosync.py) — so opening a highlight in a
   * book's last pages marked the whole book FINISHED and pushed that on to Kobo
   * sync and Hardcover. Un-finishing is guarded server-side; wrongly finishing
   * was not.
   *
   * Cleared by the reader's OWN navigation, deliberately not by the next
   * `relocated` event: one `display()` can report more than once while the
   * layout settles, and a fix keyed to the first report would quietly persist
   * the second. Keying on user intent instead means the flag holds however many
   * events a jump produces, and reading on from the destination still saves --
   * because at that point the reader really is reading there.
   */
  const previewingRef = useRef(false);

  const [rendered, setRendered] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  // #1303: true for a right-to-left book; swaps which screen side turns forward.
  const [rtl, setRtl] = useState(false);
  const [toc, setToc] = useState<TocItem[]>([]);
  const [tocOpen, setTocOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [searchTruncated, setSearchTruncated] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchComplete, setSearchComplete] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [annOpen, setAnnOpen] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  // Resolved once: whether this browser can do element fullscreen at all.
  const [canFullscreen] = useState(fullscreenSupported);
  // Every saved highlight for this book, kept in step locally on each write so
  // the drawer never needs a refetch to look right.
  const [annList, setAnnList] = useState<AnnRow[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [theme, setTheme] = useState<ReaderTheme>(loadTheme);
  const [fontPct, setFontPct] = useState(loadFont);
  const [fontFamily, setFontFamily] = useState<ReaderSettings['font']>('default');
  const [margin, setMargin] = useState(16);
  const [lineHeight, setLineHeight] = useState(150);
  /*
   * One column or two, persisted per user (#325).
   *
   * The value was ALREADY being stored — `spread` is part of ReaderSettings and
   * the classic reader has written it for years — but this reader hardcoded
   * epub.js's `spread: 'auto'` and never read it back. So a reader who chose
   * "One column" in the classic view had that preference silently ignored here.
   * This is less "add a control" than "stop discarding an answer we already had".
   *
   * 'nonespread' maps to epub.js 'none' (never two up); 'spread' maps to 'auto',
   * which is two-up only when the viewport is wide enough — so the setting stays
   * sane on a phone instead of forcing columns onto a 320px screen.
   */
  const [spread, setSpread] = useState<ReaderSettings['spread']>('spread');
  const [settingsHydrated, setSettingsHydrated] = useState(false);
  const [progress, setProgress] = useState(0);
  // Pending text selection awaiting a highlight-color choice.
  const [pendingSel, setPendingSel] = useState<{ cfiRange: string; text: string } | null>(null);
  // Existing highlight the user tapped — drives the edit/remove popover (#782).
  // `note` is the row's current note_text ('' when it has none).
  const [activeHl, setActiveHl] = useState<
    { cfiRange: string; id: string; color: string; note: string } | null>(null);
  // Open note composer (#325). 'create' writes the highlight and its note in a
  // single POST; 'edit' PATCHes note_text on a highlight that already exists.
  const [composer, setComposer] = useState<{
    /* 'standalone' is a note ABOUT the book with no passage: no CFI, no colour,
     * nothing to paint. It shares the composer because the writing experience is
     * the same; only what it commits to differs. */
    mode: 'create' | 'edit' | 'standalone';
    cfiRange: string;
    text: string;
    annotationId?: string;
    // A string, not HiliteColor: 'create' and 'standalone' seed it from the
    // four the palette offers, but 'edit' carries whatever the existing
    // highlight already is, which for an imported one can be pink or grey.
    color: string;
    note: string;
  } | null>(null);

  const epubFormat = book?.formats.find((f) => f.format.toLowerCase() === 'epub');
  const epubContentUrl = epubFormat
    ? getReaderContentUrl(id, epubFormat.format, epubFormat.content_url)
    : null;

  // C10: the TOC drawer and highlight popovers are overlays — trap focus while
  // open, restore on close, Escape closes (hooks run unconditionally every render).
  /*
   * Stable close handlers, one per overlay.
   *
   * NOT a tidiness change. useFocusTrap lists `onClose` in its effect's
   * dependencies, so an inline arrow — a new function identity on every render —
   * makes the trap re-run on EVERY RENDER of this component, not just when the
   * overlay opens. Each re-run re-focuses the dialog's first focusable and the
   * cleanup restores focus to the previous element.
   *
   * The visible symptom was in the note composer, whose first focusable is the
   * Cancel button in its header: the caret would leave the textarea and land on
   * Cancel whenever anything re-rendered the reader — a saved highlight arriving,
   * an annotation refresh, a keystroke in the controlled field. Measured as
   *     focusin TEXTAREA[Note] -> focusout TEXTAREA[Note] -> focusin BUTTON[Cancel]
   * It read as intermittent because it depended on whether a re-render happened
   * to follow the open.
   *
   * All six overlays had it; only the composer had a first focusable destructive
   * enough for anyone to notice.
   */
  const closeToc = useCallback(() => setTocOpen(false), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);
  const closePendingSel = useCallback(() => setPendingSel(null), []);
  const closeActiveHl = useCallback(() => setActiveHl(null), []);
  const closeComposer = useCallback(() => setComposer(null), []);
  const closeAnnDrawer = useCallback(() => setAnnOpen(false), []);
  const closeSearch = useCallback(() => {
    searchAbortRef.current?.abort();
    searchAbortRef.current = null;
    setSearching(false);
    setSearchOpen(false);
  }, []);

  useFocusTrap(tocRef, { onClose: closeToc, active: tocOpen });
  useFocusTrap(settingsRef, { onClose: closeSettings, active: settingsOpen });
  useFocusTrap(popRef, { onClose: closePendingSel, active: !!pendingSel });
  useFocusTrap(hlPopRef, { onClose: closeActiveHl, active: !!activeHl });
  useFocusTrap(notePopRef, { onClose: closeComposer, active: !!composer });
  useFocusTrap(annRef, { onClose: closeAnnDrawer, active: annOpen });
  useFocusTrap(searchRef, { onClose: closeSearch, active: searchOpen });

  // Declared after the trap registration: the trap first establishes the
  // drawer boundary, then this puts the caret where a search starts.
  useEffect(() => {
    if (searchOpen) searchFieldRef.current?.focus();
  }, [searchOpen]);

  /*
   * Land the caret in the note field when the composer opens, so a phone user
   * can type immediately instead of hunting for the textarea.
   *
   * Declared AFTER useFocusTrap(notePopRef, …) on purpose, and it has to stay
   * there: React runs effects in declaration order, and the trap focuses the
   * dialog's first focusable — which is the Cancel button in the header, since
   * in create mode the colour swatches also precede the field. Running after it
   * is what puts the caret in the right place.
   *
   * A callback ref cannot do this job: refs attach BEFORE any effect runs, so
   * the trap would simply overwrite it. Verified by a focus trace when it was
   * briefly written that way.
   *
   * The dependency list is the composer's identity rather than the object, so
   * closing (all three become undefined) and reopening on the same highlight
   * still re-runs this.
   */
  useEffect(() => {
    if (!composer) return;
    const field = noteFieldRef.current;
    if (!field) return;
    field.focus();
    field.setSelectionRange(field.value.length, field.value.length);
  }, [composer?.mode, composer?.annotationId, composer?.cfiRange]); // eslint-disable-line react-hooks/exhaustive-deps

  // Search only after typing settles. Every replacement, close and unmount
  // aborts the scan so an obsolete full-book walk cannot compete with the next.
  useEffect(() => {
    searchAbortRef.current?.abort();
    searchAbortRef.current = null;

    const query = searchQuery.trim();
    if (!searchOpen || query.length < MIN_QUERY_LENGTH) {
      setSearching(false);
      setSearchHits([]);
      setSearchTruncated(false);
      setSearchComplete(false);
      setSearchError(null);
      return;
    }

    setSearching(true);
    setSearchHits([]);
    setSearchTruncated(false);
    setSearchComplete(false);
    setSearchError(null);

    const timer = window.setTimeout(() => {
      const epubBook = bookRef.current;
      if (!epubBook) {
        setSearching(false);
        setSearchError(t('Could not search this book.'));
        return;
      }
      const controller = new AbortController();
      searchAbortRef.current = controller;
      /*
       * Wait for the previous scan to actually STOP before starting this one.
       *
       * Aborting is not stopping. The signal is only checked between sections,
       * so an aborted scan is still inside `section.load()`/`search()` and will
       * still run its `finally` -> `section.unload()`. Spine items belong to the
       * Book and are shared, not copied per scan, so that late unload can clear
       * the very document this scan is reading. searchBook treats the resulting
       * throw as one unreadable chapter and continues, which means the failure
       * is SILENTLY MISSING MATCHES -- the one failure a search must not have.
       *
       * Serialising costs one section's latency on a query the reader has
       * already stopped typing, and buys a correct result set.
       */
      const prior = searchRunRef.current;
      const run = (async () => {
        if (prior) await prior.catch(() => undefined);
        if (controller.signal.aborted) return;
        return searchBook(epubBook, query, { signal: controller.signal });
      })();
      searchRunRef.current = run;
      void run.then((outcome) => {
        if (controller.signal.aborted || !outcome) return;
        setSearchHits(outcome.hits);
        setSearchTruncated(outcome.truncated);
        setSearchComplete(true);
        setSearching(false);
      }).catch(() => {
        if (controller.signal.aborted) return;
        setSearchError(t('Could not search this book.'));
        setSearchComplete(true);
        setSearching(false);
      });
    }, 300);

    return () => {
      window.clearTimeout(timer);
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
    };
  }, [searchOpen, searchQuery, t]);

  useEffect(() => () => searchAbortRef.current?.abort(), []);

  // Open the edit/remove popover for a highlight the reader was tapped on (#782).
  // Closes the create-color popover so the two never show at once.
  const openHighlightEditor = useCallback((cfiRange: string, annotationId: string, color: string) => {
    setPendingSel(null);
    setComposer(null);
    setActiveHl({ cfiRange, id: annotationId, color, note: notesRef.current.get(annotationId) || '' });
  }, []);

  // Paint a highlight onto the live rendition (epub.js annotations API). The
  // data param stashes the server annotation id + color so the click callback
  // knows which row it represents; a real click callback (3rd arg) + 'cwng-hl'
  // className (4th arg) make tapping the highlight open the editor (#782).
  //
  // A highlight carrying a note is drawn with a dashed outline as well as the
  // fill (#325). epub.js paints into an SVG layer *inside the book iframe*, so
  // Reader.module.css cannot reach it — the distinction has to travel as inline
  // SVG attributes here. It is a shape difference, not a hue one, so it survives
  // SC 1.4.1 and reads on the light, sepia and dark page themes alike.
  const paintHighlight = useCallback((
    cfiRange: string, color: string, annotationId: string, hasNote = false,
  ) => {
    const fill = HILITE_FILL[color] || UNKNOWN_FILL;
    try {
      renditionRef.current?.annotations?.highlight(
        cfiRange,
        { id: annotationId, color, hasNote },
        () => openHighlightEditor(cfiRange, annotationId, color),
        // ONE token only: epub.js does classList.add(className), which throws
        // InvalidCharacterError on a space-separated list and then paints nothing.
        hasNote ? 'cwng-hl-noted' : 'cwng-hl',
        hasNote
          ? {
            fill, 'fill-opacity': '0.35', stroke: fill, 'stroke-width': '1.5',
            'stroke-dasharray': '3 2', 'stroke-opacity': '0.95',
          }
          : { fill, 'fill-opacity': '0.35' },
      );
    } catch { /* epub.js throws on a stale/foreign CFI — ignore */ }
  }, [openHighlightEditor]);

  // epub.js keys an annotation by (cfiRange + type), so changing how one is
  // drawn means removing the old paint and re-adding it.
  const repaintHighlight = useCallback((
    cfiRange: string, color: string, annotationId: string, hasNote: boolean,
  ) => {
    try { renditionRef.current?.annotations?.remove(cfiRange, 'highlight'); } catch { /* noop */ }
    paintHighlight(cfiRange, color, annotationId, hasNote);
  }, [paintHighlight]);

  // Create a highlight from the pending selection, persist it, paint it. The
  // create endpoint returns the new annotation row (incl. its id) — capture it
  // so the just-created highlight is immediately removable (#782).
  // Write a new highlight (optionally carrying a note) and paint it. Takes the
  // selection explicitly rather than reading `pendingSel`, because the note
  // composer clears that state before it saves.
  const persistHighlight = useCallback(async (
    cfiRange: string, text: string, color: string, note: string,
  ) => {
    try {
      // The create route answers with the stored row (`_data_json_row`), so the
      // listed row is built from what the SERVER recorded, falling back to what
      // we sent only where a field is absent. Two reasons. It keeps `source`
      // (and later the device fields) from being a client-side constant that
      // has to match the backend byte-for-byte to stay true. And it degrades
      // the safe way: absent-or-correct, never confidently wrong — so the
      // drawer cannot disagree with the Highlights page about a row both just
      // read from the same write.
      const created = await apiPost<Partial<AnnRow>>(`/annotations/${id}`, {
        cfi_range: cfiRange, highlighted_text: text, highlight_color: color,
        ...(note ? { note_text: note } : {}),
      }, { webreaderDevice: true });
      const newId = created?.annotation_id ?? '';
      if (note && newId) notesRef.current.set(newId, note);
      setAnnList((rows) => [...rows, {
        annotation_id: newId,
        cfi_range: created?.cfi_range ?? cfiRange,
        highlighted_text: created?.highlighted_text ?? text,
        note_text: created?.note_text ?? (note || null),
        highlight_color: created?.highlight_color ?? color,
        source: created?.source ?? 'webreader',
        // Prefer what the server stored. A selection-made highlight is 'cfi';
        // taking the response rather than assuming keeps this row identical to
        // the one a reload would fetch.
        position_type: created?.position_type ?? 'cfi',
      }]);
      paintHighlight(cfiRange, color, newId, !!note);
    } catch { /* surfaced as no-op; user can retry */ }
    try {
      (renditionRef.current?.getContents?.() || []).forEach((c: any) => c.window?.getSelection?.().removeAllRanges());
    } catch { /* noop */ }
  }, [id, paintHighlight]);

  const createHighlight = useCallback((color: string) => {
    const sel = pendingSel;
    if (!sel) return;
    setPendingSel(null);
    void persistHighlight(sel.cfiRange, sel.text, color, '');
  }, [pendingSel, persistHighlight]);

  // "Add note" on a fresh selection — hand the selection to the composer, which
  // creates the highlight and its note together on save.
  const startNoteForSelection = useCallback(() => {
    const sel = pendingSel;
    if (!sel) return;
    setPendingSel(null);
    setComposer({ mode: 'create', cfiRange: sel.cfiRange, text: sel.text, color: 'yellow', note: '' });
  }, [pendingSel]);

  /* Start a note that is not attached to anything. Opened from the drawer,
   * because that is where a reader's notes already live — asking them to select
   * text first would defeat the point. */
  const startStandaloneNote = useCallback(() => {
    setPendingSel(null);
    setActiveHl(null);
    setAnnOpen(false);
    setComposer({ mode: 'standalone', cfiRange: '', text: '', color: 'yellow', note: '' });
  }, []);

  // "Note" on an existing highlight — prefill from what we already hold.
  const startNoteForHighlight = useCallback(() => {
    const hl = activeHl;
    if (!hl) return;
    setActiveHl(null);
    setComposer({
      mode: 'edit', cfiRange: hl.cfiRange, text: '', annotationId: hl.id,
      // Carry the highlight's own colour through verbatim, even when it is one
      // the create palette does not offer (a Kobo pink or grey). Coercing it to
      // yellow here repainted an imported highlight yellow the moment its owner
      // opened the note composer. No swatch shows as pressed for such a colour,
      // which is correct — it is not one of the four on offer.
      color: hl.color,
      note: hl.note,
    });
  }, [activeHl]);

  // Single write of record for the composer, so Save and Remove note cannot
  // drift apart. Create mode POSTs highlight+note together; edit mode PATCHes
  // note_text (an empty string clears it). Silent on failure, as everywhere
  // else in this reader — the server stays the source of truth.
  const commitNote = useCallback(async (
    c: NonNullable<typeof composer>, rawNote: string,
  ) => {
    const note = rawNote.trim();
    setComposer(null);
    if (c.mode === 'standalone') {
      // An empty standalone note is nothing at all — the backend rejects it, and
      // silently discarding is kinder than an error for a field the reader
      // simply left blank.
      if (!note) return;
      try {
        const created = await apiPost<Partial<AnnRow>>(`/annotations/${id}`, {
          position_type: 'unanchored', note_text: note,
        }, { webreaderDevice: true });
        setAnnList((rows) => [...rows, {
          annotation_id: created?.annotation_id ?? '',
          cfi_range: null,
          highlighted_text: null,
          note_text: created?.note_text ?? note,
          highlight_color: null,
          source: created?.source ?? 'webreader',
          position_type: created?.position_type ?? 'unanchored',
        }]);
        announce(t('Note saved'));
      } catch { announce(t('Could not save that note.'), { assertive: true }); }
      return;
    }
    if (c.mode === 'create') {
      await persistHighlight(c.cfiRange, c.text, c.color, note);
      announce(note ? t('Note saved') : t('Highlight saved'));
      return;
    }
    if (!c.annotationId) return;
    try {
      await apiPatch(`/annotations/${id}/${c.annotationId}`, { note_text: note },
        { webreaderDevice: true });
      if (note) notesRef.current.set(c.annotationId, note);
      else notesRef.current.delete(c.annotationId);
      setAnnList((rows) => rows.map((r) =>
        r.annotation_id === c.annotationId ? { ...r, note_text: note || null } : r));
      repaintHighlight(c.cfiRange, c.color, c.annotationId, !!note);
      announce(note ? t('Note saved') : t('Note removed'));
    } catch { /* silent: server keeps the previous note */ }
  }, [id, persistHighlight, repaintHighlight, announce, t]);

  /*
   * Jump the book to a saved highlight.
   *
   * Only web-origin rows are guaranteed a portable CFI. Device rows carry
   * Kobo-native anchors and get a CFI derived server-side only when the kepub is
   * on disk, and a re-generated kepub can shift the KoboSpan ids those were
   * computed from — so a stale or absent CFI is expected here, not exceptional.
   * epub.js throws on one; the row stays listed and readable either way, which
   * is better than hiding a highlight because we cannot navigate to it.
   */
  const goToAnnotation = useCallback((row: AnnRow) => {
    if (!row.cfi_range) return;
    setAnnOpen(false);
    // Set BEFORE display(): epub.js can report the relocation synchronously, so
    // arming the flag afterwards would arm it too late to suppress anything.
    previewingRef.current = true;
    try {
      Promise.resolve(renditionRef.current?.display(row.cfi_range)).catch(() => {
        // The jump never happened, so the book is still where the reader left
        // it. Disarm, or the next real page turn would be swallowed as if it
        // were part of a preview.
        previewingRef.current = false;
        announce(t('Could not open that highlight.'));
      });
    } catch {
      previewingRef.current = false;
      announce(t('Could not open that highlight.'));
    }
  }, [announce, t]);

  const goToSearchResult = useCallback((cfi: string) => {
    closeSearch();
    /*
     * A search hit is a preview, exactly like a highlight jump, and arms the
     * same flag -- see previewingRef.
     *
     * This is the composition that does NOT come for free. The two changes
     * merged with no conflict: the preview flag landed on `goToAnnotation`, and
     * search arrived as its own `display()` caller that the flag had never heard
     * of. Nothing was overwritten and nothing was reported, so search would have
     * shipped the same defect the flag exists to prevent -- looking up a word
     * near the end of a book would move the reader's place there and, past 99%,
     * mark the book finished.
     */
    previewingRef.current = true;
    try {
      Promise.resolve(renditionRef.current?.display(cfi)).catch(() => {
        previewingRef.current = false;
        announce(t('Could not open that search result.'));
      });
    } catch {
      previewingRef.current = false;
      announce(t('Could not open that search result.'));
    }
  }, [announce, closeSearch, t]);

  const toggleFullscreen = useCallback(() => {
    const d = document as FsDoc;
    if (fullscreenElement()) {
      (d.exitFullscreen ? d.exitFullscreen() : d.webkitExitFullscreen?.());
      return;
    }
    const el = shellRef.current as FsElement | null;
    if (!el) return;
    // Rejected promises are normal here (a user gesture requirement, or an
    // embedder policy); the button simply does nothing rather than throwing.
    try {
      const req = el.requestFullscreen?.bind(el) || el.webkitRequestFullscreen?.bind(el);
      const r = req?.();
      if (r && typeof (r as Promise<void>).catch === 'function') (r as Promise<void>).catch(() => {});
    } catch { /* unsupported or refused — leave the reader as it is */ }
  }, []);

  // The browser owns this state: Escape and the system chrome can leave
  // fullscreen without touching our button, so follow the event rather than
  // assuming our own toggle is the only way out.
  useEffect(() => {
    const sync = () => setIsFullscreen(!!fullscreenElement());
    document.addEventListener('fullscreenchange', sync);
    document.addEventListener('webkitfullscreenchange', sync);
    sync();
    return () => {
      document.removeEventListener('fullscreenchange', sync);
      document.removeEventListener('webkitfullscreenchange', sync);
    };
  }, []);

  const saveNote = useCallback(() => {
    if (composer) void commitNote(composer, composer.note);
  }, [composer, commitNote]);

  const removeNote = useCallback(() => {
    if (composer) void commitNote(composer, '');
  }, [composer, commitNote]);

  // Remove the tapped highlight server-side, then un-paint it (#782). Fails
  // silently (the reader has no toast) and leaves the highlight painted on
  // error — the row is still on the server, so keeping it painted stays honest.
  const removeHighlight = useCallback(async () => {
    const hl = activeHl;
    if (!hl) return;
    setActiveHl(null);
    try {
      await apiDelete(`/annotations/${id}/${hl.id}`, { webreaderDevice: true });
      notesRef.current.delete(hl.id);
      setAnnList((rows) => rows.filter((r) => r.annotation_id !== hl.id));
      try { renditionRef.current?.annotations?.remove(hl.cfiRange, 'highlight'); } catch { /* noop */ }
    } catch { /* silent: keep the highlight painted */ }
  }, [activeHl, id]);

  // Recolor the tapped highlight (PATCH supports highlight_color). epub.js keys
  // an annotation by (cfiRange + type), so a new color is applied by removing
  // the old paint and re-adding with the new fill. Silent on error (old paint
  // is untouched, server keeps the prior color).
  const recolorHighlight = useCallback(async (color: string) => {
    const hl = activeHl;
    if (!hl) return;
    setActiveHl(null);
    if (hl.color === color) return;
    try {
      await apiPatch(`/annotations/${id}/${hl.id}`, { highlight_color: color },
        { webreaderDevice: true });
      setAnnList((rows) => rows.map((r) =>
        r.annotation_id === hl.id ? { ...r, highlight_color: color } : r));
      // Recolouring must not silently drop the note marker.
      repaintHighlight(hl.cfiRange, color, hl.id, !!notesRef.current.get(hl.id));
    } catch { /* silent: keep the highlight in its original color */ }
  }, [activeHl, id, repaintHighlight]);

  useEffect(() => {
    savedCfiRef.current = savedBookmark?.bookmark ?? savedCfiRef.current;
  }, [savedBookmark]);

  useEffect(() => {
    // Wait for the query to settle, not for it to succeed. A guest has no
    // server-side reader settings — /api/v1/reader/settings answers 401 for an
    // anonymous user by design — so gating hydration on a payload left the
    // guest's reader waiting forever behind the render guard below (#1074).
    // Settled-with-nothing is a real answer: boot on the defaults already in
    // state, which is what the guest reader is supposed to use.
    if (!isSettingsFetched) return;
    const settings = settingsData?.reader;
    if (settings) {
      setTheme(THEME_TO_READER[settings.theme]);
      setFontPct(settings.fontSize);
      setFontFamily(settings.font);
      setMargin(settings.margin);
      setLineHeight(settings.lineHeight);
      if (settings.spread) setSpread(settings.spread);
    }
    // Start epub.js only on the next render, after this server snapshot has
    // become the state captured by the rendition callbacks.
    setSettingsHydrated(true);
  }, [settingsData, isSettingsFetched]);

  const persistSetting = useCallback(<K extends keyof ReaderSettings>(key: K, value: ReaderSettings[K]) => {
    settingsPendingRef.current = { ...settingsPendingRef.current, [key]: value };
    if (settingsSaveTimer.current) clearTimeout(settingsSaveTimer.current);
    settingsSaveTimer.current = setTimeout(() => {
      const patch = settingsPendingRef.current;
      settingsPendingRef.current = {};
      settingsSaveTimer.current = null;
      saveSettings.mutate(patch, {
        onSuccess: () => announce(t('Reader settings saved.')),
        onError: () => announce(t('Could not save reader settings.'), { assertive: true }),
      });
    }, 300);
  }, [saveSettings, announce, t]);

  // #1318: saving the position is SINGLE-FLIGHT, and every send reads the refs
  // at the moment it goes out.
  //
  // The route now reports a write that did not land, which makes re-sending
  // worthwhile — SQLite contention is exactly what a second attempt clears. But
  // this route is replace-on-write and its CFI has no ordering or compare-and-set
  // protection, so two saves in flight at once can land in the wrong order and
  // move the reader BACKWARDS. That is reachable without any retry (the 800ms
  // debounce only cancels a save that has not started yet) and a retry would
  // make it commonplace: under a lock held near SQLite's 30s busy timeout, a
  // reader turning pages would pile up dozens of waiting writes.
  //
  // So at most one request exists at a time. A relocation during a send does not
  // queue a second request; it just updates the refs, and the coalesced follow-up
  // that runs on settle picks up wherever the reader has got to by then. One
  // request, always carrying the newest position, is both correct and less work.
  const flushCfiSave = useCallback(() => {
    if (saveInFlight.current) { saveCoalesced.current = true; return; }
    const cfi = lastCfiRef.current;
    if (!cfi) return;
    const pct = lastPercentRef.current;
    saveInFlight.current = true;
    // mutateAsync, not mutate(…, {onError}): per-call callbacks only fire for the
    // latest observed mutation and are dropped entirely if the reader unmounts
    // first, so the retry bookkeeping would silently stop happening.
    saveBookmark.mutateAsync(
      pct != null ? { format: 'epub', bookmark: cfi, percentage: pct }
                  : { format: 'epub', bookmark: cfi },
    ).then(() => {
      saveRetries.current = 0;
      saveFailureAnnounced.current = false;
    }).catch((error: unknown) => {
      if (isWorthResending(error) && saveRetries.current < MAX_SAVE_RETRIES) {
        saveRetries.current += 1;
        if (saveTimer.current) clearTimeout(saveTimer.current);
        saveTimer.current = setTimeout(
          flushCfiSave, Math.min(1000 * 2 ** saveRetries.current, 8000));
        return;
      }
      // Out of attempts, or a refusal re-sending cannot change. Tell the reader
      // — this is the only moment they would otherwise never find out — but only
      // on the transition into a bad state. Assertive announcements repeat even
      // when identical, so announcing per failed page turn would talk over a
      // screen-reader user continuously. The latch clears on the next success.
      saveRetries.current = 0;
      if (!saveFailureAnnounced.current) {
        saveFailureAnnounced.current = true;
        announce(t('Could not save your reading position.'), { assertive: true });
      }
    }).finally(() => {
      saveInFlight.current = false;
      if (saveCoalesced.current) {
        saveCoalesced.current = false;
        flushCfiSave();
      }
    });
  }, [saveBookmark, announce, t]);

  const persistCfi = useCallback(
    (cfi: string, percentage?: number) => {
      lastCfiRef.current = cfi;
      // #324: the CFI is private to this reader; the percentage is what the
      // server can share with the user's Kobo and the book-detail row.
      //
      // The percentage belongs to THIS cfi, so it is never sticky: a relocation
      // that cannot produce one (locations not generated yet, or a genuine 0%)
      // CLEARS the ref rather than leaving the previous value behind. Carrying
      // it forward would post a position the user is not at — and, across a
      // book change, would post the previous book's percentage under this
      // book's id, which the server would accept as real cross-device progress.
      const valid = typeof percentage === 'number' && Number.isFinite(percentage) && percentage > 0
        ? percentage
        : null;
      lastPercentRef.current = valid;
      // A fresh position supersedes any pending retry: the newest place is what
      // we want on the server, and it resets the attempt budget.
      saveRetries.current = 0;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        saveTimer.current = null;
        flushCfiSave();
      }, 800);
    },
    [flushCfiSave],
  );

  const applyTheme = useCallback((t: ReaderTheme) => {
    const rendition = renditionRef.current;
    if (!rendition) return;
    // Select the registered theme so future (page-turn) sections paint correctly…
    rendition.themes.select(t);
    // …and force it onto the currently-rendered iframe with inline styles, which
    // win unconditionally. epub.js can skip re-applying a theme it considers
    // already current (notably the initial 'dark'), leaving the prior background.
    const bg = THEMES[t].body.background.replace(' !important', '');
    const fg = THEMES[t].body.color.replace(' !important', '');
    // epub.js injects several equal-specificity `!important` body rules per theme;
    // the LAST one appended wins, so a previously-selected light/sepia rule beats
    // dark on re-select. An `!important` INLINE style sits above every stylesheet
    // rule in the cascade — set it with priority so the chosen theme always wins.
    try {
      (rendition.getContents?.() || []).forEach((c: any) => {
        if (!c?.document) return;
        c.document.documentElement.style.setProperty('background', bg, 'important');
        if (c.document.body) {
          c.document.body.style.setProperty('background', bg, 'important');
          c.document.body.style.setProperty('color', fg, 'important');
        }
      });
    } catch { /* same-origin blob content; guard regardless */ }
  }, []);

  const applyTypography = useCallback(() => {
    const rendition = renditionRef.current;
    if (!rendition) return;
    rendition.themes.fontSize(`${fontPct}%`);
    if (fontFamily === 'default') rendition.themes.font('initial');
    else rendition.themes.font(FONT_FAMILY[fontFamily]);
    try {
      (rendition.getContents?.() || []).forEach((c: any) => {
        if (!c?.document?.body) return;
        c.document.body.style.setProperty('padding-inline', `${margin}px`, 'important');
        c.document.body.style.setProperty('line-height', String(lineHeight / 100), 'important');
      });
    } catch { /* same-origin blob content; guard regardless */ }
  }, [fontPct, fontFamily, margin, lineHeight]);

  // A page turn is the reader moving themselves, so it ends any preview: from
  // here on the relocations are theirs and the position saves again. This is the
  // ONLY thing that clears the flag — see previewingRef's note.
  const goPrev = useCallback(() => { setRemoteResume(null); previewingRef.current = false; return renditionRef.current?.prev(); }, []);
  const goNext = useCallback(() => { setRemoteResume(null); previewingRef.current = false; return renditionRef.current?.next(); }, []);

  // Which way the page physically turns. In an RTL book the left of the screen
  // is forward, so the left zone advances and the right zone goes back. Labels
  // travel with the action, not with the side, or a screen reader would
  // announce the opposite of what the button does.
  //
  // Memoized on purpose: these are dependencies of the arrow-key effect below,
  // and a fresh identity each render would re-run it on every render rather
  // than only when the direction changes.
  const goLeft = useCallback(() => (rtl ? goNext() : goPrev()), [rtl, goNext, goPrev]);
  const goRight = useCallback(() => (rtl ? goPrev() : goNext()), [rtl, goPrev, goNext]);
  const leftLabel = rtl ? t('Next page') : t('Previous page');
  const rightLabel = rtl ? t('Previous page') : t('Next page');

  // Build the rendition once the epub format + its download URL are known.
  useEffect(() => {
    if (!epubFormat || !epubContentUrl || !viewerRef.current || !isBookmarkFetched || !isSettingsFetched || !settingsHydrated) return;
    let cancelled = false;
    setRendered(false);
    setRenderError(null);
    // Clear rather than carry: wouter reuses this component across an :id
    // change, so a stale RTL flag would invert the next book's page turns.
    setRtl(false);

    (async () => {
      try {
        // Fetch the .epub ourselves (same-origin cookie auth) and hand epub.js
        // an ArrayBuffer — reliable archive open regardless of the URL extension.
        const res = await fetch(resourceUrl(epubContentUrl), { credentials: 'include' });
        if (!res.ok) throw new Error(t('Could not load the book file ({status})', { status: res.status }));
        const buf = await res.arrayBuffer();
        if (cancelled) return;

        const epubBook = ePub(buf as any);
        bookRef.current = epubBook;
        const rendition = epubBook.renderTo(viewerRef.current!, {
          width: '100%',
          height: '100%',
          flow: 'paginated',
          spread: spread === 'nonespread' ? 'none' : 'auto',
        });
        renditionRef.current = rendition;

        Object.entries(THEMES).forEach(([name, t]) => rendition.themes.register(name, t));
        rendition.themes.select(theme);
        rendition.themes.fontSize(`${fontPct}%`);

        // C10 (SC 4.1.2): epub.js renders each section into an <iframe> with no
        // title — screen readers announce "frame" with no name. Title them as
        // they render so the book content region is named.
        rendition.on('rendered', () => {
          viewerRef.current?.querySelectorAll('iframe').forEach((f) => {
            f.setAttribute('title', t('Book content'));
          });
          applyTheme(theme);
          applyTypography();
        });

        setRemoteResume(null);
        // Only a book with no local CFI waits for locations before first display.
        // Cache the promise so automatic resume never generates the index twice.
        let readerMoved = false;
        let locationsReady: Promise<unknown> | undefined;
        const generateLocations = () => locationsReady ??= epubBook.ready
          .then(() => epubBook.locations.generate(1600));
        const resume = savedBookmark?.resume;
        // epub.js may emit delayed layout relocations after display resolves.
        // Treat opening with a synced hint as a preview until a real page turn,
        // or those layout events would stamp the local CFI newer than the hint.
        previewingRef.current = !!resume;
        let initialTarget = savedCfiRef.current || undefined;
        if (!initialTarget && resume?.mode === 'automatic') {
          try {
            await generateLocations();
            if (cancelled) return;
            initialTarget = resumeCfi(epubBook.locations, resume);
          } catch { /* Keep opening the book when its index cannot be generated. */ }
        }
        await rendition.display(initialTarget);
        if (cancelled) return;
        if (!savedCfiRef.current && initialTarget && resume?.mode === 'automatic') {
          // The rendered hook applies typography, which can reflow the target
          // out of the initial spread. Place it again after that layout frame.
          await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
          if (cancelled) return;
          await rendition.display(initialTarget);
        }
        if (cancelled) return;
        // display() resolves only after the package document is parsed, so the
        // spine's page-progression-direction is readable by here.
        setRtl(isRtlBook(epubBook));
        setRendered(true);

        epubBook.loaded.navigation.then((nav: any) => {
          if (!cancelled) {
            setToc(nav.toc.map((t: any) => ({ label: (t.label || '').trim(), href: t.href })));
          }
        });

        // Lazily generate locations for a progress percentage.
        generateLocations()
          .then(() => {
            if (cancelled) return;
            if (!readerMoved && savedCfiRef.current && resume?.mode === 'offer') {
              const cfi = resumeCfi(epubBook.locations, resume);
              if (cfi) setRemoteResume({ cfi, percentage: resume.percentage });
            }
            const loc = rendition.currentLocation() as any;
            if (loc?.start?.cfi && epubBook.locations.length()) {
              setProgress(Math.round(epubBook.locations.percentageFromCfi(loc.start.cfi) * 100));
            }
          })
          .catch(() => {/* locations are best-effort */});

        rendition.on('relocated', (location: any) => {
          const cfi = location?.start?.cfi;
          if (!cfi) return;
          // Locations must exist for percentageFromCfi to mean anything; without
          // them the position still saves, just without the shareable percentage.
          // Sync the UNROUNDED value: the server marks a book finished at >= 99%,
          // so rounding first would finish a book for a reader at 98.5%.
          // Rounding stays a display concern.
          const exact = epubBook.locations.length()
            ? epubBook.locations.percentageFromCfi(cfi) * 100
            : undefined;
          // A previewed jump still MOVED the book, so the progress readout
          // tracks it — that number describes where the reader is looking, and
          // showing the old one would be a lie about the page on screen. What
          // it must not do is SAVE: the bookmark, the synced percentage and the
          // finished-at-99% decision all hang off persistCfi.
          if (!previewingRef.current) {
            readerMoved = true;
            setRemoteResume(null);
            persistCfi(cfi, exact);
          }
          if (exact !== undefined) setProgress(Math.round(exact));
        });

        // Render existing highlights (the CFI-anchored ones we can place). Each
        // row carries its server annotation_id; paintHighlight stashes it in the
        // epub.js data param so a later tap can target the right row (#782).
        fetch(apiUrl(`/annotations/${id}/data.json`), { credentials: 'include' })
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => {
            if (cancelled || !d) return;
            notesRef.current.clear();
            /*
             * Take the rows and the device map from the SAME response, and take
             * every row regardless of whether its device resolves.
             *
             * This is an outer join on purpose. On a real library only a
             * minority of rows carry attribution at all — measured 3 of 14 on
             * the household instance, the rest predating the feature — so
             * filtering on resolution would empty most of the drawer to add a
             * label. An unresolved id renders as no label; it never removes a
             * highlight the reader made.
             */
            setDevices((d.devices || {}) as Record<string, { label?: string }>);
            setAnnList((d.annotations || []) as AnnRow[]);
            (d.annotations || []).forEach((a: any) => {
              const note = (a.note_text || '').trim();
              if (note && a.annotation_id) notesRef.current.set(a.annotation_id, note);
              if (a.cfi_range) {
                paintHighlight(a.cfi_range, a.highlight_color ?? '', a.annotation_id, !!note);
              }
            });
          })
          .catch(() => { /* highlights are best-effort */ });

        // Capture a text selection → offer a highlight-color popover.
        rendition.on('selected', (cfiRange: string, contents: any) => {
          let text = '';
          try { text = (contents?.window?.getSelection?.().toString() || '').trim(); } catch { /* noop */ }
          if (cfiRange) {
            setActiveHl(null);
            setPendingSel({ cfiRange, text });
          }
        });
      } catch (e) {
        if (!cancelled) setRenderError(e instanceof Error ? e.message : t('Failed to open the book.'));
      }
    })();

    return () => {
      cancelled = true;
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
        const cfi = lastCfiRef.current;
        if (cfi) {
          const pct = lastPercentRef.current;
          void apiPost(
            `/api/v1/books/${id}/bookmark`,
            pct != null ? { format: 'epub', bookmark: cfi, percentage: pct }
                        : { format: 'epub', bookmark: cfi },
            { keepalive: true, webreaderDevice: true },
          );
        }
      }
      try { renditionRef.current?.destroy(); } catch { /* noop */ }
      try { bookRef.current?.destroy(); } catch { /* noop */ }
      renditionRef.current = null;
      bookRef.current = null;
    };
    // Re-render only when the source changes; theme/font are applied imperatively.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [epubContentUrl, isBookmarkFetched, isSettingsFetched, settingsHydrated]);

  // Apply theme / font changes to a live rendition without rebuilding it, and
  // remember the preference across sessions.
  useEffect(() => {
    safeLocalStorageSet(LS_THEME, theme);
    applyTheme(theme);
  }, [theme, applyTheme]);
  useEffect(() => {
    safeLocalStorageSet(LS_FONT, String(fontPct));
    applyTypography();
  }, [fontPct, fontFamily, margin, lineHeight, applyTypography]);

  // Arrow-key navigation (the iframe also forwards keys via rendition).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Arrow keys follow the same physical convention as the zones: left is
      // forward in an RTL book.
      if (e.key === 'ArrowLeft') goLeft();
      if (e.key === 'ArrowRight') goRight();
    };
    const rendition = renditionRef.current;
    document.addEventListener('keyup', onKey);
    rendition?.on('keyup', onKey);
    return () => {
      document.removeEventListener('keyup', onKey);
      // The rendition registration was previously never undone, so each re-run
      // left another handler attached to it. Harmless while this effect ran
      // once or twice; balancing it keeps that true as the deps change.
      rendition?.off('keyup', onKey);
    };
  }, [goLeft, goRight, rendered]);

  const goToc = (href: string) => {
    // Choosing a chapter is an explicit reading move, like a page turn.
    previewingRef.current = false;
    setRemoteResume(null);
    const rendition = renditionRef.current;
    const epubBook = bookRef.current;
    setTocOpen(false);
    if (!rendition) return;
    // Resolve the TOC href to a spine section first: epub.js's display(href) can
    // throw "No Section Found" when the toc href and spine href bases differ
    // (common when opening from an ArrayBuffer). spine.get() matches by href/id/
    // index and is robust; fall back to the raw href (sans fragment) if needed.
    let target: string | number = href;
    try {
      const section = epubBook?.spine?.get(href);
      if (section && typeof section.index === 'number') target = section.index;
    } catch { /* fall through to href */ }
    Promise.resolve(rendition.display(target)).catch(() => {
      Promise.resolve(rendition.display(href.split('#')[0])).catch(() => {/* give up quietly */});
    });
  };

  if (isLoading) {
    return (
      <div className={styles.fullCenter}>
        <span className={styles.spin}><Loader2 size={36} /></span>
      </div>
    );
  }

  if (error || !book) {
    return (
      <div className={styles.fullCenter}>
        <EmptyState message={error instanceof Error ? error.message : t('Book not found.')} />
        <Link href="/" className={styles.exitLink}>{t('← Library')}</Link>
      </div>
    );
  }

  if (!epubFormat) {
    // No epub format — fall back to the legacy reader for other formats.
    const other = book.formats[0];
    return (
      <div className={styles.fullCenter}>
        <EmptyState message={t('In-browser reading currently supports EPUB. Use download or the classic reader for other formats.')} />
        <div className={styles.fallbackRow}>
          {other && <a className={styles.exitLink} href={resourceUrl(other.read_url)}>{t('Open classic reader')}</a>}
          <Link href={`/book/${id}`} className={styles.exitLink}>{t('← Back to book')}</Link>
        </div>
      </div>
    );
  }

  return (
    <div ref={shellRef} className={`${styles.reader} ${styles[`bg_${theme}`]}`}>
      {/* Top bar */}
      <header className={styles.bar}>
        {/* Page heading for the reader view (SC 1.3.1), visually the bar title. */}
        <VisuallyHidden as="h1">{book.title}</VisuallyHidden>
        <Link href={`/book/${id}`} className={styles.iconBtn} title={t('Close reader')} aria-label={t('Close reader')}>
          <X size={20} aria-hidden="true" focusable={false} />
        </Link>
        <span className={styles.bookTitle} aria-hidden="true">{book.title}</span>
        <div className={styles.barControls}>
          <button className={styles.iconBtn} onClick={() => { closeSearch(); setTocOpen((o) => !o); }}
            aria-label={t('Table of contents')} aria-expanded={tocOpen} title={t('Contents')}>
            <List size={19} aria-hidden="true" focusable={false} />
          </button>
          <button className={styles.iconBtn} onClick={() => {
            setTocOpen(false); closeSearch(); setAnnOpen((o) => !o);
          }}
            aria-label={t('Highlights and notes')} aria-expanded={annOpen} title={t('Highlights and notes')}>
            <Highlighter size={19} aria-hidden="true" focusable={false} />
            {annList.length > 0 && (
              <span className={styles.annCount} aria-hidden="true">{annList.length}</span>
            )}
          </button>
          <button className={styles.iconBtn} onClick={() => {
            setTocOpen(false); setAnnOpen(false); setSettingsOpen(false); setSearchOpen(true);
          }} aria-label={t('Search inside book')} aria-expanded={searchOpen} title={t('Search inside book')}>
            <Search size={19} aria-hidden="true" focusable={false} />
          </button>
          {canFullscreen && (
            <button className={styles.iconBtn} onClick={toggleFullscreen}
              aria-label={isFullscreen ? t('Exit full screen') : t('Full screen')}
              aria-pressed={isFullscreen}
              title={isFullscreen ? t('Exit full screen') : t('Full screen')}>
              {isFullscreen
                ? <Minimize size={19} aria-hidden="true" focusable={false} />
                : <Maximize size={19} aria-hidden="true" focusable={false} />}
            </button>
          )}
          <button className={styles.iconBtn} onClick={() => { closeSearch(); setSettingsOpen((o) => !o); }}
            aria-label={t('Reading appearance')} aria-expanded={settingsOpen} title={t('Reading appearance')}>
            <SlidersHorizontal size={19} aria-hidden="true" focusable={false} />
          </button>
        </div>
      </header>

      {/* TOC drawer */}
      {tocOpen && (
        <>
          <div className={styles.tocScrim} onClick={() => setTocOpen(false)} aria-hidden="true" />
          <nav ref={tocRef} className={styles.toc} aria-label={t('Table of contents')} tabIndex={-1}>
            <div className={styles.panelHeading}>
              <p className={styles.tocHeading}>{t('Contents')}</p>
              <button className={styles.iconBtn} onClick={() => setTocOpen(false)} aria-label={t('Close')}>
                <X size={18} aria-hidden="true" focusable={false} />
              </button>
            </div>
            {toc.length === 0 ? (
              <p className={styles.tocEmpty}>{t('No contents found.')}</p>
            ) : (
              <ul role="list">
                {toc.map((tocItem, i) => (
                  <li key={`${tocItem.href}-${i}`}>
                    <button className={styles.tocItem} onClick={() => goToc(tocItem.href)}>{tocItem.label || t('Untitled')}</button>
                  </li>
                ))}
              </ul>
            )}
          </nav>
        </>
      )}

      {remoteResume && (
        <div className={styles.resumeNotice} role="status">
          <button onClick={() => {
            previewingRef.current = true;
            const target = remoteResume.cfi;
            setRemoteResume(null);
            Promise.resolve(renditionRef.current?.display(target)).catch(() => {
              previewingRef.current = false;
              announce(t('Could not open the synced position.'));
            });
          }}>{t('Resume at {percent} from another device', { percent: `${Math.round(remoteResume.percentage)}%` })}</button>
          <button onClick={() => setRemoteResume(null)} aria-label={t('Dismiss')}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>
      )}

      {/* Full-book search drawer. The result excerpts are book-controlled text,
          so matching is rendered as React text + <mark>, never injected HTML. */}
      {searchOpen && (
        <>
          <div className={styles.tocScrim} onClick={closeSearch} aria-hidden="true" />
          <nav ref={searchRef} className={styles.toc} aria-label={t('Search this book')} tabIndex={-1}>
            <div className={styles.panelHeading}>
              <p className={styles.tocHeading}>{t('Search this book')}</p>
              <button className={styles.iconBtn} onClick={closeSearch} aria-label={t('Close')}>
                <X size={18} aria-hidden="true" focusable={false} />
              </button>
            </div>
            <label className={styles.searchField} htmlFor="reader-book-search">
              <span>{t('Search term')}</span>
              <input ref={searchFieldRef} id="reader-book-search" type="search"
                value={searchQuery} placeholder={t('Enter at least 2 characters')}
                onChange={(event) => setSearchQuery(event.target.value)} />
            </label>
            <p className={styles.searchStatus} role="status">
              {searching ? (
                <><span className={styles.spin}><Loader2 size={16} aria-hidden="true" focusable={false} /></span>
                  {t('Searching this book…')}</>
              ) : searchError ? searchError
                : searchComplete && searchTruncated
                  ? t('Showing the first {count} matches.', { count: Math.min(DEFAULT_HIT_CAP, searchHits.length) })
                  : searchComplete ? t('{count} matches', { count: searchHits.length }) : ''}
            </p>
            {searchComplete && searchHits.length === 0 && !searchError ? (
              <p className={styles.tocEmpty}>{t('No matches found in this book.')}</p>
            ) : searchHits.length > 0 ? (
              <ul className={styles.searchResults} role="list">
                {searchHits.map((hit, index) => (
                  <li key={`${hit.cfi}-${index}`}>
                    <button className={styles.searchResult} onClick={() => goToSearchResult(hit.cfi)}>
                      <span className={styles.searchChapter}>
                        {chapterLabelForHref(hit.href, toc)}
                      </span>
                      <span className={styles.searchExcerpt}>
                        {splitSearchExcerpt(hit.excerpt, searchQuery).map((part, partIndex) =>
                          part.matched
                            ? <mark key={partIndex}>{part.text}</mark>
                            : <span key={partIndex}>{part.text}</span>)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </nav>
        </>
      )}

      {/* Highlights & notes drawer (#325). Mirrors the TOC drawer's shape so the
          two read as siblings; jumping closes it, as picking a chapter does. */}
      {annOpen && (
        <>
          <div className={styles.tocScrim} onClick={() => setAnnOpen(false)} aria-hidden="true" />
          <nav ref={annRef} className={styles.toc} aria-label={t('Highlights and notes')} tabIndex={-1}>
            <div className={styles.panelHeading}>
              <p className={styles.tocHeading}>{t('Highlights and notes')}</p>
              <button className={styles.iconBtn} onClick={() => setAnnOpen(false)} aria-label={t('Close')}>
                <X size={18} aria-hidden="true" focusable={false} />
              </button>
            </div>
            {/* A note about the book needs no selection, so the way in belongs
                here rather than behind a text selection the reader has not made. */}
            <button className={styles.annNewNote} onClick={startStandaloneNote}>
              <StickyNote size={14} aria-hidden="true" focusable={false} />
              {t('Write a note')}
            </button>
            {annList.length === 0 ? (
              <p className={styles.tocEmpty}>
                {t('No highlights yet. Select text in the book to make one.')}
              </p>
            ) : (
              <ul role="list">
                {annList.map((row) => {
                  const colour = HILITE_FILL[row.highlight_color ?? ''] ?? UNKNOWN_FILL;
                  /*
                   * A standalone note is a note ABOUT the book, with no passage
                   * attached — a deliberate state, not a broken highlight.
                   * Without this it renders as a defective one: the jump greyed
                   * out with "This highlight has no saved position", and
                   * "(no text captured)" where the quote goes. Both sentences
                   * are true of a highlight whose anchor was destroyed and
                   * false of a note that never had one, and the reader cannot
                   * tell the difference from the row.
                   */
                  const unanchored = row.position_type === 'unanchored';
                  const jumpable = !unanchored && !!row.cfi_range;
                  return (
                    <li key={row.annotation_id} className={styles.annItem}>
                      <button
                        className={styles.annJump}
                        onClick={() => goToAnnotation(row)}
                        disabled={!jumpable}
                        title={jumpable ? t('Go to this highlight')
                          : unanchored ? t('A note about the book, not tied to a passage')
                          : t('This highlight has no saved position')}
                      >
                        {/* No colour bar on an unanchored note: there is no
                            highlighted passage for a colour to refer to. */}
                        {!unanchored && (
                          <span className={styles.annBar} style={{ background: colour }} aria-hidden="true" />
                        )}
                        <span className={styles.annBody}>
                          {!unanchored && (
                            <span className={styles.annQuote}>
                              {row.highlighted_text || t('(no text captured)')}
                            </span>
                          )}
                          {row.note_text && (
                            <span className={styles.annNote}>
                              <StickyNote size={12} aria-hidden="true" focusable={false} />
                              {row.note_text}
                            </span>
                          )}
                          {/*
                            * Which device made it. Matters to a reader who syncs
                            * one, because it explains why some rows cannot be
                            * jumped to.
                            *
                            * Prefer the device's own label over the raw `source`
                            * slug — "Kobo Clara" is what the reader named it;
                            * "kobo" is what our schema calls it. Falls back to
                            * the slug when the id does not resolve, and shows
                            * nothing at all rather than an id when neither is
                            * available.
                            */}
                          {(() => {
                            const label = row.origin_device_id
                              ? devices[row.origin_device_id]?.label
                              : undefined;
                            const shown = label
                              || (row.source && row.source !== 'webreader' ? row.source : '');
                            return shown ? <span className={styles.annSource}>{shown}</span> : null;
                          })()}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </nav>
        </>
      )}

      {settingsOpen && (
        <>
          <div className={styles.tocScrim} onClick={() => setSettingsOpen(false)} aria-hidden="true" />
          <div ref={settingsRef} className={styles.settingsPanel} role="dialog" aria-modal="true"
            aria-labelledby="reader-appearance-title" tabIndex={-1}>
            <div className={styles.panelHeading}>
              <h2 id="reader-appearance-title">{t('Reading appearance')}</h2>
              <button className={styles.iconBtn} onClick={() => setSettingsOpen(false)} aria-label={t('Close')}>
                <X size={18} aria-hidden="true" focusable={false} />
              </button>
            </div>
            <fieldset className={styles.settingGroup}>
              <legend>{t('Page theme')}</legend>
              <div className={styles.themeChoices}>
                {([
                  ['light', Sun, t('Light')], ['sepia', Coffee, t('Sepia')],
                  ['dark', Moon, t('Dark')], ['black', MoonStar, t('Black')],
                ] as const).map(([value, Icon, label]) => (
                  <button key={value} className={theme === value ? styles.choiceActive : styles.choice}
                    aria-pressed={theme === value} onClick={() => {
                      setTheme(value); persistSetting('theme', READER_TO_THEME[value]);
                    }}>
                    <Icon size={17} aria-hidden="true" focusable={false} /> {label}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset className={styles.settingGroup}>
              <legend>{t('Columns')}</legend>
              <div className={styles.themeChoices}>
                {([
                  ['nonespread', t('One column')],
                  ['spread', t('Two columns')],
                ] as const).map(([value, label]) => (
                  <button key={value}
                    className={spread === value ? styles.choiceActive : styles.choice}
                    aria-pressed={spread === value}
                    onClick={() => {
                      setSpread(value);
                      persistSetting('spread', value);
                      // Re-layout the open book immediately rather than on the
                      // next load — epub.js recalculates its columns in place,
                      // so the reader sees the change while looking at it.
                      try {
                        renditionRef.current?.spread(value === 'nonespread' ? 'none' : 'auto');
                      } catch { /* older epub.js builds ignore a live change */ }
                    }}>
                    {label}
                  </button>
                ))}
              </div>
            </fieldset>
            <label className={styles.settingField}>
              <span>{t('Font family')}</span>
              <select value={fontFamily} onChange={(e) => {
                const value = e.target.value as ReaderSettings['font'];
                setFontFamily(value); persistSetting('font', value);
              }}>
                <option value="default">{t('Book default')}</option>
                <option value="Arial">Arial</option><option value="Yahei">Microsoft YaHei</option>
                <option value="SimSun">SimSun</option><option value="KaiTi">KaiTi</option>
              </select>
            </label>
            {([
              ['font-size', t('Font size'), fontPct, FONT_MIN, FONT_MAX, '%', setFontPct, 'fontSize'],
              ['page-margin', t('Page margins'), margin, 0, 80, 'px', setMargin, 'margin'],
              ['line-height', t('Line height'), lineHeight, 100, 220, '%', setLineHeight, 'lineHeight'],
            ] as const).map(([key, label, value, min, max, unit, setter, settingKey]) => (
              <label key={key} className={styles.settingField} htmlFor={`reader-${key}`}>
                <span>{label} <output>{value}{unit}</output></span>
                <input id={`reader-${key}`} type="range" min={min} max={max}
                  step={key === 'page-margin' ? 4 : key === 'font-size' ? 5 : 10}
                  value={value} onChange={(e) => {
                    const next = Number(e.target.value);
                    setter(next);
                    persistSetting(settingKey, next as never);
                  }} />
              </label>
            ))}
          </div>
        </>
      )}

      {/* Viewer + page-turn zones */}
      <div className={styles.stage}>
        <button className={styles.navZone} onClick={goLeft} aria-label={leftLabel}>
          <ChevronLeft size={28} aria-hidden="true" focusable={false} />
        </button>
        <div ref={viewerRef} className={styles.viewer} />
        <button className={styles.navZone} onClick={goRight} aria-label={rightLabel}>
          <ChevronRight size={28} aria-hidden="true" focusable={false} />
        </button>

        {!rendered && !renderError && (
          <div className={styles.viewerOverlay}>
            <span className={styles.spin}><Loader2 size={32} /></span>
          </div>
        )}
        {renderError && (
          <div className={styles.viewerOverlay}>
            <EmptyState message={renderError} />
          </div>
        )}
      </div>

      {/* Highlight color popover for the current selection */}
      {pendingSel && (
        <div ref={popRef} className={styles.hilitePop} role="dialog" aria-modal="true"
          aria-label={t('Highlight color')} tabIndex={-1}>
          <span className={styles.hiliteLabel}>{t('Highlight')}</span>
          {HILITE_ORDER.map((c) => (
            <button key={c} className={styles.hiliteSwatch} style={{ background: HILITE_FILL[c] }}
              onClick={() => createHighlight(c)} aria-label={colorLabel(c)} title={colorLabel(c)} />
          ))}
          <button className={styles.hiliteNote} onClick={startNoteForSelection} title={t('Add note')}>
            <StickyNote size={15} aria-hidden="true" focusable={false} />
            <span>{t('Add note')}</span>
          </button>
          <button className={styles.hiliteCancel} onClick={() => setPendingSel(null)} aria-label={t('Cancel')}>
            <X size={16} aria-hidden="true" focusable={false} />
          </button>
        </div>
      )}

      {/* Edit/remove popover for a tapped existing highlight (#782).
          Swatches recolor (PATCH); the Remove button deletes (DELETE) + unpaints. */}
      {activeHl && (
        <div ref={hlPopRef}
          className={`${styles.hilitePop} ${activeHl.note ? styles.hilitePopStack : ''}`}
          role="dialog" aria-modal="true" aria-label={t('Highlight color')} tabIndex={-1}>
          <span className={styles.hiliteLabel}>{t('Highlight')}</span>
          {HILITE_ORDER.map((c) => (
            <button key={c} className={styles.hiliteSwatch} style={{ background: HILITE_FILL[c] }}
              onClick={() => recolorHighlight(c)}
              aria-pressed={activeHl.color === c} aria-label={colorLabel(c)} title={colorLabel(c)} />
          ))}
          <button className={styles.hiliteNote} onClick={startNoteForHighlight}
            title={activeHl.note ? t('Edit note') : t('Add note')}>
            <StickyNote size={15} aria-hidden="true" focusable={false} />
            <span>{activeHl.note ? t('Edit note') : t('Add note')}</span>
          </button>
          <button className={styles.hiliteRemove} onClick={removeHighlight} title={t('Remove highlight')}>
            <Trash2 size={15} aria-hidden="true" focusable={false} />
            <span>{t('Remove highlight')}</span>
          </button>
          <button className={styles.hiliteCancel} onClick={() => setActiveHl(null)} aria-label={t('Cancel')}>
            <X size={16} aria-hidden="true" focusable={false} />
          </button>
          {/* Reveal the note on tap, so reading one costs nothing (#325). */}
          {activeHl.note && <p className={styles.hiliteNoteText}>{activeHl.note}</p>}
        </div>
      )}

      {/* Note composer — create (highlight + note in one write) or edit (#325). */}
      {composer && (
        <div ref={notePopRef} className={styles.notePop} role="dialog" aria-modal="true"
          aria-label={composer.mode === 'standalone' ? t('Write a note')
            : composer.mode === 'create' ? t('Add note') : t('Edit note')} tabIndex={-1}>
          <div className={styles.noteHead}>
            <span className={styles.hiliteLabel}>
              {composer.mode === 'standalone' ? t('Write a note')
                : composer.mode === 'create' ? t('Add note') : t('Edit note')}
            </span>
            <button className={styles.hiliteCancel} onClick={() => setComposer(null)} aria-label={t('Cancel')}>
              <X size={16} aria-hidden="true" focusable={false} />
            </button>
          </div>
          {composer.mode === 'create' && composer.text && (
            <p className={styles.noteQuote}>{composer.text}</p>
          )}
          {composer.mode === 'create' && (
            <div className={styles.noteColors} role="group" aria-label={t('Highlight color')}>
              {HILITE_ORDER.map((c) => (
                <button key={c} className={styles.hiliteSwatch} style={{ background: HILITE_FILL[c] }}
                  onClick={() => setComposer((s) => (s ? { ...s, color: c } : s))}
                  aria-pressed={composer.color === c} aria-label={colorLabel(c)} title={colorLabel(c)} />
              ))}
            </div>
          )}
          <textarea ref={noteFieldRef} className={styles.noteInput} rows={3}
            value={composer.note} aria-label={t('Note')} placeholder={t('Write a note…')}
            onChange={(e) => setComposer((s) => (s ? { ...s, note: e.target.value } : s))}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveNote(); }
            }} />
          <div className={styles.noteActions}>
            {composer.mode === 'edit' && composer.note.trim() !== '' && (
              <button className={styles.hiliteRemove} onClick={removeNote} title={t('Remove note')}>
                <Trash2 size={15} aria-hidden="true" focusable={false} />
                <span>{t('Remove note')}</span>
              </button>
            )}
            <Button variant="primary" className={styles.noteSave} onClick={saveNote}>
              {t('Save note')}
            </Button>
          </div>
        </div>
      )}

      {/* Progress (SC 4.1.2: a named progressbar, not an aria-hidden bar). */}
      <div className={styles.progressBar} role="progressbar"
        aria-label={t('Reading progress')}
        aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}
        aria-valuetext={t('{pct}% read', { pct: Math.round(progress) })}>
        <div className={styles.progressFill} style={{ transform: `scaleX(${progress / 100})` }} />
      </div>
    </div>
  );
}
