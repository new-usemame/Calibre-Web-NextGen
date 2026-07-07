// Single source of truth for the user-selectable UI font presets (#701).
//
// The `key` is what gets stored per-user in the database and validated by the
// backend — `cps/api/account.py` (`ALLOWED_UI_FONT_BODY` / `_DISPLAY`) must
// accept exactly these keys. The full CSS font `stack` lives ONLY here, so a
// tweak to a font stack can never drift out of sync with a duplicated backend
// allowlist and silently 400-reject a valid choice. The backend only knows the
// short keys; the stacks never leave the frontend.

export interface FontPreset {
  /** stored + validated token, e.g. "serif"; "" means "use the theme default". */
  key: string;
  label: string;
  /** CSS font-family value; "" for the default (falls back to the design token). */
  stack: string;
}

const SANS = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
const SERIF = "'Iowan Old Style', 'Palatino Linotype', Palatino, 'Book Antiqua', Georgia, serif";
const MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace";

// Body text. Default is the theme's Optima-led sans (tokens.css --font-body).
export const UI_BODY_FONTS: FontPreset[] = [
  { key: '', label: 'Default (Optima / Sans-Serif)', stack: '' },
  { key: 'system-sans', label: 'System Sans-Serif', stack: SANS },
  { key: 'serif', label: 'Bookish Serif', stack: SERIF },
  { key: 'mono', label: 'Monospace', stack: MONO },
];

// Display / headings. Default is already the bookish serif (tokens.css
// --font-display), so a "serif" preset would be redundant here.
export const UI_DISPLAY_FONTS: FontPreset[] = [
  { key: '', label: 'Default (Bookish Serif)', stack: '' },
  { key: 'system-sans', label: 'System Sans-Serif', stack: SANS },
  { key: 'mono', label: 'Monospace', stack: MONO },
];

function stackFor(presets: FontPreset[], key: string | undefined): string {
  return presets.find((f) => f.key === key)?.stack ?? '';
}

/** Resolve a stored body-font key to its CSS stack ("" → theme default). */
export const bodyFontStack = (key: string | undefined): string => stackFor(UI_BODY_FONTS, key);
/** Resolve a stored display-font key to its CSS stack ("" → theme default). */
export const displayFontStack = (key: string | undefined): string => stackFor(UI_DISPLAY_FONTS, key);
