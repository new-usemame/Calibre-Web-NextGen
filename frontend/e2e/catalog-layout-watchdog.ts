import type { Page, TestInfo } from '@playwright/test';

export const CATALOG_LAYOUT_HEAL_MS = 2_000;

export type CatalogLayoutInvariant = 'resolved-track-count' | 'accepted-column-count';
export type CatalogLayoutDurationEvidence =
  | 'measured-transition'
  | 'unmeasured-safety-net';

export interface CatalogLayoutViolation {
  invariant: CatalogLayoutInvariant;
  firstSeenMs: number;
  lastSeenMs: number;
  healedAtMs: number | null;
  badSampleCount: number;
  /** Bad time supported by measured transitions; safety-only episodes report zero. */
  observedBadMs: number;
  /** Sum of supported bad durations during this page-load convergence window. */
  cumulativeObservedBadMs: number;
  durationEvidence: CatalogLayoutDurationEvidence;
  width: number;
  gap: number;
  min: number;
  expected: number;
  actual: number;
  resolvedTemplate: string;
}

export interface CatalogLayoutSample {
  atMs: number;
  width: number;
  gap: number;
  min: number;
  expected: number;
  resolvedTracks: number;
  acceptedColumns: number | null;
  resolvedTemplate: string;
}

export interface CatalogLayoutWatchdogSnapshot {
  startedAtEpochMs: number;
  sampledFrames: number;
  latest: CatalogLayoutSample | null;
  violations: CatalogLayoutViolation[];
  unhealed: CatalogLayoutViolation[];
}

interface CatalogLayoutWatchdogApi {
  snapshot: () => CatalogLayoutWatchdogSnapshot;
}

type WatchdogWindow = Window & {
  __catalogLayoutWatchdog?: CatalogLayoutWatchdogApi;
};

/**
 * Install before navigation so direct grid mutations can be timestamped in the
 * test realm before application code runs. Observers and synchronous DOM/CSSOM
 * hooks own exact transition evidence. requestAnimationFrame is a diagnostic
 * safety net only and never infers duration between sampled endpoints.
 */
export async function installCatalogLayoutWatchdog(page: Page): Promise<void> {
  await page.addInitScript(({ healMs }) => {
    const watchdogWindow = window as WatchdogWindow;
    const startedAtEpochMs = Date.now();
    const startedAt = performance.now();
    const violations: CatalogLayoutViolation[] = [];
    const active = new Map<CatalogLayoutInvariant, {
      violation: CatalogLayoutViolation;
      transitionStarted: boolean;
      cumulativeBeforeMs: number;
    }>();
    const cumulativeObservedBadMs = new Map<CatalogLayoutInvariant, number>();
    const RESOLVED_LENGTH = /^(?:0|[1-9]\d*)(?:\.\d+)?px$/;
    let sampledFrames = 0;
    let latest: CatalogLayoutSample | null = null;
    let grid: HTMLElement | null = null;
    let gridMutationObserver: MutationObserver | null = null;
    let gridResizeObserver: ResizeObserver | null = null;
    type TransitionEvidence = boolean | ReadonlySet<CatalogLayoutInvariant>;

    const now = () => performance.now() - startedAt;
    const recordHealthy = (
      invariant: CatalogLayoutInvariant,
      atMs: number,
      transitionObserved: boolean,
    ) => {
      const current = active.get(invariant);
      if (!current) return;

      const measured = current.transitionStarted && transitionObserved;
      const duration = measured ? Math.max(0, atMs - current.violation.firstSeenMs) : 0;
      const cumulative = current.cumulativeBeforeMs + duration;
      cumulativeObservedBadMs.set(invariant, cumulative);
      current.violation.healedAtMs = atMs;
      current.violation.observedBadMs = duration;
      current.violation.cumulativeObservedBadMs = cumulative;
      current.violation.durationEvidence = measured
        ? 'measured-transition'
        : 'unmeasured-safety-net';
      active.delete(invariant);
    };
    const recordViolation = (
      invariant: CatalogLayoutInvariant,
      sample: CatalogLayoutSample,
      actual: number,
      transitionObserved: boolean,
    ) => {
      const current = active.get(invariant);
      if (current) {
        current.violation.badSampleCount += 1;
        current.violation.lastSeenMs = sample.atMs;
        current.violation.width = sample.width;
        current.violation.gap = sample.gap;
        current.violation.min = sample.min;
        current.violation.expected = sample.expected;
        current.violation.actual = actual;
        current.violation.resolvedTemplate = sample.resolvedTemplate;
        return;
      }
      const cumulative = cumulativeObservedBadMs.get(invariant) ?? 0;
      const violation: CatalogLayoutViolation = {
        invariant,
        firstSeenMs: sample.atMs,
        lastSeenMs: sample.atMs,
        healedAtMs: null,
        badSampleCount: 1,
        observedBadMs: 0,
        cumulativeObservedBadMs: cumulative,
        durationEvidence: transitionObserved
          ? 'measured-transition'
          : 'unmeasured-safety-net',
        width: sample.width,
        gap: sample.gap,
        min: sample.min,
        expected: sample.expected,
        actual,
        resolvedTemplate: sample.resolvedTemplate,
      };
      violations.push(violation);
      active.set(invariant, {
        violation,
        transitionStarted: transitionObserved,
        cumulativeBeforeMs: cumulative,
      });
    };

    const readLayoutSample = (): CatalogLayoutSample | null => {
      if (!grid) return null;
      const style = getComputedStyle(grid);
      const width = grid.getBoundingClientRect().width;
      const min = Number.parseFloat(style.getPropertyValue('--catalog-grid-min'));
      const gap = Number.parseFloat(style.columnGap);
      const resolvedTemplate = style.gridTemplateColumns.trim();
      const resolved = resolvedTemplate.split(/\s+/).filter(Boolean);

      // A zero CSS minimum identifies the fixed-track mobile branch. Invalid
      // geometry or unresolved repeat()/none output is not a laid-out auto-fill
      // grid, so the invariant does not apply yet.
      if (!(width > 0)
        || !(min > 0)
        || !Number.isFinite(gap)
        || gap < 0
        || resolved.length === 0
        || !resolved.every((track) => RESOLVED_LENGTH.test(track))) return null;

      const atMs = now();
      const expected = Math.max(1, Math.floor((width + gap) / (min + gap)));
      const acceptedRaw = grid.dataset.catalogColumnCount;
      const acceptedColumns = acceptedRaw === undefined ? null : Number.parseInt(acceptedRaw, 10);
      return {
        atMs,
        width,
        gap,
        min,
        expected,
        resolvedTracks: resolved.length,
        acceptedColumns: Number.isInteger(acceptedColumns) ? acceptedColumns : null,
        resolvedTemplate,
      };
    };
    const transitionObservedFor = (
      evidence: TransitionEvidence,
      invariant: CatalogLayoutInvariant,
    ) => typeof evidence === 'boolean' ? evidence : evidence.has(invariant);
    const invariantIsHealthy = (
      currentSample: CatalogLayoutSample,
      invariant: CatalogLayoutInvariant,
    ) => invariant === 'resolved-track-count'
      ? currentSample.resolvedTracks === currentSample.expected
      : currentSample.acceptedColumns === null
        || currentSample.acceptedColumns === currentSample.expected;
    const recordSample = (
      currentSample: CatalogLayoutSample | null,
      evidence: TransitionEvidence,
    ) => {
      sampledFrames += 1;
      if (!currentSample) return;
      latest = currentSample;

      if (invariantIsHealthy(currentSample, 'resolved-track-count')) {
        recordHealthy(
          'resolved-track-count',
          currentSample.atMs,
          transitionObservedFor(evidence, 'resolved-track-count'),
        );
      } else {
        recordViolation(
          'resolved-track-count',
          currentSample,
          currentSample.resolvedTracks,
          transitionObservedFor(evidence, 'resolved-track-count'),
        );
      }

      if (invariantIsHealthy(currentSample, 'accepted-column-count')) {
        recordHealthy(
          'accepted-column-count',
          currentSample.atMs,
          transitionObservedFor(evidence, 'accepted-column-count'),
        );
      } else {
        recordViolation(
          'accepted-column-count',
          currentSample,
          currentSample.acceptedColumns!,
          transitionObservedFor(evidence, 'accepted-column-count'),
        );
      }
    };
    const sample = (evidence: TransitionEvidence) => {
      recordSample(readLayoutSample(), evidence);
    };

    const attachGrid = (candidate?: HTMLElement, transitionObserved = false) => {
      const nextGrid = candidate
        ?? document.querySelector<HTMLElement>('[data-testid="catalog-grid"]');
      if (nextGrid === grid) return;

      gridMutationObserver?.disconnect();
      gridResizeObserver?.disconnect();
      grid = nextGrid;
      if (!grid) return;

      // MutationObserver reports that markup changed, not which invariant input
      // changed across the mutation. Synchronous test-realm hooks own exact
      // evidence; observer delivery is a diagnostic fallback for other realms.
      gridMutationObserver = new MutationObserver(() => sample(false));
      gridMutationObserver.observe(grid, {
        attributes: true,
        attributeFilter: ['style', 'class', 'data-catalog-column-count'],
        childList: true,
      });
      gridResizeObserver = new ResizeObserver(() => sample(true));
      gridResizeObserver.observe(grid);
      sample(transitionObserved);
    };

    const isCatalogGrid = (element: Element): element is HTMLElement => element instanceof HTMLElement
      && element.getAttribute('data-testid') === 'catalog-grid';
    const isStylesheetElement = (node: Node): node is HTMLStyleElement | HTMLLinkElement =>
      node instanceof HTMLStyleElement || node instanceof HTMLLinkElement;
    const stylesheetElementsWithin = (node: Node): Array<HTMLStyleElement | HTMLLinkElement> => {
      if (isStylesheetElement(node)) return [node];
      return node instanceof Element
        ? [...node.querySelectorAll<HTMLStyleElement | HTMLLinkElement>('style, link')]
        : [];
    };
    const stylesheetElementCausality = new WeakMap<HTMLStyleElement | HTMLLinkElement, boolean>();

    const fontFamilyCanAffectGrid = (family: string) => {
      if (!grid || !family.trim()) return false;
      const normalize = (value: string) => value.trim().replace(/^['"]|['"]$/g, '').toLowerCase();
      const target = normalize(family);
      return getComputedStyle(grid).fontFamily.split(',').some((candidate) =>
        normalize(candidate) === target);
    };
    const stylesheetIsActive = (sheet: CSSStyleSheet): boolean => {
      if (sheet.disabled) return false;
      const owner = sheet.ownerNode;
      if (owner instanceof Element && !owner.isConnected) return false;
      if (owner instanceof HTMLLinkElement && !owner.relList.contains('stylesheet')) return false;
      if ((owner instanceof HTMLStyleElement || owner instanceof HTMLLinkElement)
        && owner.media && !matchMedia(owner.media).matches) return false;
      if (!owner) {
        const ownerRule = sheet.ownerRule;
        if (typeof CSSImportRule !== 'undefined' && ownerRule instanceof CSSImportRule) {
          if (ownerRule.media.mediaText && !matchMedia(ownerRule.media.mediaText).matches) return false;
          return ownerRule.parentStyleSheet ? stylesheetIsActive(ownerRule.parentStyleSheet) : false;
        }
        return document.adoptedStyleSheets.includes(sheet);
      }
      return true;
    };
    const ruleContextCanApply = (rule: CSSRule) => {
      if (rule.parentStyleSheet && !stylesheetIsActive(rule.parentStyleSheet)) return false;
      let parent = rule.parentRule;
      while (parent) {
        if (typeof CSSMediaRule !== 'undefined' && parent instanceof CSSMediaRule
          && !matchMedia(parent.conditionText).matches) return false;
        if (typeof CSSSupportsRule !== 'undefined' && parent instanceof CSSSupportsRule
          && !CSS.supports(parent.conditionText)) return false;
        // There is no browser API that answers whether a CSSContainerRule is
        // currently active for this element. Treat it as diagnostic rather than
        // inventing exact causality from a merely matching nested selector.
        if (typeof CSSContainerRule !== 'undefined' && parent instanceof CSSContainerRule) {
          return false;
        }
        parent = parent.parentRule;
      }
      return true;
    };
    const ruleCanAffectGrid = (rule: CSSRule): boolean => {
      if (!grid || !ruleContextCanApply(rule)) return false;
      if (typeof CSSStyleRule !== 'undefined' && rule instanceof CSSStyleRule) {
        try {
          return grid.matches(rule.selectorText);
        } catch {
          // A thrown selector test is unknown, not proof. Marking it causal
          // could recreate the exact false red fixed here if an unrelated rule
          // merely followed an earlier unhooked grid change.
          return false;
        }
      }
      if (typeof CSSFontFaceRule !== 'undefined' && rule instanceof CSSFontFaceRule) {
        return fontFamilyCanAffectGrid(rule.style.getPropertyValue('font-family'));
      }
      if (typeof CSSKeyframeRule !== 'undefined' && rule instanceof CSSKeyframeRule) {
        const keyframes = rule.parentRule;
        if (typeof CSSKeyframesRule === 'undefined' || !(keyframes instanceof CSSKeyframesRule)) {
          return false;
        }
        return getComputedStyle(grid).animationName.split(',')
          .some((name) => name.trim() === keyframes.name);
      }
      if (typeof CSSImportRule !== 'undefined' && rule instanceof CSSImportRule) {
        if (rule.media.mediaText && !matchMedia(rule.media.mediaText).matches) return false;
        return stylesheetCanAffectGrid(rule.styleSheet);
      }
      const nested = (rule as CSSRule & { cssRules?: CSSRuleList }).cssRules;
      return nested ? [...nested].some(ruleCanAffectGrid) : false;
    };
    const stylesheetCanAffectGrid = (sheet: CSSStyleSheet | null): boolean => {
      if (!sheet || !stylesheetIsActive(sheet)) return false;
      try {
        return [...sheet.cssRules].some(ruleCanAffectGrid);
      } catch {
        // Cross-origin or otherwise opaque sheets stay diagnostic-only.
        return false;
      }
    };
    const stylesheetElementCanAffectGrid = (element: HTMLStyleElement | HTMLLinkElement) => {
      const causal = stylesheetCanAffectGrid(element.sheet as CSSStyleSheet | null);
      stylesheetElementCausality.set(element, causal);
      return causal;
    };
    const notifyDiagnosticObservation = (couldAffectGrid: boolean) => {
      if (!couldAffectGrid) return;
      attachGrid(undefined, false);
      // Asynchronous lifecycle/font notifications cannot provide a synchronous
      // before endpoint. Ownership may explain why we looked, but it cannot
      // license duration; only measureSynchronousWrite can do that for hooks.
      sample(false);
    };
    const invariantTruthChanged = (
      before: CatalogLayoutSample | null,
      after: CatalogLayoutSample | null,
      invariant: CatalogLayoutInvariant,
    ) => {
      if (!before || !after) return false;
      return invariantIsHealthy(before, invariant) !== invariantIsHealthy(after, invariant);
    };
    let synchronousWriteDepth = 0;
    const measureSynchronousWrite = <Result>(write: () => Result): Result => {
      // Until a grid exists there is no invariant state to read. Nested hooks
      // describe the same browser write, so the outermost hook owns the one
      // before/after pair instead of multiplying forced-style reads.
      if (!grid) attachGrid();
      if (!grid || synchronousWriteDepth > 0) return write();

      const before = readLayoutSample();
      synchronousWriteDepth += 1;
      let result: Result;
      try {
        result = write();
      } finally {
        synchronousWriteDepth -= 1;
      }
      const after = readLayoutSample();
      const changed = new Set<CatalogLayoutInvariant>();
      for (const invariant of [
        'resolved-track-count',
        'accepted-column-count',
      ] as const) {
        if (invariantTruthChanged(before, after, invariant)) changed.add(invariant);
      }
      // Ownership and selector matching are only useful prefilters. A measured
      // transition exists only when this exact call flips the same healthy/bad
      // predicate recordSample uses; truth-preserving writes stay diagnostic.
      recordSample(after, changed);
      return result;
    };

    // Existing rule declarations do not notify MutationObserver and do not call
    // CSSStyleSheet's whole-sheet methods. Hook their shared write surface so
    // `rule.style.setProperty(...)`, inline declarations, and cssText changes
    // receive exact post-write timestamps.
    const originalStyleSetProperty = CSSStyleDeclaration.prototype.setProperty;
    CSSStyleDeclaration.prototype.setProperty = function setProperty(
      property,
      value,
      priority,
    ) {
      measureSynchronousWrite(() => {
        originalStyleSetProperty.call(this, property, value, priority);
      });
    };
    const originalStyleRemoveProperty = CSSStyleDeclaration.prototype.removeProperty;
    CSSStyleDeclaration.prototype.removeProperty = function removeProperty(property) {
      return measureSynchronousWrite(() => originalStyleRemoveProperty.call(this, property));
    };
    const cssTextDescriptor = Object.getOwnPropertyDescriptor(
      CSSStyleDeclaration.prototype,
      'cssText',
    );
    if (cssTextDescriptor?.set && cssTextDescriptor.configurable) {
      Object.defineProperty(CSSStyleDeclaration.prototype, 'cssText', {
        ...cssTextDescriptor,
        set(value) {
          measureSynchronousWrite(() => {
            cssTextDescriptor.set?.call(this, value);
          });
        },
      });
    }

    // CSS declarations use Web IDL named-property setters for camelCase and
    // dashed assignments; Chromium and WebKit expose no configurable property
    // descriptors for those names. A stable proxy on rule.style is therefore
    // the reachable interception point for direct property assignment.
    const ruleStyleProxies = new WeakMap<CSSStyleDeclaration, CSSStyleDeclaration>();
    const proxyRuleStyle = (target: CSSStyleDeclaration) => {
      const existing = ruleStyleProxies.get(target);
      if (existing) return existing;
      const proxy = new Proxy(target, {
        get(style, property) {
          const value = Reflect.get(style, property, style);
          return typeof value === 'function' ? value.bind(style) : value;
        },
        set(style, property, value) {
          return measureSynchronousWrite(() => Reflect.set(style, property, value, style));
        },
      }) as CSSStyleDeclaration;
      ruleStyleProxies.set(target, proxy);
      return proxy;
    };
    const hookRuleStyleGetter = (prototype: object) => {
      const descriptor = Object.getOwnPropertyDescriptor(prototype, 'style');
      if (!descriptor?.get || !descriptor.configurable) return;
      Object.defineProperty(prototype, 'style', {
        ...descriptor,
        get() {
          return proxyRuleStyle(descriptor.get?.call(this) as CSSStyleDeclaration);
        },
      });
    };
    if (typeof CSSStyleRule !== 'undefined') hookRuleStyleGetter(CSSStyleRule.prototype);
    if (typeof CSSFontFaceRule !== 'undefined') hookRuleStyleGetter(CSSFontFaceRule.prototype);
    if (typeof CSSKeyframeRule !== 'undefined') hookRuleStyleGetter(CSSKeyframeRule.prototype);

    // Rule insertion/removal/replacement on either constructed or DOM-backed
    // stylesheets. These wrappers live only in Playwright's isolated test page.
    const originalInsertRule = CSSStyleSheet.prototype.insertRule;
    CSSStyleSheet.prototype.insertRule = function insertRule(rule, index) {
      return measureSynchronousWrite(() => originalInsertRule.call(this, rule, index));
    };
    const originalDeleteRule = CSSStyleSheet.prototype.deleteRule;
    CSSStyleSheet.prototype.deleteRule = function deleteRule(index) {
      measureSynchronousWrite(() => originalDeleteRule.call(this, index));
    };
    const originalReplaceSync = CSSStyleSheet.prototype.replaceSync;
    if (originalReplaceSync) {
      CSSStyleSheet.prototype.replaceSync = function replaceSync(text) {
        measureSynchronousWrite(() => originalReplaceSync.call(this, text));
      };
    }
    const originalReplace = CSSStyleSheet.prototype.replace;
    if (originalReplace) {
      CSSStyleSheet.prototype.replace = function replace(text) {
        const replacement = measureSynchronousWrite(() => originalReplace.call(this, text));
        return replacement.then((sheet) => {
          // If replacement becomes visible only when the promise resolves,
          // there is no synchronous pre-change endpoint at resolution. Keep
          // that completion diagnostic rather than inferring a duration.
          notifyDiagnosticObservation(stylesheetCanAffectGrid(sheet));
          return sheet;
        });
      };
    }

    const hookMeasuredSetter = (
      prototype: object,
      property: string,
    ) => {
      const descriptor = Object.getOwnPropertyDescriptor(prototype, property);
      if (!descriptor?.set || !descriptor.configurable) return;
      Object.defineProperty(prototype, property, {
        ...descriptor,
        set(this: object, value: unknown) {
          measureSynchronousWrite(() => {
            descriptor.set?.call(this, value);
          });
        },
      });
    };
    hookMeasuredSetter(CSSStyleSheet.prototype, 'disabled');
    hookMeasuredSetter(HTMLStyleElement.prototype, 'media');
    hookMeasuredSetter(HTMLLinkElement.prototype, 'disabled');
    hookMeasuredSetter(HTMLLinkElement.prototype, 'media');
    hookMeasuredSetter(HTMLLinkElement.prototype, 'href');
    hookMeasuredSetter(HTMLLinkElement.prototype, 'rel');

    const adoptedDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'adoptedStyleSheets');
    if (adoptedDescriptor?.set && adoptedDescriptor.configurable) {
      Object.defineProperty(Document.prototype, 'adoptedStyleSheets', {
        ...adoptedDescriptor,
        set(value) {
          measureSynchronousWrite(() => {
            adoptedDescriptor.set?.call(this, value);
          });
        },
      });
    }
    if (typeof CSSStyleRule !== 'undefined') {
      hookMeasuredSetter(CSSStyleRule.prototype, 'selectorText');
    }
    if (typeof CSSKeyframeRule !== 'undefined') {
      hookMeasuredSetter(CSSKeyframeRule.prototype, 'keyText');
    }

    // Attribute changes made by React and ordinary DOM code.
    const isStylesheetAttribute = (name: string) =>
      name === 'media' || name === 'disabled' || name === 'href' || name === 'rel';
    const originalSetAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function setAttribute(name, value) {
      measureSynchronousWrite(() => originalSetAttribute.call(this, name, value));
      if (name === 'data-testid' && isCatalogGrid(this)) attachGrid(this, false);
      if (isStylesheetElement(this) && isStylesheetAttribute(name)) {
        stylesheetElementCanAffectGrid(this);
      }
    };
    const originalRemoveAttribute = Element.prototype.removeAttribute;
    Element.prototype.removeAttribute = function removeAttribute(name) {
      measureSynchronousWrite(() => originalRemoveAttribute.call(this, name));
      if (isStylesheetElement(this) && isStylesheetAttribute(name)) {
        stylesheetElementCanAffectGrid(this);
      }
    };
    const originalToggleAttribute = Element.prototype.toggleAttribute;
    Element.prototype.toggleAttribute = function toggleAttribute(name, force) {
      const changed = measureSynchronousWrite(
        () => originalToggleAttribute.call(this, name, force),
      );
      if (isStylesheetElement(this) && isStylesheetAttribute(name)) {
        stylesheetElementCanAffectGrid(this);
      }
      return changed;
    };

    // Direct `grid.style.foo = ...` and `grid.style.setProperty(...)` calls do
    // not invoke the JavaScript setAttribute wrapper, so expose a stable proxy
    // for this one diagnostic node only.
    const styleDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'style');
    const styleProxies = new WeakMap<HTMLElement, CSSStyleDeclaration>();
    if (styleDescriptor?.get && styleDescriptor.configurable) {
      Object.defineProperty(HTMLElement.prototype, 'style', {
        ...styleDescriptor,
        get() {
          const element = this as HTMLElement;
          const target = styleDescriptor.get?.call(element) as CSSStyleDeclaration;
          const existing = styleProxies.get(element);
          if (existing) return existing;
          const proxy = new Proxy(target, {
            get(style, property) {
              const value = Reflect.get(style, property, style);
              if (typeof value !== 'function') return value;
              return (...args: unknown[]) => Reflect.apply(value, style, args);
            },
            set(style, property, value) {
              return measureSynchronousWrite(() => Reflect.set(style, property, value, style));
            },
          }) as CSSStyleDeclaration;
          styleProxies.set(element, proxy);
          return proxy;
        },
      });
    }

    // CSS Typed OM writes bypass setAttribute and CSSStyleDeclaration. They use
    // the same before/after invariant comparison, so ownership alone can never
    // promote an unrelated map write to measured evidence.
    if (typeof StylePropertyMap !== 'undefined') {
      const originalStyleMapSet = StylePropertyMap.prototype.set;
      StylePropertyMap.prototype.set = function set(property, ...values) {
        measureSynchronousWrite(() => originalStyleMapSet.call(this, property, ...values));
      };
      const originalStyleMapDelete = StylePropertyMap.prototype.delete;
      StylePropertyMap.prototype.delete = function deleteProperty(property) {
        measureSynchronousWrite(() => originalStyleMapDelete.call(this, property));
      };
      const originalStyleMapClear = StylePropertyMap.prototype.clear;
      StylePropertyMap.prototype.clear = function clear() {
        measureSynchronousWrite(() => originalStyleMapClear.call(this));
      };
    }

    // DOMStringMap named setters are browser internals rather than calls to the
    // JavaScript setAttribute method; proxy the grid's dataset for exact timing.
    const datasetDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'dataset');
    const datasetProxies = new WeakMap<HTMLElement, DOMStringMap>();
    if (datasetDescriptor?.get && datasetDescriptor.configurable) {
      Object.defineProperty(HTMLElement.prototype, 'dataset', {
        ...datasetDescriptor,
        get() {
          const element = this as HTMLElement;
          const target = datasetDescriptor.get?.call(element) as DOMStringMap;
          const existing = datasetProxies.get(element);
          if (existing) return existing;
          const proxy = new Proxy(target, {
            set(dataset, property, value) {
              return measureSynchronousWrite(
                () => Reflect.set(dataset, property, value, dataset),
              );
            },
            deleteProperty(dataset, property) {
              return measureSynchronousWrite(() => Reflect.deleteProperty(dataset, property));
            },
          }) as DOMStringMap;
          datasetProxies.set(element, proxy);
          return proxy;
        },
      });
    }

    // Class changes can affect computed tracks without touching inline style.
    const classNameDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'className');
    if (classNameDescriptor?.get && classNameDescriptor.set && classNameDescriptor.configurable) {
      Object.defineProperty(Element.prototype, 'className', {
        ...classNameDescriptor,
        set(value) {
          measureSynchronousWrite(() => {
            classNameDescriptor.set?.call(this, value);
          });
        },
      });
    }
    const classListDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'classList');
    const classListProxies = new WeakMap<Element, DOMTokenList>();
    if (classListDescriptor?.get && classListDescriptor.configurable) {
      Object.defineProperty(Element.prototype, 'classList', {
        ...classListDescriptor,
        get() {
          const element = this as Element;
          const target = classListDescriptor.get?.call(element) as DOMTokenList;
          const existing = classListProxies.get(element);
          if (existing) return existing;
          const proxy = new Proxy(target, {
            get(tokens, property) {
              const value = Reflect.get(tokens, property, tokens);
              if (typeof value !== 'function') return value;
              return (...args: unknown[]) => {
                if (property === 'add'
                  || property === 'remove'
                  || property === 'toggle'
                  || property === 'replace') {
                  return measureSynchronousWrite(() => Reflect.apply(value, tokens, args));
                }
                return Reflect.apply(value, tokens, args);
              };
            },
          }) as DOMTokenList;
          classListProxies.set(element, proxy);
          return proxy;
        },
      });
    }

    const documentObserver = new MutationObserver((records) => {
      let addedGrid: HTMLElement | undefined;
      for (const record of records) {
        if (record.type === 'attributes'
          && record.target instanceof Element
          && isCatalogGrid(record.target)) {
          addedGrid = record.target;
          break;
        }
        for (const node of record.addedNodes) {
          if (isCatalogGrid(node as Element)) {
            addedGrid = node as HTMLElement;
            break;
          }
          if (node instanceof Element) {
            const nestedGrid = node.querySelector<HTMLElement>('[data-testid="catalog-grid"]');
            if (nestedGrid) {
              addedGrid = nestedGrid;
              break;
            }
          }
        }
        if (addedGrid) break;
      }
      // A newly inserted grid is the beginning of its observable layout
      // lifetime, so an applicable bad state at that boundary has an exact
      // start. If a synchronous hook already attached it, attachGrid is a no-op
      // and cannot retroactively promote a diagnostic episode.
      attachGrid(addedGrid, Boolean(addedGrid));
      if (addedGrid) {
        for (const element of document.querySelectorAll<HTMLStyleElement | HTMLLinkElement>(
          'style, link',
        )) stylesheetElementCanAffectGrid(element);
      }
      let stylesheetTouched = false;
      let stylesheetCausal = false;
      for (const record of records) {
        if (record.type === 'attributes') {
          if (isStylesheetElement(record.target) && record.attributeName !== 'data-testid') {
            stylesheetTouched = true;
            stylesheetCausal ||= stylesheetElementCanAffectGrid(record.target);
          }
          continue;
        }
        for (const node of record.addedNodes) {
          for (const element of stylesheetElementsWithin(node)) {
            stylesheetTouched = true;
            stylesheetCausal ||= stylesheetElementCanAffectGrid(element);
          }
        }
        for (const node of record.removedNodes) {
          for (const element of stylesheetElementsWithin(node)) {
            stylesheetTouched = true;
            stylesheetCausal ||= stylesheetElementCausality.get(element) ?? false;
          }
        }
      }
      if (stylesheetTouched) notifyDiagnosticObservation(stylesheetCausal);
    });
    documentObserver.observe(document, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-testid', 'media', 'disabled', 'href', 'rel'],
    });
    document.addEventListener('load', (event) => {
      const target = event.target;
      if (target instanceof Node && isStylesheetElement(target)) {
        notifyDiagnosticObservation(stylesheetElementCanAffectGrid(target));
      }
    }, true);
    attachGrid();

    // FontFaceSet events run after the corresponding font-load state change.
    // They give font-metric recalculation an exact notification surface without
    // assigning duration to the time between unrelated animation frames.
    const fontEventCanAffectGrid = (event: Event) => {
      const faces = (event as FontFaceSetLoadEvent).fontfaces;
      return faces?.some((face) => fontFamilyCanAffectGrid(face.family)) ?? false;
    };
    let relevantFontLoadPending = false;
    document.fonts.addEventListener('loading', (event) => {
      const causal = fontEventCanAffectGrid(event);
      relevantFontLoadPending ||= causal;
      notifyDiagnosticObservation(causal);
    });
    const finishFontLoad = (event: Event) => {
      const causal = relevantFontLoadPending || fontEventCanAffectGrid(event);
      relevantFontLoadPending = false;
      notifyDiagnosticObservation(causal);
    };
    document.fonts.addEventListener('loadingdone', finishFontLoad);
    document.fonts.addEventListener('loadingerror', finishFontLoad);
    void document.fonts.ready.then(() => {
      const causal = relevantFontLoadPending;
      relevantFontLoadPending = false;
      notifyDiagnosticObservation(causal);
    });

    const snapshot = (): CatalogLayoutWatchdogSnapshot => {
      // A settle read is a safety observation. It can close an active record,
      // but cannot manufacture a duration for a transition that observers did
      // not timestamp.
      attachGrid();
      sample(false);
      const copied = violations.map((violation) => ({ ...violation }));
      return {
        startedAtEpochMs,
        sampledFrames,
        latest: latest ? { ...latest } : null,
        violations: copied,
        unhealed: copied.filter((violation) => violation.healedAtMs === null
          || violation.cumulativeObservedBadMs > healMs),
      };
    };

    watchdogWindow.__catalogLayoutWatchdog = { snapshot };
    const tick = () => {
      attachGrid();
      sample(false);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, { healMs: CATALOG_LAYOUT_HEAL_MS });
}

export async function readCatalogLayoutWatchdog(
  page: Page,
): Promise<CatalogLayoutWatchdogSnapshot> {
  return page.evaluate(() => {
    const api = (window as WatchdogWindow).__catalogLayoutWatchdog;
    if (!api) throw new Error('catalog layout watchdog was not installed before navigation');
    return api.snapshot();
  });
}
export type HostileLoadProfile = 'css-slow' | 'script-slow';

const HOSTILE_DELAYS_MS: Record<HostileLoadProfile, Record<string, number>> = {
  'css-slow': { stylesheet: 1_100, script: 250 },
  'script-slow': { stylesheet: 250, script: 650 },
};

export interface HostileLoadEvent {
  resourceType: string;
  delayMs: number;
  url: string;
}

export function hostileLoadProfile(testInfo: TestInfo): HostileLoadProfile | null {
  const profile = testInfo.project.metadata.hostileLoadProfile;
  return profile === 'css-slow' || profile === 'script-slow' ? profile : null;
}

/** Delay fulfilled static responses, which works through Playwright's routing
 * layer in Chromium and WebKit (unlike CDP network throttling). */
export async function installHostileLoad(
  page: Page,
  profile: HostileLoadProfile,
  events: HostileLoadEvent[],
): Promise<void> {
  const delays = HOSTILE_DELAYS_MS[profile];
  await page.route('**/*', async (route) => {
    const request = route.request();
    const resourceType = request.resourceType();
    const delayMs = delays[resourceType];
    if (request.method() !== 'GET' || delayMs === undefined) {
      await route.continue();
      return;
    }

    const response = await route.fetch();
    events.push({ resourceType, delayMs, url: request.url() });
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    await route.fulfill({ response });
  });
}
