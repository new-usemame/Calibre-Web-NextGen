/*
 * Compositor safety for icon animations.
 *
 * MEASURED DEFECT (Chrome DevTools trace, book detail route, mobile 390x844@3,
 * 20x CPU throttle): `.arrow` in HelpBanner.module.css ran
 * `animation: hbNudge 1.8s var(--ease) infinite` -- a `transform` keyframe --
 * directly on a lucide `<ArrowUpRight>` SVG. Chrome's frame reporter said
 * `has_compositor_animation: false, has_main_animation: true` on 944/944 update
 * frames: an SVG element does not get its own composited layer, so a transform
 * animation on one falls back to the MAIN thread and runs there for as long as
 * it is mounted -- style recalc -> prepaint -> layerize -> commit at ~60fps.
 * `AppShell` mounts the banner, so it was on every route.
 *
 *   3,251ms main thread / 19.87s, 1,169 style recalcs, 22 dropped frames, INP 125ms
 *   with the animation cancelled: 399ms, 0 style recalcs, 1 dropped frame, INP 12ms
 *   the same keyframes on a plain <div>: has_compositor_animation TRUE 809/809
 *
 * A Chrome trace of one page carrying both shapes at once emits
 * `compositeFailed: 1024` for the SVG target and `compositeFailed: 0` for the
 * span. (Measured in Chrome. Other engines were not measured; the invariant is
 * written for the engine we have evidence for, and costs nothing elsewhere.)
 *
 * The failure is silent -- the animation still looks right, it just moves to the
 * main thread -- so a trace catches it once and only a static gate keeps it from
 * coming back. This pins the GENERAL invariant rather than the single instance:
 *
 *   a keyframe animation that animates a compositor property (transform /
 *   opacity / the transform longhands) must NOT be applied via `className`
 *   directly to an SVG element. Put it on a wrapper element instead.
 *
 * Run: node --test frontend/tests/unit/iconAnimationCompositing.test.ts
 * (Fast Tests runs it through tests/unit/test_frontend_unit_suites_run.py.)
 *
 * POSITIVE CONTROL: the same analyzer is run over synthetic fixtures that must
 * be flagged and fixtures that must NOT be, so a harness that rejects
 * everything -- or accepts everything, or recognises only the one class name
 * this bug happened to use -- cannot masquerade as a working gate.
 *
 * KNOWN LIMITS, stated rather than implied. This is a static text scanner, not
 * a CSS/TS compiler, and it will NOT see:
 *   - a class reached through a spread (`{...{className: styles.spin}}`), a
 *     computed key (`styles[key]`), optional chaining, or a prop assembled in
 *     another module;
 *   - a component that forwards `className` down onto an SVG it renders, or a
 *     non-exported local `<svg>` component;
 *   - cascade: a later same-specificity `animation: none` that unconditionally
 *     cancels an earlier rule still reads as animated;
 *   - `@media` state: a class animated only inside a media query reads as
 *     animated, which is deliberate -- it genuinely animates in that state;
 *   - an animation applied by a descendant selector with no class on the icon
 *     (`.foo svg { animation: ... }`), which involves no `className` at all.
 * Two shapes that WOULD be invisible are instead made loud: an unresolvable
 * `animation: var(--x)` value, and native CSS nesting (see the flat-CSS guard).
 *
 * Uses Node's built-in runner + native type stripping, like the other suites
 * here: the frontend ships no test framework on purpose (hard rule 6).
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, existsSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, relative } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, '..', '..');
const SRC = resolve(FRONTEND, 'src');

/* ------------------------------------------------------------------ CSS --- */

/** Properties the compositor can animate off the main thread -- and precisely
 *  the ones that stop being compositable when the target is an SVG element. */
const COMPOSITOR_PROP = /(?:^|[;{\s])(?:transform|opacity|translate|rotate|scale)\s*:/;

/** `animation` / `animation-name`, and NOT `--animation`: the leading boundary
 *  refuses a custom property, which otherwise parses as a real declaration. */
const ANIMATION_DECL = /(?:^|[;{\s])animation(?:-name)?\s*:\s*([^;]*)/g;

const UNRESOLVED_KEYFRAMES = '<unresolved var()>';

function stripCssComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, ' ');
}

function wordRe(name: string): RegExp {
  return new RegExp(`(?:^|[^\\w-])${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:[^\\w-]|$)`);
}

/** Every `@keyframes` name in the sheet, and which of them touch a compositor
 *  property. Both are needed: the compositor set decides what to flag, the full
 *  set decides whether an `animation` value naming a keyframe is resolvable. */
export function keyframeNames(css: string): { all: Set<string>; compositor: Set<string> } {
  const text = stripCssComments(css);
  const all = new Set<string>();
  const compositor = new Set<string>();
  const re = /@(?:-\w+-)?keyframes\s+([\w-]+)\s*\{/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const bodyStart = re.lastIndex;
    let depth = 1;
    let i = bodyStart;
    while (i < text.length && depth > 0) {
      if (text[i] === '{') depth++;
      else if (text[i] === '}') depth--;
      i++;
    }
    all.add(match[1]);
    if (COMPOSITOR_PROP.test(text.slice(bodyStart, i - 1))) compositor.add(match[1]);
    re.lastIndex = i;
  }
  return { all, compositor };
}

/** One rule that animates a compositor property. `classes` are the classes of
 *  the selector's LAST COMPOUND and ALL of them must be present on an element
 *  for the rule to apply -- `.loading .spin` and `.loading.spin` used to mark
 *  `loading` as independently animated, which is a false positive. */
export interface AnimatedRule { classes: string[]; keyframes: string[] }

export function compositorAnimatedRules(css: string): AnimatedRule[] {
  const text = stripCssComments(css);
  const { all, compositor } = keyframeNames(text);
  const rules: AnimatedRule[] = [];

  // Only innermost rules match, which makes `@media` wrappers transparent and
  // skips `@keyframes` step blocks (they carry no `animation` declaration).
  const rule = /([^{}]+)\{([^{}]*)\}/g;
  let match: RegExpExecArray | null;
  while ((match = rule.exec(text)) !== null) {
    const selectorList = match[1].trim();
    if (selectorList.startsWith('@')) continue;

    ANIMATION_DECL.lastIndex = 0;
    const values: string[] = [];
    let decl: RegExpExecArray | null;
    while ((decl = ANIMATION_DECL.exec(match[2])) !== null) values.push(decl[1]);
    if (values.length === 0) continue;

    const keyframes: string[] = [];
    for (const value of values) {
      for (const name of compositor) if (wordRe(name).test(value)) keyframes.push(name);
      // A value we cannot resolve is reported rather than silently dropped:
      // `animation: var(--motion)` is a real animation with an invisible name.
      const namesAnyKind = [...all].some((name) => wordRe(name).test(value));
      if (!namesAnyKind && /var\(/.test(value)) keyframes.push(UNRESOLVED_KEYFRAMES);
    }
    if (keyframes.length === 0) continue;

    for (const selector of selectorList.split(',')) {
      const compound = selector.trim().split(/[\s>+~]+/).pop() ?? '';
      const classes = [...compound.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)].map((m) => m[1]);
      if (classes.length === 0) continue; // e.g. `.foo svg` -- no class on the target
      rules.push({ classes, keyframes: [...new Set(keyframes)] });
    }
  }
  return rules;
}

/** Union of every class that participates in a compositor animation. Used only
 *  by the non-vacuity checks; the violation scan uses the rules above. */
export function animatedClassNames(css: string): Set<string> {
  const out = new Set<string>();
  for (const r of compositorAnimatedRules(css)) for (const c of r.classes) out.add(c);
  return out;
}

/* ------------------------------------------------------------------ TSX --- */

interface ImportedName { local: string; imported: string; spec: string }

function namedImports(tsx: string): ImportedName[] {
  const out: ImportedName[] = [];
  const re = /import\s+(?:type\s+)?\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(tsx)) !== null) {
    for (const raw of match[1].split(',')) {
      const part = raw.trim();
      if (!part) continue;
      const halves = part.split(/\s+as\s+/);
      const imported = halves[0].trim().replace(/^type\s+/, '');
      const local = (halves[1] ?? halves[0]).trim().replace(/^type\s+/, '');
      if (imported && local) out.push({ local, imported, spec: match[2] });
    }
  }
  return out;
}

function defaultImports(tsx: string): Array<{ local: string; spec: string }> {
  const out: Array<{ local: string; spec: string }> = [];
  const re = /import\s+([A-Za-z_$][\w$]*)\s*(?:,\s*\{[^}]*\}\s*)?from\s*['"]([^'"]+)['"]/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(tsx)) !== null) out.push({ local: match[1], spec: match[2] });
  return out;
}

function namespaceImports(tsx: string): Array<{ local: string; spec: string }> {
  const out: Array<{ local: string; spec: string }> = [];
  const re = /import\s*\*\s*as\s+([A-Za-z_$][\w$]*)\s*from\s*['"]([^'"]+)['"]/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(tsx)) !== null) out.push({ local: match[1], spec: match[2] });
  return out;
}

/** Walk forward from `<Name` to the `>` that closes the opening tag, skipping
 *  over braces, strings and template literals so `size={a > b ? 1 : 2}` and
 *  `onClick={() => …}` do not terminate the scan early. */
function openingTag(text: string, tagStart: number): { attrs: string; end: number } | null {
  let i = tagStart + 1;
  while (i < text.length && /[\w$.]/.test(text[i])) i++;
  const attrsStart = i;
  let depth = 0;
  let quote: string | null = null;
  while (i < text.length) {
    const ch = text[i];
    if (quote !== null) {
      if (ch === '\\') { i += 2; continue; }
      if (ch === quote) quote = null;
      i++;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; i++; continue; }
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    else if (ch === '>' && depth === 0) return { attrs: text.slice(attrsStart, i), end: i };
    i++;
  }
  return null;
}

function openingTagAttributes(text: string, tagStart: number): string | null {
  return openingTag(text, tagStart)?.attrs ?? null;
}

function balancedEnd(text: string, openIndex: number, open: string, close: string): number {
  let depth = 0;
  let quote: string | null = null;
  let i = openIndex;
  while (i < text.length) {
    const ch = text[i];
    if (quote !== null) {
      if (ch === '\\') { i += 2; continue; }
      if (ch === quote) quote = null;
      i++;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; i++; continue; }
    if (ch === open) depth++;
    else if (ch === close) { depth--; if (depth === 0) return i + 1; }
    i++;
  }
  return -1;
}

/** Every `className=` expression in one opening tag's attribute text. */
export function classNameExpressions(attrs: string): string[] {
  const out: string[] = [];
  const re = /className\s*=\s*/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(attrs)) !== null) {
    const start = match.index + match[0].length;
    const ch = attrs[start];
    if (ch === '{') {
      const end = balancedEnd(attrs, start, '{', '}');
      if (end > 0) { out.push(attrs.slice(start + 1, end - 1)); re.lastIndex = end; }
    } else if (ch === '"' || ch === "'") {
      const end = attrs.indexOf(ch, start + 1);
      if (end > 0) { out.push(attrs.slice(start + 1, end)); re.lastIndex = end + 1; }
    }
  }
  return out;
}

/** `const cls = styles.spin` / `const { spin } = styles` / `const { spin: s } = styles`.
 *  Without this an alias defeats the whole gate: the animation still lands on
 *  the SVG and nothing sees it. */
export function classAliases(tsx: string, stylesIdent: string): Map<string, string> {
  const aliases = new Map<string, string>();
  const direct = new RegExp(
    `\\b(?:const|let|var)\\s+([A-Za-z_$][\\w$]*)\\s*(?::[^=]+)?=\\s*${stylesIdent}\\.([A-Za-z_$][\\w$]*)`,
    'g',
  );
  for (const m of tsx.matchAll(direct)) aliases.set(m[1], m[2]);

  const destructured = new RegExp(
    `\\b(?:const|let|var)\\s*\\{([^}]*)\\}\\s*=\\s*${stylesIdent}\\b`,
    'g',
  );
  for (const m of tsx.matchAll(destructured)) {
    for (const raw of m[1].split(',')) {
      const part = raw.trim();
      if (!part) continue;
      const [key, alias] = part.split(':').map((s) => s.trim());
      if (key) aliases.set(alias || key, key);
    }
  }
  return aliases;
}

function styleClassRefs(expression: string, stylesIdent: string, aliases: Map<string, string>): string[] {
  const out: string[] = [];
  const dotted = new RegExp(`\\b${stylesIdent}\\.([A-Za-z_$][\\w$]*)`, 'g');
  const bracketed = new RegExp(`\\b${stylesIdent}\\[\\s*['"]([^'"]+)['"]\\s*\\]`, 'g');
  for (const m of expression.matchAll(dotted)) out.push(m[1]);
  for (const m of expression.matchAll(bracketed)) out.push(m[1]);
  for (const [alias, cls] of aliases) {
    if (new RegExp(`(?:^|[^\\w$.])${alias}(?![\\w$])`).test(expression)) out.push(cls);
  }
  return out;
}

/** Exported components in this module whose own body returns an `<svg>` root --
 *  those ARE SVG elements at runtime, exactly like a lucide icon. Scoped per
 *  export: a module exporting both `Box` (a div) and `Mark` (an svg) must not
 *  mark `Box`. */
export function svgRootComponents(tsx: string): Set<string> {
  const names = new Set<string>();
  const decl = /export\s+(?:default\s+)?(?:function|const)\s+([A-Z][\w$]*)/g;
  const starts: Array<{ name: string; index: number }> = [];
  for (const m of tsx.matchAll(decl)) starts.push({ name: m[1], index: m.index ?? 0 });
  for (let i = 0; i < starts.length; i++) {
    const body = tsx.slice(starts[i].index, starts[i + 1]?.index ?? tsx.length);
    if (/(?:return|=>)\s*\(?\s*<svg[\s/>]/.test(body)) names.add(starts[i].name);
  }
  return names;
}

/** Lowercase SVG tags. The invariant is about SVG ELEMENTS, not only SVG roots:
 *  a compositor animation on a `<path>` inside a hand-written mark has the same
 *  defect. `KofiMark` already puts classes on its `<path>`s. */
const SVG_TAGS = [
  'svg', 'path', 'g', 'circle', 'ellipse', 'line', 'polygon', 'polyline', 'rect',
  'text', 'tspan', 'use', 'defs', 'mask', 'clipPath', 'symbol', 'marker', 'image',
  'foreignObject', 'linearGradient', 'radialGradient', 'stop', 'pattern',
];

export interface Violation {
  file: string;
  line: number;
  component: string;
  className: string;
  keyframes: string[];
}

export interface ScanInput {
  path: string;
  tsx: string;
  readCss: (absolutePath: string) => string | null;
  svgComponentsByModule?: Map<string, Set<string>>;
}

export function scanSource(input: ScanInput): Violation[] {
  const { path, tsx, readCss } = input;
  const svgByModule = input.svgComponentsByModule ?? new Map<string, Set<string>>();

  // Which JSX tag names render an SVG element?
  const iconComponents = new Set<string>(SVG_TAGS);
  for (const imp of namedImports(tsx)) {
    if (imp.spec === 'lucide-react') { iconComponents.add(imp.local); continue; }
    if (!imp.spec.startsWith('.')) continue;
    const resolved = resolveModule(path, imp.spec, svgByModule);
    if (resolved && svgByModule.get(resolved)?.has(imp.imported)) iconComponents.add(imp.local);
  }
  // `import * as Icons from 'lucide-react'` -> `<Icons.Loader2 …>`
  const namespaceTagPrefixes: string[] = [];
  for (const imp of namespaceImports(tsx)) {
    if (imp.spec === 'lucide-react') namespaceTagPrefixes.push(imp.local);
  }
  // `{ icon: Icon }` destructuring -- the codebase's dynamic-icon shape. This is
  // a heuristic and it can over-report; that is the deliberate direction, since
  // the remedy (a wrapper element) is harmless on a non-SVG too.
  for (const m of tsx.matchAll(/\bicon\s*:\s*([A-Z][\w$]*)/g)) iconComponents.add(m[1]);

  // Which local identifiers are CSS-module namespaces, and what do they animate?
  const modules: Array<{ ident: string; rules: AnimatedRule[]; aliases: Map<string, string> }> = [];
  for (const imp of defaultImports(tsx)) {
    if (!/\.css$/.test(imp.spec)) continue;
    const css = readCss(resolve(dirname(path), imp.spec));
    if (css === null) continue;
    modules.push({
      ident: imp.local,
      rules: compositorAnimatedRules(css),
      aliases: classAliases(tsx, imp.local),
    });
  }
  if (modules.length === 0) return [];

  const violations: Violation[] = [];
  const tagPatterns = [
    ...[...iconComponents].map((c) => ({ label: c, re: new RegExp(`<${c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w$.-])`, 'g') })),
    ...namespaceTagPrefixes.map((p) => ({ label: `${p}.*`, re: new RegExp(`<${p}\\.[A-Za-z][\\w$]*(?![\\w$.])`, 'g') })),
  ];

  for (const { label, re } of tagPatterns) {
    let match: RegExpExecArray | null;
    while ((match = re.exec(tsx)) !== null) {
      const attrs = openingTagAttributes(tsx, match.index);
      if (attrs === null) continue;
      for (const expression of classNameExpressions(attrs)) {
        for (const mod of modules) {
          const applied = new Set(styleClassRefs(expression, mod.ident, mod.aliases));
          if (applied.size === 0) continue;
          for (const rule of mod.rules) {
            if (!rule.classes.every((c) => applied.has(c))) continue;
            violations.push({
              file: path,
              line: tsx.slice(0, match.index).split('\n').length,
              component: label,
              className: rule.classes.join('.'),
              keyframes: rule.keyframes,
            });
          }
        }
      }
    }
  }
  return violations;
}

/* ------------------------------------------------- the wrapper contract --- */

/* Moving the animation onto a wrapper is only safe while the wrapper stays a
 * pure animation carrier that shrink-wraps exactly one icon. Those preservation
 * properties were MEASURED once (identical rendered geometry at 1200px and
 * 390px against the pre-fix markup); these assertions are what stops them
 * drifting afterwards. The population is DERIVED from the sources -- a
 * hand-maintained list of call sites is the false-green this project has been
 * bitten by before. */

/** Declarations a compositor-animation wrapper class may carry. Anything else
 *  (width/height/padding/margin/border/position/overflow/transform/inset) would
 *  change the box the icon used to occupy, which is exactly what the wrapper
 *  must never do. */
const WRAPPER_SAFE_PROPS = new Set([
  'display', 'align-items', 'justify-content', 'color', 'line-height',
  'flex', 'flex-grow', 'flex-shrink', 'flex-basis', 'flex-none', 'vertical-align',
  'animation', 'animation-name', 'animation-duration', 'animation-timing-function',
  'animation-delay', 'animation-iteration-count', 'animation-direction',
  'animation-fill-mode', 'animation-play-state', 'will-change',
]);

export interface WrapperSite {
  file: string;
  line: number;
  cls: string;
  wrapperDecls: Array<[string, string]>;
  innerHtml: string;
  iconTags: string[];
  nonIconChildren: string[];
  iconAttrs: string[];
}

/** Every declaration of rules whose last compound is exactly `.cls`. */
function declarationsForClass(css: string, cls: string): Array<[string, string]> {
  const text = stripCssComments(css);
  const out: Array<[string, string]> = [];
  const rule = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = rule.exec(text)) !== null) {
    const selectorList = m[1].trim();
    if (selectorList.startsWith('@')) continue;
    for (const selector of selectorList.split(',')) {
      const compound = selector.trim().split(/[\s>+~]+/).pop() ?? '';
      const classes = [...compound.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)].map((c) => c[1]);
      if (classes.length !== 1 || classes[0] !== cls) continue;
      if (/[:[]/.test(compound.replace(/^\.[\w-]+/, ''))) continue; // skip :hover etc.
      for (const decl of m[2].split(';')) {
        const idx = decl.indexOf(':');
        if (idx < 0) continue;
        out.push([decl.slice(0, idx).trim(), decl.slice(idx + 1).trim()]);
      }
    }
  }
  return out;
}

/** Find `<span className={…animated class…}> … </span>` wrappers. */
export function animationWrappers(input: ScanInput): WrapperSite[] {
  const { path, tsx, readCss } = input;
  const iconNames = new Set<string>(SVG_TAGS);
  for (const imp of namedImports(tsx)) if (imp.spec === 'lucide-react') iconNames.add(imp.local);

  const sites: WrapperSite[] = [];
  for (const imp of defaultImports(tsx)) {
    if (!/\.css$/.test(imp.spec)) continue;
    const css = readCss(resolve(dirname(path), imp.spec));
    if (css === null) continue;
    const rules = compositorAnimatedRules(css);
    const aliases = classAliases(tsx, imp.local);

    const spanRe = /<span(?![\w$.-])/g;
    let m: RegExpExecArray | null;
    while ((m = spanRe.exec(tsx)) !== null) {
      const tag = openingTag(tsx, m.index);
      if (tag === null) continue;
      const applied = new Set(
        classNameExpressions(tag.attrs).flatMap((e) => styleClassRefs(e, imp.local, aliases)),
      );
      if (applied.size === 0) continue;
      const rule = rules.find((r) => r.classes.every((c) => applied.has(c)));
      if (!rule) continue;

      // children, up to the matching </span>
      let depth = 1;
      let i = tag.end + 1;
      const inner = /<\/?span(?![\w$.-])/g;
      inner.lastIndex = i;
      let close = tsx.length;
      let child: RegExpExecArray | null;
      while ((child = inner.exec(tsx)) !== null) {
        if (tsx[child.index + 1] === '/') { depth--; if (depth === 0) { close = child.index; break; } }
        else depth++;
      }
      const innerHtml = tsx.slice(tag.end + 1, close);
      const iconTags: string[] = [];
      const nonIconChildren: string[] = [];
      const iconAttrs: string[] = [];
      for (const el of innerHtml.matchAll(/<([A-Za-z][\w$.]*)/g)) {
        if (iconNames.has(el[1])) {
          iconTags.push(el[1]);
          iconAttrs.push(openingTagAttributes(innerHtml, el.index ?? 0) ?? '');
        } else nonIconChildren.push(el[1]);
      }
      // text that is not whitespace and not a JSX expression container
      const bare = innerHtml.replace(/<[^>]*>/g, ' ').replace(/\{[^}]*\}/g, ' ').trim();
      if (bare.length > 0) nonIconChildren.push(`text:${bare.slice(0, 30)}`);

      sites.push({
        file: path,
        line: tsx.slice(0, m.index).split('\n').length,
        cls: rule.classes.join('.'),
        wrapperDecls: rule.classes.flatMap((c) => declarationsForClass(css, c)),
        innerHtml,
        iconTags,
        nonIconChildren,
        iconAttrs,
      });
    }
  }
  return sites;
}

function resolveModule(
  fromFile: string,
  spec: string,
  known?: Map<string, Set<string>>,
): string | null {
  const base = resolve(dirname(fromFile), spec);
  const candidates = [base, `${base}.tsx`, `${base}.ts`, resolve(base, 'index.tsx')];
  // Prefer a module we already analysed -- that keeps the analyzer testable
  // against in-memory sources, so the <svg>-root discovery is exercised for
  // real rather than through an injected map.
  for (const candidate of candidates) if (known?.has(candidate)) return candidate;
  for (const candidate of candidates) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

/* ------------------------------------------------------ real-project scan --- */

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = resolve(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

const ALL_FILES = walk(SRC);
const TSX_FILES = ALL_FILES.filter((f) => f.endsWith('.tsx'));
const CSS_FILES = ALL_FILES.filter((f) => f.endsWith('.css'));

const SVG_COMPONENTS_BY_MODULE = new Map<string, Set<string>>();
for (const file of TSX_FILES) {
  const names = svgRootComponents(readFileSync(file, 'utf8'));
  if (names.size > 0) SVG_COMPONENTS_BY_MODULE.set(file, names);
}

const readCssFromDisk = (p: string): string | null =>
  existsSync(p) && statSync(p).isFile() ? readFileSync(p, 'utf8') : null;

const REAL_VIOLATIONS = TSX_FILES.flatMap((file) =>
  scanSource({
    path: file,
    tsx: readFileSync(file, 'utf8'),
    readCss: readCssFromDisk,
    svgComponentsByModule: SVG_COMPONENTS_BY_MODULE,
  }),
);

const REAL_WRAPPERS = TSX_FILES.flatMap((file) =>
  animationWrappers({ path: file, tsx: readFileSync(file, 'utf8'), readCss: readCssFromDisk }),
);

/* ------------------------------------------------------------- fixtures --- */

const FIXTURE_CSS = `
.arrow { color: red; flex: none; animation: fxNudge 1.8s var(--ease) infinite; }
@keyframes fxNudge { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(2px, -2px); } }

/* a SECOND, differently-named compositor animation: a scanner hard-coded to
   'arrow'/'fxNudge' must not be able to pass the suite */
.whirl { animation: fxTwirl 1s linear infinite; }
@keyframes fxTwirl { to { transform: rotate(360deg); } }

.tinted { animation: fxTint 2s linear infinite; }
@keyframes fxTint { to { color: blue; } }
.plain { color: green; }
.varDriven { --motion: fxNudge 1s linear infinite; animation: var(--motion); }
.customProp { --animation: fxNudge 1s; }
.loading .descendant { animation: fxTwirl 1s; }
.loading.compound { animation: fxTwirl 1s; }
@media (prefers-reduced-motion: reduce) { .arrow { animation: none; } }
`;

function fixtureScan(tsx: string, svgModules?: Map<string, Set<string>>): Violation[] {
  return scanSource({
    path: resolve(SRC, 'components', '__fixture__.tsx'),
    tsx,
    readCss: (p) => (p.endsWith('Fixture.module.css') ? FIXTURE_CSS : null),
    svgComponentsByModule: svgModules,
  });
}

const HEAD = "import { ArrowUpRight } from 'lucide-react';\nimport styles from './Fixture.module.css';\n";

/* ---------------------------------------------------------------- tests --- */

describe('analyzer positive control', () => {
  test('flags a transform keyframe applied straight to a lucide icon', () => {
    const found = fixtureScan(
      `${HEAD}export const X = () => <ArrowUpRight size={15} className={styles.arrow} aria-hidden="true" />;`,
    );
    assert.equal(found.length, 1, `expected one violation, got ${JSON.stringify(found)}`);
    assert.equal(found[0].component, 'ArrowUpRight');
    assert.equal(found[0].className, 'arrow');
    assert.deepEqual(found[0].keyframes, ['fxNudge']);
  });

  test('flags a DIFFERENT class and keyframe too — not just the one this bug used', () => {
    const found = fixtureScan(`${HEAD}export const X = () => <ArrowUpRight className={styles.whirl} />;`);
    assert.equal(found.length, 1, JSON.stringify(found));
    assert.equal(found[0].className, 'whirl');
    assert.deepEqual(found[0].keyframes, ['fxTwirl']);
  });

  test('flags it through a template literal and a ternary too', () => {
    assert.equal(
      fixtureScan(`${HEAD}export const X = () => <ArrowUpRight className={\`\${styles.plain} \${styles.arrow}\`} />;`).length,
      1,
    );
    assert.equal(
      fixtureScan(`${HEAD}export const X = ({ on }: { on: boolean }) => <ArrowUpRight className={on ? styles.arrow : undefined} />;`).length,
      1,
    );
  });

  test('follows a local class alias — an alias must not defeat the gate', () => {
    assert.equal(
      fixtureScan(`${HEAD}const cls = styles.arrow;\nexport const X = () => <ArrowUpRight className={cls} />;`).length,
      1,
    );
    assert.equal(
      fixtureScan(`${HEAD}const { arrow } = styles;\nexport const X = () => <ArrowUpRight className={arrow} />;`).length,
      1,
    );
    assert.equal(
      fixtureScan(`${HEAD}const { arrow: nudge } = styles;\nexport const X = () => <ArrowUpRight className={nudge} />;`).length,
      1,
    );
  });

  test('flags a bare <svg>, an SVG child element, and a namespace lucide import', () => {
    assert.equal(
      fixtureScan(`import styles from './Fixture.module.css';\nexport const X = () => <svg className={styles.arrow} />;`).length,
      1,
    );
    assert.equal(
      fixtureScan(`import styles from './Fixture.module.css';\nexport const X = () => <svg><path className={styles.arrow} d="M0 0" /></svg>;`).length,
      1,
      'a compositor animation on an SVG child element is the same defect',
    );
    assert.equal(
      fixtureScan(`import * as Icons from 'lucide-react';\nimport styles from './Fixture.module.css';\nexport const X = () => <Icons.Loader2 className={styles.arrow} />;`).length,
      1,
    );
  });

  test('discovers a local <svg>-root component for real, and does not over-claim', () => {
    // exercises svgRootComponents itself rather than injecting a hand-built map
    const markSource =
      "export const Box = () => <div className=\"x\" />;\nexport const Mark = () => <svg viewBox=\"0 0 1 1\" />;\n";
    const names = svgRootComponents(markSource);
    assert.deepEqual([...names], ['Mark'], 'only the <svg>-rooted export is an SVG component');

    const markPath = resolve(SRC, 'components', 'Mark.tsx');
    const modules = new Map([[markPath, names]]);
    assert.equal(
      fixtureScan(`import { Mark } from './Mark';\nimport styles from './Fixture.module.css';\nexport const X = () => <Mark className={styles.arrow} />;`, modules).length,
      1,
    );
    assert.equal(
      fixtureScan(`import { Box } from './Mark';\nimport styles from './Fixture.module.css';\nexport const X = () => <Box className={styles.arrow} />;`, modules).length,
      0,
      'a non-SVG export from the same module must not be flagged',
    );
  });

  test('flags an animation whose name hides behind var() rather than missing it', () => {
    const found = fixtureScan(`${HEAD}export const X = () => <ArrowUpRight className={styles.varDriven} />;`);
    assert.equal(found.length, 1, JSON.stringify(found));
    assert.deepEqual(found[0].keyframes, [UNRESOLVED_KEYFRAMES]);
  });

  test('does NOT flag the same animation on a wrapper element — this is the fix shape', () => {
    assert.deepEqual(
      fixtureScan(
        `${HEAD}export const X = () => <span className={styles.arrow}><ArrowUpRight size={15} aria-hidden="true" /></span>;`,
      ),
      [],
    );
  });

  test('does NOT flag a non-compositor animation, an unanimated class, or a --animation custom property', () => {
    assert.deepEqual(fixtureScan(`${HEAD}export const X = () => <ArrowUpRight className={styles.tinted} />;`), []);
    assert.deepEqual(fixtureScan(`${HEAD}export const X = () => <ArrowUpRight className={styles.plain} />;`), []);
    assert.deepEqual(fixtureScan(`${HEAD}export const X = () => <ArrowUpRight className={styles.customProp} />;`), []);
  });

  test('does NOT attribute a compound or descendant selector to its other classes', () => {
    assert.deepEqual(
      fixtureScan(`${HEAD}export const X = () => <ArrowUpRight className={styles.loading} />;`),
      [],
      '`.loading .descendant` / `.loading.compound` do not animate `.loading` on its own',
    );
    assert.equal(
      fixtureScan(`${HEAD}export const X = () => <ArrowUpRight className={\`\${styles.loading} \${styles.compound}\`} />;`).length,
      1,
      'both halves of `.loading.compound` present -> it really does animate',
    );
  });

  test('does NOT trip on JSX that merely contains ">" inside an attribute', () => {
    assert.deepEqual(
      fixtureScan(
        `${HEAD}export const X = ({ n }: { n: number }) => <ArrowUpRight size={n > 2 ? 16 : 12} onClick={() => undefined} className={styles.plain} />;`,
      ),
      [],
    );
  });
});

describe('CSS analysis', () => {
  // The parse reads INNERMOST rules, which is what makes `@media` wrappers
  // transparent -- and is exactly why native CSS nesting would blind it: in
  // `.a { animation: x 1s; &:hover { … } }` the outer block is no longer
  // innermost and its `animation` declaration is never seen. The SPA writes
  // flat CSS today; pin that, so the day someone adopts nesting this fails
  // loudly instead of quietly passing everything.
  test('the SPA writes flat CSS — nesting would blind the innermost-rule parse', () => {
    const nesting: string[] = [];
    for (const file of CSS_FILES) {
      const text = readFileSync(file, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, ' ')   // comments
        .replace(/"(?:[^"\\]|\\.)*"/g, ' ')  // "R&D" is not nesting
        .replace(/'(?:[^'\\]|\\.)*'/g, ' ')
        .replace(/url\([^)]*\)/g, ' ');
      if (/&/.test(text)) nesting.push(relative(FRONTEND, file));
    }
    assert.deepEqual(nesting, [], `CSS nesting found — teach compositorAnimatedRules about it:\n${nesting.join('\n')}`);
  });

  test('reads the real HelpBanner keyframes', () => {
    const css = readFileSync(resolve(SRC, 'components', 'HelpBanner.module.css'), 'utf8');
    const { compositor } = keyframeNames(css);
    assert.ok(compositor.has('hbNudge'), `hbNudge missing from ${[...compositor].join(', ')}`);
    assert.ok(compositor.has('hbSlideDown'));
    assert.ok(animatedClassNames(css).has('arrow'), 'the .arrow class should still carry the nudge');
  });
});

describe('the SPA never animates a compositor property on an SVG element', () => {
  test('the scan is not vacuous', () => {
    assert.ok(TSX_FILES.length > 20, `only ${TSX_FILES.length} .tsx files found under ${SRC}`);
    assert.ok(CSS_FILES.length > 10, `only ${CSS_FILES.length} .css files found under ${SRC}`);
    const animatedSomewhere = CSS_FILES.reduce(
      (n, f) => n + animatedClassNames(readFileSync(f, 'utf8')).size,
      0,
    );
    assert.ok(animatedSomewhere > 5, `only ${animatedSomewhere} compositor-animated classes found — the CSS parse is broken`);
    const lucideUsers = TSX_FILES.filter((f) => readFileSync(f, 'utf8').includes("from 'lucide-react'"));
    assert.ok(lucideUsers.length > 10, `only ${lucideUsers.length} files import lucide-react`);
    assert.ok(SVG_COMPONENTS_BY_MODULE.size > 0, 'no local <svg>-root component discovered — svgRootComponents is broken');
  });

  test('no animated class is applied directly to an SVG element', () => {
    const report = REAL_VIOLATIONS.map(
      (v) => `${relative(FRONTEND, v.file)}:${v.line}  <${v.component} className={styles.${v.className}}>  ` +
        `carries @keyframes ${v.keyframes.join(', ')}, which animates a compositor property. ` +
        `An SVG target gets no compositor layer, so it falls back to the main thread. ` +
        `Move the animation to a wrapper element.`,
    );
    assert.deepEqual(report, [], `\n${report.join('\n')}\n`);
  });
});

describe('every animation wrapper keeps the contract that makes it safe', () => {
  const named = (s: WrapperSite) => `${relative(FRONTEND, s.file)}:${s.line} .${s.cls}`;
  const iconWrappers = REAL_WRAPPERS.filter((s) => s.iconTags.length > 0);

  test('the wrapper population is derived and non-empty', () => {
    // Derived from the sources, never a hand-maintained list of call sites --
    // that is the false-green this project has shipped before.
    // 14 = every call site this fix moved onto a wrapper. A floor rather than a
    // list, so the population stays derived; lower it deliberately (and say why)
    // if a wrapper is ever legitimately removed, so losing one cannot be silent.
    assert.ok(
      iconWrappers.length >= 14,
      `expected at least the 14 icon-animation wrappers, found ${iconWrappers.length}:\n` +
        iconWrappers.map(named).join('\n'),
    );
  });

  test('each wrapper class shrink-wraps — display:inline-flex, so the box is the glyph', () => {
    const bad = iconWrappers.filter(
      (s) => !s.wrapperDecls.some(([prop, value]) => prop === 'display' && value === 'inline-flex'),
    );
    assert.deepEqual(bad.map(named), [], 'a wrapper without display:inline-flex is a new box around the icon');
  });

  test('each wrapper class stays a pure animation carrier and cannot change the box', () => {
    const offenders: string[] = [];
    for (const s of iconWrappers) {
      for (const [prop] of s.wrapperDecls) {
        if (!WRAPPER_SAFE_PROPS.has(prop) && !prop.startsWith('--')) {
          offenders.push(`${named(s)} declares \`${prop}\``);
        }
      }
    }
    assert.deepEqual(
      [...new Set(offenders)],
      [],
      'geometry-affecting declarations on an animation wrapper change the box the icon used to occupy',
    );
  });

  test('a wrapper that holds an icon holds ONLY that icon', () => {
    const bad = iconWrappers
      .filter((s) => s.iconTags.length !== 1 || s.nonIconChildren.length > 0)
      .map((s) => `${named(s)} -> icons=[${s.iconTags}] other=[${s.nonIconChildren}]`);
    assert.deepEqual(bad, [], 'extra content inside the wrapper is animated along with the icon');
  });

  test('each wrapped icon keeps an explicit size', () => {
    const bad = iconWrappers
      .filter((s) => !s.iconAttrs.some((a) => /\bsize\s*=\s*\{/.test(a)) && !s.iconAttrs.some((a) => /\bwidth\s*=/.test(a)))
      .map(named);
    assert.deepEqual(bad, [], 'an icon with no explicit size takes its size from the wrapper instead');
  });

  test('a wrapped icon that is hidden from assistive tech is hidden consistently', () => {
    // NOT an assertion that every wrapped icon is hidden: nine of them never
    // carried these attributes (pre-existing, filed as F-6543d1, deliberately
    // untouched by the compositing fix). What IS pinned is that an icon which
    // declares one of the pair declares the other, so neither can be dropped
    // on its own.
    const bad: string[] = [];
    for (const s of iconWrappers) {
      for (const attrs of s.iconAttrs) {
        const hidden = /aria-hidden\s*=\s*"true"/.test(attrs);
        const unfocusable = /focusable\s*=\s*\{false\}/.test(attrs);
        if (hidden !== unfocusable) bad.push(`${named(s)} aria-hidden=${hidden} focusable={false}=${unfocusable}`);
      }
    }
    assert.deepEqual(bad, [], 'aria-hidden and focusable={false} must travel together');
  });

  test('every compositor-animated class applied to a wrapper is applied to a wrapper ONLY', () => {
    // the mirror of the main gate: the class must not also be reachable on an
    // SVG somewhere else in the tree
    assert.deepEqual(REAL_VIOLATIONS, []);
  });
});

describe('the announcement banner nudge specifically', () => {
  const tsx = readFileSync(resolve(SRC, 'components', 'AnnouncementBanner.tsx'), 'utf8');

  test('puts the nudge on a wrapper, not on <ArrowUpRight>', () => {
    const arrowIndex = tsx.indexOf('<ArrowUpRight');
    const attrs = openingTagAttributes(tsx, arrowIndex);
    assert.ok(attrs !== null, 'no <ArrowUpRight> in AnnouncementBanner.tsx any more');
    assert.ok(
      !attrs.includes('styles.arrow'),
      `the nudge is still on the SVG itself: <ArrowUpRight${attrs}>`,
    );
    assert.ok(
      /<span className=\{styles\.arrow\}>/.test(tsx),
      'expected a <span className={styles.arrow}> wrapper carrying the animation',
    );
    assert.ok(attrs.includes('aria-hidden="true"'), 'the icon must stay decorative');
    assert.ok(attrs.includes('focusable={false}'), 'the icon must stay out of the tab order');
  });

  test('reduced motion still switches the nudge off', () => {
    const css = readFileSync(resolve(SRC, 'components', 'HelpBanner.module.css'), 'utf8');
    const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));
    assert.ok(reduced.length > 0, 'the reduced-motion block disappeared');
    assert.ok(/\.arrow\s*\{[^}]*animation:\s*none/.test(reduced), '.arrow no longer honours reduced motion');
    assert.ok(/\.banner\s*\{[^}]*animation:\s*none/.test(reduced), '.banner no longer honours reduced motion');
  });
});
