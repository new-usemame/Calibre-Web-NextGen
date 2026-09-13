/* "Design a cover" v2 — the whole designer panel.
 *
 * Owns the full design surface of the cover picker: preset dropdown + manager,
 * arrangement thumbnails, colour-scheme swatches with a custom-colour popover,
 * lettering samples, an Advanced disclosure (per-slot fonts/sizes/alignment,
 * text templates, cover size), the debounced server preview, and apply.
 *
 * The designer sends a CoverDesign (frontend/src/features/coverDesigner/
 * contract.ts — field names are the shared contract) and shows what the server
 * rendered. There is deliberately no client-side canvas: preview and applied
 * cover come from the same renderer, so what you see is what gets stored.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlignCenter, AlignLeft, AlignRight, BookmarkPlus, Check, Loader2, Lock, Palette, Plus,
  RotateCcw, Settings2, Sparkles, Unlock, X,
} from 'lucide-react';
import { ApiError } from '../../lib/api';
import { useT, type TFunction } from '../../lib/i18n';
import { useMe } from '../../lib/queries';
import { Button } from '../../components/Button';
import { coverDesignerApi } from './api';
import {
  ALIGNMENTS, ASPECT_RATIO, SLOTS, clampDimension, designsEqual, effectiveColors, mergeDesign,
  normalizeHex, resolvePreset,
  type Alignment, type CataloguePreset, type CoverDesign,
  type DesignColors, type DesignerCatalogue, type DesignerState, type SlotName,
} from './contract';
import styles from './DesignerPanel.module.css';

const CUSTOM = '__custom';
const HIDDEN_KEY = 'cwng.coverDesigner.hiddenBuiltins';

/** Built-ins this browser has hidden, id → name, so the manager can offer
 *  Restore even on a server whose preset list does not carry the additive
 *  `hidden` flag the contract allows. */
function readHiddenBuiltins(): Record<string, string> {
  try {
    const raw = localStorage.getItem(HIDDEN_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed as Record<string, string> : {};
  } catch { return {}; }
}
function writeHiddenBuiltins(map: Record<string, string>) {
  try { localStorage.setItem(HIDDEN_KEY, JSON.stringify(map)); } catch { /* private mode */ }
}

export function CoverDesignerPanel({ id, designer, locked, personal, onApplied, onError }: {
  id: string; designer: DesignerState | undefined; locked: boolean; personal: boolean;
  onApplied: (url?: string) => void; onError: (e: unknown) => void;
}) {
  // A v1 server (or a box with neither Calibre nor Pillow) has no catalogue —
  // hide the panel rather than offering controls that can only fail.
  if (!designer?.available || !designer.catalogue) return null;
  return (
    <DesignerPanelInner
      id={id} catalogue={designer.catalogue} locked={locked} personal={personal}
      onApplied={onApplied} onError={onError} />
  );
}

function DesignerPanelInner({ id, catalogue, locked, personal, onApplied, onError }: {
  id: string; catalogue: DesignerCatalogue; locked: boolean; personal: boolean;
  onApplied: (url?: string) => void; onError: (e: unknown) => void;
}) {
  const t = useT();
  const { data: me } = useMe();
  const isAdmin = !!me?.role?.admin;

  const [open, setOpen] = useState(false);
  const [design, setDesign] = useState<CoverDesign>(() => mergeDesign({}, catalogue.defaults));
  /** The preset the working design was last loaded from — names the "Custom
   *  (based on X)" state after the user diverges. */
  const [basedOn, setBasedOn] = useState<string | null>(null);
  const [presets, setPresets] = useState<CataloguePreset[]>(catalogue.presets);
  const [hiddenServer, setHiddenServer] = useState<CataloguePreset[]>([]);
  const [hiddenLocal, setHiddenLocal] = useState<Record<string, string>>(readHiddenBuiltins);

  const [preview, setPreview] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [colorOpen, setColorOpen] = useState(false);
  const seq = useRef(0); // a slow render must not overwrite a newer one

  // Selecting a preset loads its design; every edit after that diverges and the
  // dropdown says so. Deriving the match (instead of tracking a selection) keeps
  // the control honest in both directions: rebuild a preset's exact design by
  // hand and its entry lights up again.
  const matchedPreset = presets.find((p) => designsEqual(resolvePreset(p, catalogue.defaults), design));
  const basedOnName = basedOn ? presets.find((p) => p.id === basedOn)?.name
    ?? hiddenLocal[basedOn] ?? null : null;

  const visibleHidden = useMemo(() => {
    const byId = new Map<string, string>();
    for (const p of hiddenServer) byId.set(p.id, p.name);
    for (const [hid, name] of Object.entries(hiddenLocal)) {
      if (!presets.some((p) => p.id === hid)) byId.set(hid, name);
    }
    return [...byId.entries()].map(([hid, name]) => ({ id: hid, name }));
  }, [hiddenServer, hiddenLocal, presets]);

  const refreshPresets = useCallback(async () => {
    const r = await coverDesignerApi.presets();
    setPresets(r.presets.filter((p) => !p.hidden));
    setHiddenServer(r.presets.filter((p) => p.hidden));
  }, []);

  // The catalogue payload carries the preset list already; the standalone GET
  // catches changes made from another device since the page loaded. Failure
  // (an older server) keeps the catalogue list — nothing breaks.
  useEffect(() => {
    if (!open) return;
    refreshPresets().catch(() => {});
  }, [open, refreshPresets]);

  const choosePreset = (presetId: string) => {
    if (presetId === CUSTOM) return;
    const entry = presets.find((p) => p.id === presetId);
    if (!entry) return;
    setDesign(resolvePreset(entry, catalogue.defaults));
    setBasedOn(entry.id);
    setNotice(null);
  };

  const patchDesign = (patch: CoverDesign) => setDesign((d) => mergeDesign(d, patch));

  const resetDefaults = () => {
    setDesign(mergeDesign({}, catalogue.defaults));
    setBasedOn(null);
  };

  // Rendering costs a subprocess on the server, so nothing is rendered until the
  // panel is actually open, and changes are debounced.
  useEffect(() => {
    if (!open) return;
    const mySeq = ++seq.current;
    setRendering(true);
    const h = setTimeout(async () => {
      try {
        const r = await coverDesignerApi.designPreview(id, design, personal);
        if (mySeq === seq.current) { setPreview(r.data_url); setRenderError(null); }
      } catch (e) {
        if (mySeq === seq.current) {
          setPreview(null);
          setRenderError((e instanceof ApiError && e.message) || t('Could not design a cover for this book.'));
        }
      } finally { if (mySeq === seq.current) setRendering(false); }
    }, 300);
    return () => clearTimeout(h);
  }, [open, id, design, personal, t]);

  const apply = async () => {
    if (locked || applying || !preview) return;
    setApplying(true);
    try {
      const r = await coverDesignerApi.applyGenerated(id, design, personal);
      if (r.ok !== false) onApplied(r.cover_url);
      else onError(new ApiError(400, r.error_message || t('Cover save failed.')));
    } catch (e) { onError(e); }
    finally { setApplying(false); }
  };

  const onSavedPreset = (preset: CataloguePreset) => {
    setPresets((prev) => [...prev.filter((p) => p.id !== preset.id), preset]);
    setBasedOn(preset.id);
    setSaveOpen(false);
    setNotice(t('Preset saved.'));
    refreshPresets().catch(() => {});
  };

  const onHiddenBuiltin = (presetId: string, name: string) => {
    const next = { ...hiddenLocal, [presetId]: name };
    setHiddenLocal(next);
    writeHiddenBuiltins(next);
  };
  const onRestoredBuiltin = (presetId: string) => {
    const next = { ...hiddenLocal };
    delete next[presetId];
    setHiddenLocal(next);
    writeHiddenBuiltins(next);
  };

  const groups = useMemo(() => ({
    builtin: presets.filter((p) => p.builtin),
    library: presets.filter((p) => !p.builtin && p.scope === 'library'),
    user: presets.filter((p) => !p.builtin && p.scope !== 'library'),
  }), [presets]);

  return (
    <details className={styles.panel} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className={styles.panelSummary}>
        <Palette size={15} aria-hidden="true" focusable={false} /> {t('Design a cover')}
        <span className={styles.panelHint}>{t("Make one from the book's title and author")}</span>
      </summary>
      <div className={styles.panelBody}>
        <div className={styles.presetBar}>
          <label className={styles.presetField}>
            <span className={styles.fieldLabel}>{t('Preset')}</span>
            <select
              className={styles.select}
              value={matchedPreset?.id ?? CUSTOM}
              onChange={(e) => choosePreset(e.target.value)}
            >
              {!matchedPreset && (
                <option value={CUSTOM}>
                  {basedOnName ? t('Custom (based on {name})', { name: basedOnName }) : t('Custom')}
                </option>
              )}
              {groups.builtin.length > 0 && (
                <optgroup label={t('Built-in')}>
                  {groups.builtin.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </optgroup>
              )}
              {groups.library.length > 0 && (
                <optgroup label={t('Library')}>
                  {groups.library.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </optgroup>
              )}
              {groups.user.length > 0 && (
                <optgroup label={t('My presets')}>
                  {groups.user.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </optgroup>
              )}
            </select>
          </label>
          <Button variant="ghost" size="sm" onClick={() => setManageOpen(true)}>
            <Settings2 size={14} aria-hidden="true" focusable={false} /> {t('Manage presets…')}
          </Button>
        </div>

        <div className={styles.designerGrid}>
          <div className={styles.previewCol}>
            <div className={styles.frame} aria-busy={rendering}>
              {preview
                ? <img src={preview} className={styles.previewImg} alt={t('Preview of the designed cover')} />
                : <div className={styles.frameFallback}>
                    {rendering ? <span className={styles.spin}><Loader2 size={22} /></span> : <Palette size={26} />}
                  </div>}
              {/* Re-render feedback where the eye already is: the outgoing cover
                  dims (via aria-busy in the CSS) and a spinner rides over it.
                  Decorative — the role=status caption carries the announcement. */}
              {rendering && preview && (
                <div className={styles.frameBusy} aria-hidden="true">
                  <span className={styles.spin}><Loader2 size={18} /></span>
                </div>
              )}
            </div>
            <p className={styles.panelNote} role="status">
              {rendering ? t('Drawing the cover…')
                : renderError ? '' : t('The server draws this cover; nothing is saved until you apply it.')}
            </p>
            {renderError && <p className={styles.errText} role="alert">{renderError}</p>}
          </div>

          <div className={styles.controls}>
            {catalogue.styles.length > 0 && (
              <ArrangementPicker
                catalogue={catalogue}
                value={design.style ?? catalogue.defaults.style ?? catalogue.styles[0].id}
                onChange={(style) => patchDesign({ style })}
              />
            )}

            <SchemePicker
              catalogue={catalogue}
              design={design}
              popoverOpen={colorOpen}
              onTogglePopover={setColorOpen}
              onPickScheme={(scheme) => setDesign((d) => ({ ...d, scheme, colors: {} }))}
              onCustomColors={(colors) => setDesign((d) => ({ ...d, scheme: null, colors }))}
            />

            {catalogue.fonts.length > 0 && (
              <LetteringPicker
                catalogue={catalogue}
                design={design}
                onChange={(family) => {
                  const fonts = { ...design.fonts };
                  for (const slot of SLOTS) {
                    fonts[slot] = { ...fonts[slot], family };
                  }
                  patchDesign({ fonts });
                }}
              />
            )}

            <AdvancedControls catalogue={catalogue} design={design} onPatch={patchDesign} onReset={resetDefaults} onSet={setDesign} />

            <div className={styles.actions}>
              <Button onClick={apply} disabled={locked || applying || !preview || rendering}>
                {applying ? <span className={styles.spin}><Loader2 size={14} /></span> : <Sparkles size={14} />}
                {' '}{t('Use this design')}
              </Button>
              <Button variant="ghost" onClick={() => setSaveOpen(true)}>
                <BookmarkPlus size={14} aria-hidden="true" focusable={false} /> {t('Save as preset')}
              </Button>
            </div>
            {locked && <p className={styles.lockedHint}>{t('Unlock the cover above to apply a new one.')}</p>}
            {notice && <p className={styles.notice} role="status"><Check size={13} /> {notice}</p>}
          </div>
        </div>
      </div>

      {saveOpen && (
        <SavePresetModal
          isAdmin={isAdmin}
          design={design}
          onClose={() => setSaveOpen(false)}
          onSaved={onSavedPreset}
        />
      )}
      {manageOpen && (
        <ManagePresetsModal
          presets={presets}
          hidden={visibleHidden}
          catalogue={catalogue}
          onClose={() => setManageOpen(false)}
          onChanged={() => refreshPresets().catch(() => {})}
          onHiddenBuiltin={onHiddenBuiltin}
          onRestoredBuiltin={onRestoredBuiltin}
        />
      )}
    </details>
  );
}

// ============================================================================
// Arrangement — selectable thumbnails in a horizontal strip
// ============================================================================

/** Arrow-key contract for the radiogroups in this panel (APG radio group):
 *  arrows/Home/End move selection AND focus together; only the checked radio
 *  is in the tab order (roving tabindex on the buttons themselves). */
function radioGroupKeys(e: React.KeyboardEvent<HTMLElement>) {
  const nav = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'];
  if (!nav.includes(e.key)) return;
  const root = e.currentTarget as HTMLElement;
  const radios = Array.from(root.querySelectorAll<HTMLElement>('[role="radio"]'));
  const idx = radios.findIndex((r) => r === document.activeElement);
  if (idx < 0) return;
  e.preventDefault();
  let next = idx;
  if (e.key === 'Home') next = 0;
  else if (e.key === 'End') next = radios.length - 1;
  else next = (idx + (e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 1) + radios.length) % radios.length;
  radios[next].focus();
  radios[next].click();
}

function ArrangementPicker({ catalogue, value, onChange }: {
  catalogue: DesignerCatalogue; value: string; onChange: (style: string) => void;
}) {
  const t = useT();
  const noneChecked = !catalogue.styles.some((s) => s.id === value);
  return (
    <fieldset className={styles.group}>
      <legend className={styles.groupLegend}>{t('Arrangement')}</legend>
      <div className={styles.arrangeRow} role="radiogroup" aria-label={t('Arrangement')} onKeyDown={radioGroupKeys}>
        {catalogue.styles.map((s, i) => {
          const checked = s.id === value;
          return (
            <button
              key={s.id}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked || (noneChecked && i === 0) ? 0 : -1}
              className={checked ? styles.arrangeBtnOn : styles.arrangeBtn}
              title={s.description || undefined}
              onClick={() => onChange(s.id)}
            >
              <StyleThumb styleId={s.id} url={s.thumbnail_url} label={s.label} />
              <span className={styles.arrangeLabel}>{t(s.label)}</span>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

/** The server-rendered style thumbnail; the inline glyph is the offline/404
 *  fallback so the strip never shows a broken image. */
function StyleThumb({ styleId, url, label }: { styleId: string; url: string; label: string }) {
  const [failed, setFailed] = useState(!url);
  useEffect(() => { setFailed(!url); }, [url]);
  if (failed) return <StyleGlyph styleId={styleId} label={label} />;
  return (
    <img
      src={url} alt="" loading="lazy" className={styles.arrangeThumb}
      onError={() => setFailed(true)} />
  );
}

/** Neutral per-style glyph drawn from the arrangement idea itself. Unknown
 *  styles get the generic stacked-lines drawing. Decorative: the button's
 *  accessible name comes from its label, so these carry no alt text. */
function StyleGlyph({ styleId, label }: { styleId: string; label: string }) {
  const common = { fill: 'currentColor' } as const;
  return (
    <svg viewBox="0 0 64 96" className={styles.arrangeGlyph} role="img" aria-label={label}>
      {styleId === 'banner' && (
        <>
          <rect x="6" y="14" width="52" height="18" rx="2" opacity="0.9" {...common} />
          <rect x="14" y="19" width="36" height="3" rx="1.5" fill="var(--surface-1)" />
          <rect x="20" y="24" width="24" height="2.5" rx="1.2" fill="var(--surface-1)" />
          <rect x="18" y="44" width="28" height="3" rx="1.5" {...common} opacity="0.55" />
          <rect x="24" y="50" width="16" height="2.5" rx="1.2" {...common} opacity="0.4" />
        </>
      )}
      {styleId === 'ornamental' && (
        <>
          <rect x="8" y="12" width="48" height="72" rx="3" fill="none" stroke="currentColor" strokeWidth="2.5" />
          <rect x="13" y="17" width="38" height="62" rx="2" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.6" />
          <rect x="18" y="36" width="28" height="3" rx="1.5" {...common} />
          <rect x="22" y="43" width="20" height="2.5" rx="1.2" {...common} opacity="0.7" />
          <rect x="24" y="58" width="16" height="2.5" rx="1.2" {...common} opacity="0.5" />
        </>
      )}
      {/* 'blocks' and every unknown style: text over a colour band. */}
      {(styleId === 'blocks' || (styleId !== 'banner' && styleId !== 'ornamental')) && (
        <>
          <rect x="10" y="16" width="44" height="4" rx="2" {...common} />
          <rect x="16" y="24" width="32" height="3" rx="1.5" {...common} opacity="0.7" />
          <rect x="6" y="58" width="52" height="20" rx="2" {...common} opacity="0.9" />
          <rect x="16" y="66" width="32" height="3" rx="1.5" fill="var(--surface-1)" />
        </>
      )}
    </svg>
  );
}

// ============================================================================
// Colour scheme — split swatches + the custom-colour popover
// ============================================================================

function SchemePicker({ catalogue, design, popoverOpen, onTogglePopover, onPickScheme, onCustomColors }: {
  catalogue: DesignerCatalogue; design: CoverDesign;
  popoverOpen: boolean; onTogglePopover: (open: boolean) => void;
  onPickScheme: (schemeId: string) => void;
  onCustomColors: (colors: DesignColors) => void;
}) {
  const t = useT();
  const isCustom = design.scheme === null;
  const currentColors = effectiveColors(design, catalogue);
  const currentScheme = catalogue.schemes.find((s) => s.id === design.scheme);
  const noneChecked = !currentScheme && !isCustom;

  return (
    <fieldset className={styles.group}>
      <legend className={styles.groupLegend}>{t('Colour scheme')}</legend>
      <div className={styles.swatchZone}>
        <div className={styles.swatchRow} role="radiogroup" aria-label={t('Colour scheme')} onKeyDown={radioGroupKeys}>
          {catalogue.schemes.map((s, i) => {
            const checked = s.id === design.scheme;
            return (
              <button
                key={s.id}
                type="button"
                role="radio"
                aria-checked={checked}
                aria-label={t(s.label)}
                tabIndex={checked || (noneChecked && i === 0) ? 0 : -1}
                className={checked ? styles.swatchOn : styles.swatch}
                onClick={() => onPickScheme(s.id)}
              >
                <SwatchFace colors={s.colors} />
              </button>
            );
          })}
          {isCustom && (
            <button
              type="button"
              className={`${styles.swatch} ${styles.swatchOn}`}
              aria-label={t('Edit custom colours')}
              aria-pressed={popoverOpen}
              onClick={() => onTogglePopover(!popoverOpen)}
            >
              <SwatchFace colors={currentColors} />
            </button>
          )}
          <button
            type="button"
            className={`${styles.swatch} ${styles.swatchRainbow}`}
            aria-label={t('Custom colours')}
            aria-expanded={popoverOpen}
            onClick={() => onTogglePopover(!popoverOpen)}
          >
            <Plus size={18} aria-hidden="true" focusable={false} />
          </button>
        </div>
        <p className={styles.schemeCaption}>
          {isCustom ? t('Custom') : currentScheme ? t(currentScheme.label) : ''}
        </p>
        {popoverOpen && (
          <ColorPopover
            colors={currentColors}
            onChange={onCustomColors}
            onClose={() => onTogglePopover(false)}
          />
        )}
      </div>
    </fieldset>
  );
}

/** A circular swatch split diagonally between the background and band colours,
 *  with the two text colours as centred dots. */
function SwatchFace({ colors }: { colors: Required<DesignColors> }) {
  return (
    <span
      className={styles.swatchFace}
      aria-hidden="true"
      style={{ background: `linear-gradient(135deg, ${colors.background} 50%, ${colors.band} 50%)` }}
    >
      <span className={styles.swatchDots}>
        <span className={styles.swatchDot} style={{ background: colors.title }} />
        <span className={styles.swatchDot} style={{ background: colors.author }} />
      </span>
    </span>
  );
}

const COLOR_SLOTS: { key: keyof DesignColors; label: string }[] = [
  { key: 'background', label: 'Background' },
  { key: 'band', label: 'Band' },
  { key: 'title', label: 'Title text' },
  { key: 'author', label: 'Author text' },
];

function ColorPopover({ colors, onChange, onClose }: {
  colors: Required<DesignColors>;
  onChange: (colors: DesignColors) => void;
  onClose: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  // Hex fields keep their own draft text so an half-typed value is not "corrected"
  // mid-keystroke; only a complete valid colour patches the design (live preview).
  const [drafts, setDrafts] = useState<Record<string, string>>(() => ({ ...colors }));

  useEffect(() => {
    const node = ref.current;
    node?.querySelector<HTMLElement>('input')?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    const onPointer = (e: PointerEvent) => {
      if (node && !node.contains(e.target as Node)) onClose();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointer);
    };
  }, [onClose]);

  const setColor = (key: keyof DesignColors, hex: string | null) => {
    if (!hex) return;
    onChange({ ...colors, [key]: hex });
  };

  return (
    <div className={styles.popover} role="dialog" aria-label={t('Custom colours')} ref={ref}>
      {COLOR_SLOTS.map(({ key, label }) => (
        <div className={styles.popRow} key={key}>
          <span className={styles.popLabel}>{t(label)}</span>
          <input
            type="color"
            className={styles.colorInput}
            value={normalizeHex(colors[key] ?? '#000000') ?? '#000000'}
            aria-label={t(label)}
            onChange={(e) => {
              setDrafts((d) => ({ ...d, [key]: e.target.value }));
              setColor(key, e.target.value);
            }}
          />
          <input
            type="text"
            className={styles.hexInput}
            value={drafts[key] ?? ''}
            inputMode="text"
            spellCheck={false}
            aria-label={t('{name} hex value', { name: t(label) })}
            placeholder="#rrggbb"
            onChange={(e) => {
              const raw = e.target.value;
              setDrafts((d) => ({ ...d, [key]: raw }));
              const hex = normalizeHex(raw);
              if (hex) setColor(key, hex);
            }}
          />
        </div>
      ))}
      <div className={styles.popFoot}>
        <Button variant="ghost" size="sm" onClick={onClose}>{t('Done')}</Button>
      </div>
    </div>
  );
}

// ============================================================================
// Lettering — font samples as selectable cards
// ============================================================================

function LetteringPicker({ catalogue, design, onChange }: {
  catalogue: DesignerCatalogue; design: CoverDesign; onChange: (family: string) => void;
}) {
  const t = useT();
  // One lettering choice drives all three slots. Per-slot divergence (set in
  // Advanced) means no card is selected — the honest "mixed" state.
  const families = SLOTS.map((s) => design.fonts?.[s]?.family).filter(Boolean);
  const uniform = families.length > 0 && families.every((f) => f === families[0]) ? families[0] : null;
  const noneChecked = !uniform;
  return (
    <fieldset className={styles.group}>
      <legend className={styles.groupLegend}>{t('Lettering')}</legend>
      <div className={styles.fontRow} role="radiogroup" aria-label={t('Lettering')} onKeyDown={radioGroupKeys}>
        {catalogue.fonts.map((f, i) => {
          const checked = f.id === uniform;
          return (
            <button
              key={f.id}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked || (noneChecked && i === 0) ? 0 : -1}
              className={checked ? styles.fontCardOn : styles.fontCard}
              onClick={() => onChange(f.id)}
            >
              <FontSample sampleUrl={f.sample_url} cssStack={f.css_stack} label={f.label} />
              <span className={styles.fontLabel}>{t(f.label)}</span>
            </button>
          );
        })}
      </div>
      {!uniform && <p className={styles.mixedNote}>{t('Mixed lettering — set per line in Advanced.')}</p>}
    </fieldset>
  );
}

function FontSample({ sampleUrl, cssStack, label }: { sampleUrl: string; cssStack: string; label: string }) {
  const [failed, setFailed] = useState(!sampleUrl);
  useEffect(() => { setFailed(!sampleUrl); }, [sampleUrl]);
  if (failed) {
    return (
      <span className={styles.fontSampleFallback} style={{ fontFamily: cssStack || undefined }} aria-hidden="true">
        Aa
      </span>
    );
  }
  return (
    <img
      src={sampleUrl} alt={label} loading="lazy" className={styles.fontSample}
      onError={() => setFailed(true)} />
  );
}

// ============================================================================
// Advanced — per-slot control, templates, cover size
// ============================================================================

function AdvancedControls({ catalogue, design, onPatch, onReset, onSet }: {
  catalogue: DesignerCatalogue;
  design: CoverDesign;
  onPatch: (patch: CoverDesign) => void;
  onReset: () => void;
  onSet: (fn: (d: CoverDesign) => CoverDesign) => void;
}) {
  const t = useT();
  return (
    <details className={styles.advanced}>
      <summary className={styles.advSummary}>
        {t('Advanced')}
        <span className={styles.advHint}>{t('Fonts per line, wording, cover size')}</span>
      </summary>
      <div className={styles.advBody}>
        {catalogue.fonts.length > 0 && SLOTS.map((slot) => (
          <SlotControls key={slot} slot={slot} catalogue={catalogue} design={design} onPatch={onPatch} t={t} />
        ))}

        <fieldset className={styles.group}>
          <legend className={styles.groupLegend}>{t('Text templates')}</legend>
          {SLOTS.map((slot) => (
            <label className={styles.field} key={slot}>
              <span className={styles.fieldLabel}>{slotLabel(slot, t)}</span>
              <input
                type="text"
                className={styles.textInput}
                value={design.text?.[slot] ?? ''}
                placeholder={catalogue.defaults.text?.[slot] ?? ''}
                onChange={(e) => {
                  // An emptied field removes the key so the server default
                  // applies; the contract has no "blank this slot" value.
                  const value = e.target.value;
                  onSet((d) => {
                    const text = { ...d.text };
                    if (value === '') delete text[slot]; else text[slot] = value;
                    return { ...d, text };
                  });
                }}
              />
            </label>
          ))}
          <p className={styles.helpText}>
            {t('Placeholders: {title}, {authors}, {series}, {series_index}. Leave a field empty to use its default.')}
          </p>
        </fieldset>

        <SizeControls catalogue={catalogue} design={design} onPatch={onPatch} />

        <div>
          <Button variant="ghost" size="sm" onClick={onReset}>
            <RotateCcw size={14} aria-hidden="true" focusable={false} /> {t('Reset to defaults')}
          </Button>
        </div>
      </div>
    </details>
  );
}

function slotLabel(slot: SlotName, t: TFunction): string {
  if (slot === 'title') return t('Title');
  if (slot === 'subtitle') return t('Subtitle');
  return t('Author');
}

function SlotControls({ slot, catalogue, design, onPatch, t }: {
  slot: SlotName; catalogue: DesignerCatalogue; design: CoverDesign;
  onPatch: (patch: CoverDesign) => void; t: TFunction;
}) {
  const slotFont = design.fonts?.[slot] ?? {};
  const align = design.align?.[slot] ?? catalogue.defaults.align?.[slot] ?? 'center';
  const limits = catalogue.limits;
  const setFont = (patch: { family?: string; size?: number }) =>
    onPatch({ fonts: { ...design.fonts, [slot]: { ...slotFont, ...patch } } });

  return (
    <fieldset className={styles.group}>
      <legend className={styles.groupLegend}>{slotLabel(slot, t)}</legend>
      <div className={styles.slotGrid}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('Font')}</span>
          <select
            className={styles.select}
            value={slotFont.family ?? catalogue.fonts[0]?.id ?? ''}
            onChange={(e) => setFont({ family: e.target.value })}
          >
            {catalogue.fonts.map((f) => <option key={f.id} value={f.id}>{t(f.label)}</option>)}
          </select>
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('Size (pt)')}</span>
          <input
            type="number"
            className={styles.numInput}
            min={limits.font_size_min}
            max={limits.font_size_max}
            value={slotFont.size ?? ''}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (e.target.value !== '' && Number.isFinite(n)) {
                setFont({ size: clampDimension(n, limits.font_size_min, limits.font_size_max) });
              }
            }}
          />
        </label>
        <div className={styles.field}>
          <span className={styles.fieldLabel} id={`cd-align-label-${slot}`}>{t('Alignment')}</span>
          <div className={styles.alignGroup} role="radiogroup" aria-labelledby={`cd-align-label-${slot}`}>
            {ALIGNMENTS.map((a) => (
              <AlignButton
                key={a} value={a} checked={align === a}
                onPick={() => onPatch({ align: { ...design.align, [slot]: a } })}
              />
            ))}
          </div>
        </div>
      </div>
    </fieldset>
  );
}

function AlignButton({ value, checked, onPick }: { value: Alignment; checked: boolean; onPick: () => void }) {
  const t = useT();
  const Icon = value === 'left' ? AlignLeft : value === 'right' ? AlignRight : AlignCenter;
  const label = value === 'left' ? t('Left') : value === 'right' ? t('Right') : t('Center');
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      aria-label={label}
      title={label}
      className={checked ? styles.alignBtnOn : styles.alignBtn}
      onClick={onPick}
    >
      <Icon size={14} aria-hidden="true" focusable={false} />
    </button>
  );
}

function SizeControls({ catalogue, design, onPatch }: {
  catalogue: DesignerCatalogue; design: CoverDesign; onPatch: (patch: CoverDesign) => void;
}) {
  const t = useT();
  const limits = catalogue.limits;
  const size = design.size ?? {};
  const [ratioLocked, setRatioLocked] = useState(true);

  const setWidth = (raw: number) => {
    const width = clampDimension(raw, limits.min_width, limits.max_width);
    const next = { ...size, width };
    if (ratioLocked) next.height = clampDimension(width / ASPECT_RATIO, limits.min_height, limits.max_height);
    onPatch({ size: next });
  };
  const setHeight = (raw: number) => {
    const height = clampDimension(raw, limits.min_height, limits.max_height);
    const next = { ...size, height };
    if (ratioLocked) next.width = clampDimension(height * ASPECT_RATIO, limits.min_width, limits.max_width);
    onPatch({ size: next });
  };

  return (
    <fieldset className={styles.group}>
      <legend className={styles.groupLegend}>{t('Cover size')}</legend>
      <div className={styles.sizeRow}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('Width (px)')}</span>
          <input
            type="number"
            className={styles.numInput}
            min={limits.min_width}
            max={limits.max_width}
            value={size.width ?? ''}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (e.target.value !== '' && Number.isFinite(n)) setWidth(n);
            }}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{t('Height (px)')}</span>
          <input
            type="number"
            className={styles.numInput}
            min={limits.min_height}
            max={limits.max_height}
            value={size.height ?? ''}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (e.target.value !== '' && Number.isFinite(n)) setHeight(n);
            }}
          />
        </label>
        <button
          type="button"
          className={styles.lockBtn}
          aria-pressed={ratioLocked}
          aria-label={ratioLocked ? t('Unlock the 2:3 proportions') : t('Lock the 2:3 proportions')}
          title={ratioLocked ? t('Unlock the 2:3 proportions') : t('Lock the 2:3 proportions')}
          onClick={() => {
            const next = !ratioLocked;
            setRatioLocked(next);
            if (next && size.width) {
              onPatch({ size: { ...size, height: clampDimension(size.width / ASPECT_RATIO, limits.min_height, limits.max_height) } });
            }
          }}
        >
          {ratioLocked ? <Lock size={14} aria-hidden="true" focusable={false} /> : <Unlock size={14} aria-hidden="true" focusable={false} />}
        </button>
      </div>
      <p className={styles.helpText}>
        {t('Between {min} and {max} px. Locked to 2:3, the ratio every cover frame in the app uses.', { min: limits.min_width, max: limits.max_width })}
      </p>
    </fieldset>
  );
}

// ============================================================================
// Preset modals
// ============================================================================

/** Shared modal wiring: focus the first field on open, trap Tab, Escape closes,
 *  focus returns to the trigger. Same contract as the picker's confirm modal. */
function useModalBehavior(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prevFocus = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const focusables = () => node
      ? Array.from(node.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
        .filter((el) => !el.hasAttribute('disabled'))
      : [];
    const f = focusables();
    (f.find((el) => el.tagName === 'INPUT') ?? f[0] ?? node)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return; }
      if (e.key !== 'Tab') return;
      const els = focusables();
      if (!els.length) return;
      const first = els[0], last = els[els.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); prevFocus?.focus?.(); };
  }, [onClose]);
  return ref;
}

function SavePresetModal({ isAdmin, design, onClose, onSaved }: {
  isAdmin: boolean; design: CoverDesign;
  onClose: () => void; onSaved: (preset: CataloguePreset) => void;
}) {
  const t = useT();
  const ref = useModalBehavior(onClose);
  const [name, setName] = useState('');
  const [libraryScope, setLibraryScope] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    try {
      const r = await coverDesignerApi.createPreset(trimmed, design, libraryScope ? 'library' : undefined);
      onSaved(r.preset);
    } catch (e) {
      setError((e instanceof ApiError && e.message) || t('Could not save the preset. Try again.'));
      setSaving(false);
    }
  };

  return (
    <div className={styles.overlay} onClick={onClose} role="presentation">
      <div
        className={styles.modal} onClick={(e) => e.stopPropagation()}
        ref={ref} role="dialog" aria-modal="true" aria-label={t('Save as preset')} tabIndex={-1}>
        <div className={styles.modalHead}>
          <h3>{t('Save as preset')}</h3>
          <button className={styles.modalClose} onClick={onClose} aria-label={t('Close')}><X size={18} /></button>
        </div>
        <form
          className={styles.modalBody}
          onSubmit={(e) => { e.preventDefault(); void save(); }}
        >
          <label className={styles.field}>
            <span className={styles.fieldLabel}>{t('Preset name')}</span>
            <input
              type="text"
              className={styles.textInput}
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          {isAdmin && (
            <label className={styles.switchRow}>
              <input type="checkbox" checked={libraryScope} onChange={(e) => setLibraryScope(e.target.checked)} />
              <span>{t('Save for the whole library')}</span>
            </label>
          )}
          {error && <p className={styles.errText} role="alert">{error}</p>}
          <div className={styles.modalFoot}>
            <Button variant="ghost" type="button" onClick={onClose}>{t('Cancel')}</Button>
            <Button type="submit" disabled={saving || !name.trim()}>
              {saving ? <span className={styles.spin}><Loader2 size={14} /></span> : <BookmarkPlus size={14} />}
              {' '}{t('Save preset')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ManagePresetsModal({ presets, hidden, catalogue, onClose, onChanged, onHiddenBuiltin, onRestoredBuiltin }: {
  presets: CataloguePreset[];
  hidden: { id: string; name: string }[];
  catalogue: DesignerCatalogue;
  onClose: () => void;
  onChanged: () => void;
  onHiddenBuiltin: (id: string, name: string) => void;
  onRestoredBuiltin: (id: string) => void;
}) {
  const t = useT();
  const ref = useModalBehavior(onClose);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fail = (e: unknown) =>
    setError((e instanceof ApiError && e.message) || t('The preset change failed. Try again.'));

  const doRename = async (presetId: string) => {
    const name = renameDraft.trim();
    if (!name || busy) return;
    setBusy(true);
    try {
      await coverDesignerApi.updatePreset(presetId, { name });
      setRenaming(null);
      onChanged();
    } catch (e) { fail(e); }
    finally { setBusy(false); }
  };

  const doDelete = async (preset: CataloguePreset) => {
    if (busy) return;
    setBusy(true);
    try {
      await coverDesignerApi.deletePreset(preset.id);
      if (preset.builtin) onHiddenBuiltin(preset.id, preset.name);
      setConfirmingDelete(null);
      onChanged();
    } catch (e) { fail(e); }
    finally { setBusy(false); }
  };

  const doRestore = async (presetId: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await coverDesignerApi.restorePreset(presetId);
      onRestoredBuiltin(presetId);
      onChanged();
    } catch (e) { fail(e); }
    finally { setBusy(false); }
  };

  const scopeBadge = (p: CataloguePreset) =>
    p.builtin ? t('Built-in') : p.scope === 'library' ? t('Library') : t('Mine');

  return (
    <div className={styles.overlay} onClick={onClose} role="presentation">
      <div
        className={styles.modal} onClick={(e) => e.stopPropagation()}
        ref={ref} role="dialog" aria-modal="true" aria-label={t('Manage presets')} tabIndex={-1}>
        <div className={styles.modalHead}>
          <h3>{t('Manage presets')}</h3>
          <button className={styles.modalClose} onClick={onClose} aria-label={t('Close')}><X size={18} /></button>
        </div>
        <div className={styles.modalBody}>
          {presets.length === 0 && <p className={styles.helpText}>{t('No presets yet. Save one from the designer.')}</p>}
          <ul className={styles.manageList}>
            {presets.map((p) => (
              <li key={p.id} className={styles.manageRow}>
                <span
                  className={styles.manageDot}
                  aria-hidden="true"
                  style={{ background: presetDot(p, catalogue) }}
                />
                {renaming === p.id ? (
                  <input
                    type="text"
                    className={styles.textInput}
                    value={renameDraft}
                    maxLength={80}
                    aria-label={t('Preset name')}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') { e.preventDefault(); void doRename(p.id); }
                      if (e.key === 'Escape') setRenaming(null);
                    }}
                  />
                ) : (
                  <span className={styles.manageName}>{p.name}</span>
                )}
                <span className={styles.manageBadge}>{scopeBadge(p)}</span>
                <span className={styles.rowActions}>
                  {renaming === p.id ? (
                    <>
                      <Button size="sm" variant="ghost" disabled={busy || !renameDraft.trim()}
                              onClick={() => void doRename(p.id)}>{t('Save')}</Button>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => setRenaming(null)}>{t('Cancel')}</Button>
                    </>
                  ) : confirmingDelete === p.id ? (
                    <>
                      <span className={styles.confirmText}>
                        {p.builtin ? t('Hide {name}?', { name: p.name }) : t('Delete {name}?', { name: p.name })}
                      </span>
                      <Button size="sm" variant="danger" disabled={busy} onClick={() => void doDelete(p)}>
                        {p.builtin ? t('Hide') : t('Delete')}
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmingDelete(null)}>{t('Cancel')}</Button>
                    </>
                  ) : (
                    <>
                      {!p.builtin && (
                        <Button size="sm" variant="ghost" disabled={busy}
                                onClick={() => { setRenaming(p.id); setRenameDraft(p.name); setConfirmingDelete(null); }}>
                          {t('Rename')}
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" disabled={busy}
                              onClick={() => { setConfirmingDelete(p.id); setRenaming(null); }}>
                        {p.builtin ? t('Hide') : t('Delete')}
                      </Button>
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {hidden.length > 0 && (
            <>
              <h4 className={styles.hiddenHead}>{t('Hidden built-ins')}</h4>
              <ul className={styles.manageList}>
                {hidden.map((h) => (
                  <li key={h.id} className={styles.manageRow}>
                    <span className={styles.manageName}>{h.name}</span>
                    <span className={styles.rowActions}>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => void doRestore(h.id)}>
                        {t('Restore')}
                      </Button>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {error && <p className={styles.errText} role="alert">{error}</p>}
        </div>
      </div>
    </div>
  );
}

function presetDot(p: CataloguePreset, catalogue: DesignerCatalogue): string {
  const colors = effectiveColors(resolvePreset(p, catalogue.defaults), catalogue);
  return `linear-gradient(135deg, ${colors.background} 50%, ${colors.band} 50%)`;
}
