/* Cover designer v2 — the client mirror of state/cover-designer/v2/CONTRACT.md.
 *
 * Field names here are law: the backend leg builds against the same contract and
 * may only EXTEND it additively. Everything the renderer needs is expressed as a
 * `CoverDesign`; the server resolves omissions against its own defaults and
 * echoes the resolved design back in preview responses.
 */

// ---- the design object (client -> server) ------------------------------------

export type SlotName = 'title' | 'subtitle' | 'author';

export const SLOTS: SlotName[] = ['title', 'subtitle', 'author'];

export type Alignment = 'left' | 'center' | 'right';

export const ALIGNMENTS: Alignment[] = ['left', 'center', 'right'];

/** All four colours as #rrggbb. Any subset may be present: with a named `scheme`
 *  set they are overrides on top of it; with `scheme: null` they ARE the scheme. */
export interface DesignColors {
  background?: string;
  band?: string;
  title?: string;
  author?: string;
}

export interface SlotFont {
  family?: string;
  size?: number;
  bold?: boolean;
  italic?: boolean;
}

export type DesignFonts = Partial<Record<SlotName, SlotFont>>;
export type DesignAlign = Partial<Record<SlotName, Alignment>>;
/** Calibre templates per slot, e.g. "{title}" / "{series} {series_index}" /
 *  "{authors}". An absent key means the server default applies. */
export type DesignText = Partial<Record<SlotName, string>>;

export interface DesignSize {
  width?: number;
  height?: number;
}

/** Every field optional — the server fills defaults and echoes the resolution. */
export interface CoverDesign {
  style?: string;
  scheme?: string | null;
  colors?: DesignColors;
  fonts?: DesignFonts;
  align?: DesignAlign;
  text?: DesignText;
  size?: DesignSize;
}

// ---- the catalogue (server -> client) -----------------------------------------

export interface CatalogueStyle {
  id: string;
  label: string;
  description: string;
  thumbnail_url: string;
  /** What each of the four colours paints in THIS arrangement (English source
   *  strings, rendered through t()). Extension added by the v2 API leg. */
  color_roles?: Partial<Record<keyof Required<DesignColors>, string>>;
  /** The arrangement's own default alignment per slot — what the server
   *  resolves when the design omits align. */
  align?: Partial<Record<SlotName, Alignment>>;
}

export interface CatalogueScheme {
  id: string;
  label: string;
  colors: Required<DesignColors>;
  builtin: boolean;
  /** v1 field, retained by the v2 catalogue: [background, band]. */
  swatch?: string[];
}

export interface CatalogueFont {
  id: string;
  label: string;
  css_stack: string;
  sample_url: string;
  /** "serif" | "sans" | "mono" for the three ids that always exist. */
  generic?: string;
}

export interface CataloguePreset {
  id: string;
  name: string;
  design: CoverDesign;
  builtin: boolean;
  /** The backend marks shipped presets "builtin"; the base contract only knew
   *  user|library. */
  scope: 'user' | 'library' | 'builtin';
  /** Additive: flagged on every entry by the presets endpoint (the catalogue's
   *  own list omits hidden entries entirely). */
  hidden?: boolean;
  /** v1 alias of `name`, retained server-side. */
  label?: string;
  /** Saved presets only: the reader's own ordering. */
  position?: number;
}

export interface CatalogueLimits {
  min_width: number;
  max_width: number;
  min_height: number;
  max_height: number;
  font_size_min: number;
  font_size_max: number;
  max_template_length?: number;
  max_name_length?: number;
  max_presets?: number;
}

export interface CataloguePlaceholder {
  id: string;
  label: string;
}

export interface DesignerCatalogue {
  styles: CatalogueStyle[];
  schemes: CatalogueScheme[];
  fonts: CatalogueFont[];
  presets: CataloguePreset[];
  defaults: CoverDesign;
  limits: CatalogueLimits;
  placeholders?: CataloguePlaceholder[];
  alignments?: Alignment[];
  text_slots?: SlotName[];
  color_slots?: (keyof Required<DesignColors>)[];
  /** v1 keys the server retains (the admin settings page reads `layouts`). */
  default_preset?: string;
  layouts?: { id: string; label: string }[];
}

/** `designer` inside GET /book/<id>/cover/state (and the my-cover payload).
 *  The server sends the catalogue FLAT (the designer_state() payload IS the
 *  catalogue plus these keys); the contract doc's nested `catalogue` form is
 *  accepted too, for forward compatibility. */
export interface DesignerState extends Partial<DesignerCatalogue> {
  available: boolean;
  renderer?: string | null;
  catalogue?: DesignerCatalogue;
  /** Ids of presets this reader has hidden (flat form). */
  hidden_presets?: string[];
  /** Server-computed "may save scope:library" — the admin role. */
  can_share_presets?: boolean;
}

/** The catalogue out of a designer state payload, whichever form it takes. */
export function catalogueOf(designer: DesignerState | undefined): DesignerCatalogue | null {
  if (!designer?.available) return null;
  if (designer.catalogue) return designer.catalogue;
  if (Array.isArray(designer.styles) && designer.defaults && designer.limits) {
    return designer as DesignerCatalogue;
  }
  return null;
}

// ---- preview / apply / presets endpoints ---------------------------------------

export interface PreviewResponse {
  data_url: string;
  renderer: string;
  design: CoverDesign;
}

export interface PresetListResponse {
  presets: CataloguePreset[];
  /** Ids of presets this reader has hidden (the list already flags them). */
  hidden?: string[];
}

export interface PresetResponse {
  preset: CataloguePreset;
}

// ---- helpers -------------------------------------------------------------------

/** Calibre template placeholder ids a text template may reference (fallback
 *  list; the catalogue's `placeholders[]` carries labels and is preferred). */
export const TEXT_PLACEHOLDERS = ['title', 'authors', 'series', 'series_index'];

/** The locked cover aspect: 2:3, like every cover frame in the app. */
export const ASPECT_RATIO = 2 / 3;

/** Deep merge of `patch` over `base`; `patch` keys set to null/undefined objects
 *  are treated as absent, except `scheme: null` which is meaningful (custom
 *  colours) and therefore wins. Returns a fresh object. */
export function mergeDesign(base: CoverDesign, patch: CoverDesign | undefined | null): CoverDesign {
  if (!patch) return { ...base };
  const out: CoverDesign = { ...base };
  if (patch.style !== undefined) out.style = patch.style;
  if (patch.scheme !== undefined) out.scheme = patch.scheme;
  if (patch.colors) out.colors = { ...base.colors, ...patch.colors };
  if (patch.fonts) {
    out.fonts = { ...base.fonts };
    for (const slot of SLOTS) {
      if (patch.fonts[slot]) out.fonts[slot] = { ...base.fonts?.[slot], ...patch.fonts[slot] };
    }
  }
  if (patch.align) out.align = { ...base.align, ...patch.align };
  if (patch.text) out.text = { ...base.text, ...patch.text };
  if (patch.size) out.size = { ...base.size, ...patch.size };
  return out;
}

/** A preset's design as the renderer will see it: resolved over the defaults. */
export function resolvePreset(preset: CataloguePreset, defaults: CoverDesign): CoverDesign {
  return mergeDesign(defaults, preset.design);
}

function sortedEntries(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortedEntries);
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(record)
        .filter((k) => record[k] !== undefined)
        .sort()
        .map((k) => [k, sortedEntries(record[k])]),
    );
  }
  return value;
}

/** Structural equality on the fields the contract defines, key order aside. This
 *  drives the preset dropdown's honesty: diverging from a preset must un-select
 *  it, and rebuilding a preset's exact design by hand must re-select it. */
export function designsEqual(a: CoverDesign | undefined, b: CoverDesign | undefined): boolean {
  return JSON.stringify(sortedEntries(a ?? {})) === JSON.stringify(sortedEntries(b ?? {}));
}

/** The four colours a design will render with: the named scheme's colours, with
 *  any custom overrides on top; custom colours over the default scheme when
 *  `scheme` is null. Used for swatch display and as the base the colour popover
 *  edits from — the server applies the same rule. */
export function effectiveColors(design: CoverDesign, catalogue: DesignerCatalogue): Required<DesignColors> {
  const fallback = catalogue.schemes[0]?.colors
    ?? { background: '#ffffff', band: '#333333', title: '#333333', author: '#ffffff' };
  const schemeColors = catalogue.schemes.find((s) => s.id === (design.scheme ?? catalogue.defaults.scheme))?.colors
    ?? fallback;
  return { ...schemeColors, ...stripUndefined(design.colors) };
}

function stripUndefined<T extends object>(obj: T | undefined): Partial<T> {
  if (!obj) return {};
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined)) as Partial<T>;
}

export const HEX_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

/** Normalize a typed hex colour to lowercase #rrggbb, or null when unusable.
 *  #abc expands to #aabbcc. Anything else is rejected — the renderer's clamp is
 *  the last line of defence, not the first. */
export function normalizeHex(raw: string): string | null {
  const v = raw.trim();
  if (!HEX_RE.test(v)) return null;
  if (v.length === 4) {
    return `#${v[1]}${v[1]}${v[2]}${v[2]}${v[3]}${v[3]}`.toLowerCase();
  }
  return v.toLowerCase();
}

/** Clamp a pixel dimension into the catalogue's limits. */
export function clampDimension(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Math.round(value)));
}
