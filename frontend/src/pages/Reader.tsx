import { observeReaderSelections } from '../../../cps/static/js/reading/selection-observer.js';
import {
  archiveMatchesFingerprint, chapterProgressCfi, resumeCfi, resumeForArchive,
  withResumeTimeout,
} from "../lib/readerResume";
import { useEffect, useRef, useState, useCallback, useId, useMemo } from 'react';
import { Link, useSearch } from 'wouter';
import ePub from 'epubjs';
import {
  ChevronLeft, ChevronRight, X, List, Sun, Moon, Coffee, Loader2, Trash2,
  SlidersHorizontal, StickyNote, Highlighter, MoonStar, Maximize, Minimize,
  Search, MapPin,
} from 'lucide-react';
import {
  type ReaderSettings, isWorthResending, useBook, useBookmark, useReaderSettings,
  useReadingSources, useSaveBookmark, useSaveReaderSettings, useReaderFonts, type ReadingSource,
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
import { flattenToc, tocFromNavigation, type TocItem } from '../lib/reader/toc';
import { safeLocalStorageGet, safeLocalStorageSet } from '../lib/safeStorage';
import { getReaderContentUrl, withLookupMode } from '../lib/readerTarget';
import {
  classifyHref, inBookTarget, isNoteElement, isNoterefAnchor, isOpenableHref,
  sanitizeNoteElement,
} from '../lib/readerLinks';
import { hasNativeAnchor, resolveNativeAnnotations } from '../lib/reader/nativeAnnotations';
import { readerFontFaceCss, readerFontFamily, BUILTIN_READER_FONTS, type ReaderFont } from '../lib/readerFonts';
import styles from './Reader.module.css';

/*
 * Parent-side hit target for one visible run of one link in the book.
 *
 * Geometry only: the anchor itself is held in a ref, keyed by `key`, because a
 * DOM node from the book frame has no business in React state.
 */
interface LinkHit {
  key: string;
  /** The book's own href, verbatim — the hit target's stable identity. */
  href: string;
  left: number;
  top: number;
  width: number;
  height: number;
  label: string;
}

/** An open footnote, already sanitised. `target` is what "Go to note" displays. */
interface ReaderNote {
  html: string;
  label: string;
  target: string;
}

/*
 * Smallest hit target we will paint, in CSS pixels (WCAG 2.2 SC 2.5.8 asks for
 * 24). A footnote marker is often a 7px superscript digit, which is why tapping
 * one is a coin flip on a phone even when the routing is right. The box only
 * ever GROWS around the link's own rectangle and is then clipped to the page,
 * so it cannot push a neighbouring link's target off its own text.
 */
const MIN_LINK_HIT_PX = 24;

/** `epub:type` survives HTML parsing as a literal attribute name, but a book
 *  served as XHTML puts it in the OPS namespace. Ask for both. */
function epubTypeOf(element: Element): string | null {
  return element.getAttribute('epub:type')
    ?? element.getAttributeNS('http://www.idpf.org/2007/ops', 'type');
}

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

/** A saved highlight as the reader needs it: enough to list, jump to and edit. */
interface AnnRow {
  annotation_id: string;
  cfi_range: string | null;
  start_kobospan?: string | null;
  end_kobospan?: string | null;
  content_id?: string | null;
  start_offset?: number | null;
  end_offset?: number | null;
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
