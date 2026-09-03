import { test, expect, type Page } from '@playwright/test';
import {
  CATALOG_LAYOUT_HEAL_MS,
  installCatalogLayoutWatchdog,
  readCatalogLayoutWatchdog,
  type CatalogLayoutWatchdogSnapshot,
} from './catalog-layout-watchdog';

async function waitForHealthySevenTrackGrid(page: Page): Promise<void> {
  await expect.poll(async () => page.locator('[data-testid="catalog-grid"]').evaluate((grid) => ({
    resolvedTracks: getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).filter(Boolean).length,
    acceptedColumns: grid.getAttribute('data-catalog-column-count'),
  })), {
    message: 'catalog fixture did not reach its intended final 7-track / accepted-7 state',
    timeout: 5_000,
  }).toEqual({ resolvedTracks: 7, acceptedColumns: '7' });
}

test('a scheduler gap after an immediate layout heal is not observed bad time', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const stallMs = CATALOG_LAYOUT_HEAL_MS + 100;
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: 1168px;
    }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="1"><div>x</div></div>
  <script>
    requestAnimationFrame(() => {
      const grid = document.querySelector('#grid');
      grid.style.gridTemplateColumns = 'repeat(7, minmax(0, 1fr))';
      grid.dataset.catalogColumnCount = '7';
      window.__catalogHealthyAt = performance.now();
      const until = performance.now() + ${stallMs};
      while (performance.now() < until) {}
    });
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await waitForHealthySevenTrackGrid(page);

  const snapshot = await readCatalogLayoutWatchdog(page);
  expect(snapshot.latest?.resolvedTracks).toBe(7);
  expect(snapshot.latest?.acceptedColumns).toBe(7);
  expect(snapshot.violations).toHaveLength(2);
  expect(snapshot.violations.map(({ invariant }) => invariant).sort()).toEqual([
    'accepted-column-count',
    'resolved-track-count',
  ]);
  for (const violation of snapshot.violations) {
    expect(violation.healedAtMs).not.toBeNull();
    expect(violation.lastSeenMs).toBeGreaterThanOrEqual(violation.firstSeenMs);
    expect(violation.lastSeenMs, 'lastSeenMs never advances beyond the observed heal')
      .toBeLessThanOrEqual(violation.healedAtMs ?? 0);
    expect(violation.durationEvidence).toBe('measured-transition');
    expect(violation.observedBadMs, 'the measured heal precedes the unsampled scheduler gap')
      .toBeLessThan(250);
    expect(violation.cumulativeObservedBadMs).toBe(violation.observedBadMs);
  }
  expect(snapshot.unhealed, JSON.stringify(snapshot, null, 2)).toEqual([]);
});

test('repeated bad frames across slow samples exhaust the healing budget', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: 1168px;
    }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="1"></div>
  <script>
    window.__catalogSustainedBadAt = performance.now();
    setTimeout(() => {
      const row = document.createElement('div');
      row.dataset.virtualGridRow = 'true';
      row.textContent = 'row';
      document.querySelector('#grid').append(row);
    }, 600);

    let badFramesRemaining = 8;
    const slowBadFrames = () => requestAnimationFrame(() => {
      const until = performance.now() + 300;
      while (performance.now() < until) {}
      badFramesRemaining -= 1;
      if (badFramesRemaining > 0) {
        slowBadFrames();
      } else {
        const grid = document.querySelector('#grid');
        grid.style.gridTemplateColumns = 'repeat(7, minmax(0, 1fr))';
        grid.dataset.catalogColumnCount = '7';
        window.__catalogSustainedHealthyAt = performance.now();
      }
    });
    slowBadFrames();
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.locator('[data-virtual-grid-row]').waitFor({ state: 'visible' });
  await page.waitForFunction(() => (window as Window & {
    __catalogSustainedHealthyAt?: number;
  }).__catalogSustainedHealthyAt !== undefined);
  await waitForHealthySevenTrackGrid(page);

  const snapshot = await readCatalogLayoutWatchdog(page);
  const actualBadMs = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogSustainedBadAt?: number;
      __catalogSustainedHealthyAt?: number;
    };
    return (probeWindow.__catalogSustainedHealthyAt ?? 0)
      - (probeWindow.__catalogSustainedBadAt ?? 0);
  });
  expect(actualBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
  expect(snapshot.latest?.resolvedTracks).toBe(7);
  expect(snapshot.latest?.acceptedColumns).toBe(7);
  expect(snapshot.violations).toHaveLength(2);
  for (const violation of snapshot.violations) {
    expect(violation.durationEvidence).toBe('measured-transition');
    expect(violation.observedBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
    expect(Math.abs(violation.observedBadMs - actualBadMs)).toBeLessThan(150);
    expect(violation.cumulativeObservedBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
    expect(violation.healedAtMs).not.toBeNull();
    expect(violation.lastSeenMs).toBeGreaterThanOrEqual(violation.firstSeenMs);
    expect(violation.lastSeenMs).toBeLessThanOrEqual(violation.healedAtMs ?? 0);
  }
  expect(snapshot.unhealed.map(({ invariant }) => invariant).sort()).toEqual([
    'accepted-column-count',
    'resolved-track-count',
  ]);
});

test('one healthy frame does not reset cumulative bad evidence', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: 1168px;
    }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="1"></div>
  <script>
    setTimeout(() => {
      const row = document.createElement('div');
      row.dataset.virtualGridRow = 'true';
      row.textContent = 'row';
      document.querySelector('#grid').append(row);
    }, 600);

    const grid = document.querySelector('#grid');
    let episode = 1;
    let badFrames = 0;
    let awaitingHealthySample = false;
    window.__catalogFlapTransitions = [];
    window.__catalogFlapBadSpans = [];
    let badAt = performance.now();

    const drive = () => requestAnimationFrame(() => {
      if (awaitingHealthySample) {
        episode += 1;
        grid.style.gridTemplateColumns = '1168px';
        grid.dataset.catalogColumnCount = '1';
        window.__catalogFlapTransitions.push({ type: 'bad-' + episode, at: performance.now() });
        badAt = performance.now();
        awaitingHealthySample = false;
        badFrames = 0;
        drive();
        return;
      }

      // Make the accumulated bad time deterministic without changing the
      // reviewer's 70-sample / one-healthy-sample shape.
      const until = performance.now() + 18;
      while (performance.now() < until) {}
      badFrames += 1;
      if (badFrames >= 70) {
        grid.style.gridTemplateColumns = 'repeat(7, minmax(0, 1fr))';
        grid.dataset.catalogColumnCount = '7';
        window.__catalogFlapTransitions.push({ type: 'healthy-' + episode, at: performance.now() });
        window.__catalogFlapBadSpans.push(performance.now() - badAt);
        if (episode >= 2) return;
        awaitingHealthySample = true;
      }
      drive();
    });
    drive();
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.locator('[data-virtual-grid-row]').waitFor({ state: 'visible' });
  await page.waitForFunction(() => ((window as Window & {
    __catalogFlapBadSpans?: number[];
  }).__catalogFlapBadSpans?.length ?? 0) === 2);
  await waitForHealthySevenTrackGrid(page);

  const snapshot = await readCatalogLayoutWatchdog(page);
  const timing = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogFlapTransitions?: Array<{ type: string; at: number }>;
      __catalogFlapBadSpans?: number[];
    };
    const transitions = probeWindow.__catalogFlapTransitions ?? [];
    const firstHealthy = transitions
      .find(({ type }) => type === 'healthy-1');
    const secondBad = transitions
      .find(({ type }) => type === 'bad-2');
    const badSpans = probeWindow.__catalogFlapBadSpans ?? [];
    return {
      healthySampleGapMs: (secondBad?.at ?? 0) - (firstHealthy?.at ?? 0),
      badSpans,
      totalBadMs: badSpans.reduce((total, duration) => total + duration, 0),
    };
  });

  expect(timing.healthySampleGapMs).toBeGreaterThanOrEqual(0);
  expect(timing.healthySampleGapMs).toBeLessThan(250);
  expect(timing.totalBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
  expect(snapshot.latest?.resolvedTracks).toBe(7);
  expect(snapshot.latest?.acceptedColumns).toBe(7);
  expect(snapshot.violations).toHaveLength(4);
  for (const invariant of ['resolved-track-count', 'accepted-column-count'] as const) {
    const episodes = snapshot.violations.filter((violation) => violation.invariant === invariant);
    expect(episodes).toHaveLength(2);
    expect(episodes.every((violation) => violation.durationEvidence === 'measured-transition')).toBe(true);
    expect(episodes.every((violation) => violation.observedBadMs < CATALOG_LAYOUT_HEAL_MS)).toBe(true);
    expect(Math.abs(
      episodes.reduce((total, violation) => total + violation.observedBadMs, 0)
      - timing.totalBadMs,
    )).toBeLessThan(150);
    expect(episodes[1]?.cumulativeObservedBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
  }
  expect(snapshot.unhealed.map(({ invariant }) => invariant).sort()).toEqual([
    'accepted-column-count',
    'resolved-track-count',
  ]);
});

test('two isolated bad endpoints do not license their unsampled healthy gap', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const stallMs = CATALOG_LAYOUT_HEAL_MS + 100;
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: 1168px;
    }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="1"><div>x</div></div>
  <script>
    window.__catalogBadSpans = [];
    const initialBadAt = performance.now();
    requestAnimationFrame(() => {
      const grid = document.querySelector('#grid');
      grid.style.gridTemplateColumns = 'repeat(7, minmax(0, 1fr))';
      grid.dataset.catalogColumnCount = '7';
      window.__catalogFirstHealthyAt = performance.now();
      window.__catalogBadSpans.push(window.__catalogFirstHealthyAt - initialBadAt);

      const until = performance.now() + ${stallMs};
      while (performance.now() < until) {}
      grid.style.gridTemplateColumns = '1168px';
      grid.dataset.catalogColumnCount = '1';
      window.__catalogSecondBadAt = performance.now();

      requestAnimationFrame(() => {
        grid.style.gridTemplateColumns = 'repeat(7, minmax(0, 1fr))';
        grid.dataset.catalogColumnCount = '7';
        window.__catalogBadSpans.push(performance.now() - window.__catalogSecondBadAt);
      });
    });
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.waitForFunction(() => ((window as Window & {
    __catalogBadSpans?: number[];
  }).__catalogBadSpans?.length ?? 0) === 2);
  await waitForHealthySevenTrackGrid(page);

  const snapshot = await readCatalogLayoutWatchdog(page);
  const timing = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogFirstHealthyAt?: number;
      __catalogSecondBadAt?: number;
      __catalogBadSpans?: number[];
    };
    const badSpans = probeWindow.__catalogBadSpans ?? [];
    return {
      actualHealthyGapMs: (probeWindow.__catalogSecondBadAt ?? 0)
        - (probeWindow.__catalogFirstHealthyAt ?? 0),
      totalBadMs: badSpans.reduce((total, duration) => total + duration, 0),
    };
  });
  expect(timing.actualHealthyGapMs).toBeGreaterThanOrEqual(stallMs);
  expect(snapshot.latest?.resolvedTracks).toBe(7);
  expect(snapshot.latest?.acceptedColumns).toBe(7);
  expect(snapshot.violations).toHaveLength(4);
  for (const invariant of ['resolved-track-count', 'accepted-column-count'] as const) {
    const episodes = snapshot.violations.filter((violation) => violation.invariant === invariant);
    expect(episodes).toHaveLength(2);
    expect(episodes.every((violation) => violation.durationEvidence === 'measured-transition')).toBe(true);
    expect(Math.abs(
      episodes.reduce((total, violation) => total + violation.observedBadMs, 0)
      - timing.totalBadMs,
    )).toBeLessThan(100);
    expect(episodes[1]?.cumulativeObservedBadMs).toBeLessThan(250);
    for (const violation of episodes) {
      expect(violation.healedAtMs).not.toBeNull();
      expect(violation.lastSeenMs).toBeGreaterThanOrEqual(violation.firstSeenMs);
      expect(violation.lastSeenMs).toBeLessThanOrEqual(violation.healedAtMs ?? 0);
    }
  }
  expect(snapshot.unhealed, JSON.stringify(snapshot, null, 2)).toEqual([]);
});

test('measured two-observation episodes accumulate across brief heals', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: 1168px;
    }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="1"><div>x</div></div>
  <script>
    const grid = document.querySelector('#grid');
    window.__catalogBadSpans = [];
    window.__catalogHealthySpans = [];
    let episode = 0;

    const beginEpisode = () => {
      episode += 1;
      grid.style.gridTemplateColumns = '1168px';
      grid.dataset.catalogColumnCount = '1';
      const badAt = performance.now();
      requestAnimationFrame(() => {
        const until = performance.now() + 600;
        while (performance.now() < until) {}
        requestAnimationFrame(() => {
          grid.style.gridTemplateColumns = 'repeat(7, minmax(0, 1fr))';
          grid.dataset.catalogColumnCount = '7';
          const healthyAt = performance.now();
          window.__catalogBadSpans.push(healthyAt - badAt);
          requestAnimationFrame(() => {
            if (episode >= 4) {
              window.__catalogDone = true;
              return;
            }
            const nextBadAt = performance.now();
            window.__catalogHealthySpans.push(nextBadAt - healthyAt);
            beginEpisode();
          });
        });
      });
    };
    beginEpisode();
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.waitForFunction(() => (window as Window & { __catalogDone?: boolean }).__catalogDone);
  await waitForHealthySevenTrackGrid(page);

  const snapshot = await readCatalogLayoutWatchdog(page);
  const timing = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogBadSpans?: number[];
      __catalogHealthySpans?: number[];
    };
    const badSpans = probeWindow.__catalogBadSpans ?? [];
    const healthySpans = probeWindow.__catalogHealthySpans ?? [];
    return {
      badSpans,
      healthySpans,
      totalBadMs: badSpans.reduce((total, duration) => total + duration, 0),
    };
  });

  expect(timing.badSpans).toHaveLength(4);
  expect(timing.healthySpans).toHaveLength(3);
  expect(timing.totalBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
  expect(timing.healthySpans.every((duration) => duration < 250)).toBe(true);
  expect(snapshot.latest?.resolvedTracks).toBe(7);
  expect(snapshot.latest?.acceptedColumns).toBe(7);
  expect(snapshot.violations).toHaveLength(8);
  for (const invariant of ['resolved-track-count', 'accepted-column-count'] as const) {
    const episodes = snapshot.violations.filter((violation) => violation.invariant === invariant);
    expect(episodes).toHaveLength(4);
    expect(episodes.every((violation) => violation.durationEvidence === 'measured-transition')).toBe(true);
    const measuredBadMs = episodes.reduce(
      (total, violation) => total + violation.observedBadMs,
      0,
    );
    expect(Math.abs(measuredBadMs - timing.totalBadMs)).toBeLessThan(150);
    expect(episodes[episodes.length - 1]?.cumulativeObservedBadMs)
      .toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
  }
  expect(snapshot.unhealed.map(({ invariant }) => invariant).sort()).toEqual([
    'accepted-column-count',
    'resolved-track-count',
  ]);
});

test('measured healthy stalls separate three brief bad episodes', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: 1168px;
    }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="1"><div>x</div></div>
  <script>
    const grid = document.querySelector('#grid');
    window.__catalogBadSpans = [];
    window.__catalogHealthySpans = [];
    let endpoint = 1;
    let badAt = performance.now();

    const runHealthyGap = () => {
      grid.style.gridTemplateColumns = 'repeat(7, minmax(0, 1fr))';
      grid.dataset.catalogColumnCount = '7';
      const healthyAt = performance.now();
      window.__catalogBadSpans.push(healthyAt - badAt);
      const until = performance.now() + 1100;
      while (performance.now() < until) {}
      grid.style.gridTemplateColumns = '1168px';
      grid.dataset.catalogColumnCount = '1';
      badAt = performance.now();
      window.__catalogHealthySpans.push(badAt - healthyAt);
      endpoint += 1;
      requestAnimationFrame(() => {
        if (endpoint >= 3) {
          grid.style.gridTemplateColumns = 'repeat(7, minmax(0, 1fr))';
          grid.dataset.catalogColumnCount = '7';
          window.__catalogBadSpans.push(performance.now() - badAt);
          window.__catalogDone = true;
        } else {
          runHealthyGap();
        }
      });
    };
    requestAnimationFrame(runHealthyGap);
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.waitForFunction(() => (window as Window & { __catalogDone?: boolean }).__catalogDone);
  await waitForHealthySevenTrackGrid(page);

  const snapshot = await readCatalogLayoutWatchdog(page);
  const timing = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogBadSpans?: number[];
      __catalogHealthySpans?: number[];
    };
    const badSpans = probeWindow.__catalogBadSpans ?? [];
    const healthySpans = probeWindow.__catalogHealthySpans ?? [];
    return {
      badSpans,
      healthySpans,
      totalBadMs: badSpans.reduce((total, duration) => total + duration, 0),
      totalHealthyMs: healthySpans.reduce((total, duration) => total + duration, 0),
    };
  });

  expect(timing.badSpans).toHaveLength(3);
  expect(timing.healthySpans).toHaveLength(2);
  expect(timing.totalHealthyMs).toBeGreaterThanOrEqual(2200);
  expect(timing.totalBadMs).toBeLessThan(250);
  expect(snapshot.latest?.resolvedTracks).toBe(7);
  expect(snapshot.latest?.acceptedColumns).toBe(7);
  expect(snapshot.violations).toHaveLength(6);
  for (const invariant of ['resolved-track-count', 'accepted-column-count'] as const) {
    const episodes = snapshot.violations.filter((violation) => violation.invariant === invariant);
    expect(episodes).toHaveLength(3);
    expect(episodes.every((violation) => violation.durationEvidence === 'measured-transition')).toBe(true);
    const measuredBadMs = episodes.reduce(
      (total, violation) => total + violation.observedBadMs,
      0,
    );
    expect(Math.abs(measuredBadMs - timing.totalBadMs)).toBeLessThan(100);
    expect(episodes[episodes.length - 1]?.cumulativeObservedBadMs).toBeLessThan(250);
  }
  expect(snapshot.unhealed, JSON.stringify(snapshot, null, 2)).toEqual([]);
});

test('CSSOM track changes are measured even without grid mutation or resize', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const badDurationMs = CATALOG_LAYOUT_HEAL_MS + 200;
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      height: 100px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
    }
  </style>
  <style id="override"></style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="7"><div>x</div></div>
  <script>
    const sheet = document.querySelector('#override').sheet;
    window.__catalogCssomBadAt = performance.now();
    sheet.insertRule('#grid { grid-template-columns: 1168px !important }', 0);
    setTimeout(() => {
      sheet.deleteRule(0);
      window.__catalogCssomHealthyAt = performance.now();
    }, ${badDurationMs});
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.waitForFunction(() => (window as Window & {
    __catalogCssomHealthyAt?: number;
  }).__catalogCssomHealthyAt !== undefined);
  await waitForHealthySevenTrackGrid(page);

  const snapshot = await readCatalogLayoutWatchdog(page);
  const actualBadMs = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogCssomBadAt?: number;
      __catalogCssomHealthyAt?: number;
    };
    return (probeWindow.__catalogCssomHealthyAt ?? 0)
      - (probeWindow.__catalogCssomBadAt ?? 0);
  });
  expect(actualBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
  expect(snapshot.latest?.resolvedTracks).toBe(7);
  expect(snapshot.latest?.acceptedColumns).toBe(7);
  expect(snapshot.violations).toHaveLength(1);
  const violation = snapshot.violations[0];
  expect(violation?.invariant).toBe('resolved-track-count');
  expect(violation?.durationEvidence).toBe('measured-transition');
  expect(violation?.observedBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
  expect(Math.abs((violation?.observedBadMs ?? 0) - actualBadMs)).toBeLessThan(100);
  expect(snapshot.unhealed.map(({ invariant }) => invariant)).toEqual([
    'resolved-track-count',
  ]);
});

test('rule.style brief failures do not charge blocked healthy spans', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      height: 100px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
    }
  </style>
  <style id="override">
    #grid { grid-template-columns: repeat(7, minmax(0, 1fr)) !important; }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="7"><div>x</div></div>
  <script>
    const rule = document.querySelector('#override').sheet.cssRules[0];
    const good = () => rule.style.setProperty(
      'grid-template-columns',
      'repeat(7, minmax(0, 1fr))',
      'important',
    );
    const bad = () => rule.style.setProperty(
      'grid-template-columns',
      '1168px',
      'important',
    );
    const block = (duration) => {
      const until = performance.now() + duration;
      while (performance.now() < until) {}
    };

    window.__catalogRuleBadSpans = [];
    window.__catalogRuleHealthySpans = [];
    for (let cycle = 0; cycle < 4; cycle += 1) {
      const badAt = performance.now();
      bad();
      block(5);
      good();
      const healthyAt = performance.now();
      window.__catalogRuleBadSpans.push(healthyAt - badAt);
      block(600);
      window.__catalogRuleHealthySpans.push(performance.now() - healthyAt);
    }
    window.__catalogRuleDone = true;
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.waitForFunction(() => (window as Window & { __catalogRuleDone?: boolean })
    .__catalogRuleDone === true);

  const snapshot = await readCatalogLayoutWatchdog(page);
  const timing = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogRuleBadSpans?: number[];
      __catalogRuleHealthySpans?: number[];
    };
    const badSpans = probeWindow.__catalogRuleBadSpans ?? [];
    const healthySpans = probeWindow.__catalogRuleHealthySpans ?? [];
    return {
      badSpans,
      healthySpans,
      totalBadMs: badSpans.reduce((total, duration) => total + duration, 0),
      totalHealthyMs: healthySpans.reduce((total, duration) => total + duration, 0),
    };
  });
  expect(timing.badSpans).toHaveLength(4);
  expect(timing.healthySpans).toHaveLength(4);
  expect(timing.totalBadMs).toBeLessThan(100);
  expect(timing.totalHealthyMs).toBeGreaterThanOrEqual(2_400);
  expect(snapshot.latest?.resolvedTracks).toBe(7);
  expect(snapshot.latest?.acceptedColumns).toBe(7);
  expect(snapshot.violations).toHaveLength(4);
  expect(snapshot.violations.every((violation) =>
    violation.invariant === 'resolved-track-count'
      && violation.durationEvidence === 'measured-transition')).toBe(true);
  const measuredBadMs = snapshot.violations.reduce(
    (total, violation) => total + violation.observedBadMs,
    0,
  );
  expect(Math.abs(measuredBadMs - timing.totalBadMs)).toBeLessThan(100);
  expect(snapshot.unhealed, JSON.stringify(snapshot, null, 2)).toEqual([]);
});

test('rule.style sustained failure remains measured across blocked frames', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      height: 100px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
    }
  </style>
  <style id="override">
    #grid { grid-template-columns: repeat(7, minmax(0, 1fr)) !important; }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="7"><div>x</div></div>
  <script>
    const rule = document.querySelector('#override').sheet.cssRules[0];
    const block = (duration) => {
      const until = performance.now() + duration;
      while (performance.now() < until) {}
    };
    window.__catalogRuleSustainedBadAt = performance.now();
    rule.style.setProperty('grid-template-columns', '1168px', 'important');
    window.__catalogRuleStallSpans = [];
    for (let stall = 0; stall < 3; stall += 1) {
      const stallAt = performance.now();
      block(800);
      window.__catalogRuleStallSpans.push(performance.now() - stallAt);
      rule.style.setProperty('grid-template-columns', '1168px', 'important');
    }
    rule.style.setProperty(
      'grid-template-columns',
      'repeat(7, minmax(0, 1fr))',
      'important',
    );
    window.__catalogRuleSustainedHealthyAt = performance.now();
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  const snapshot = await readCatalogLayoutWatchdog(page);
  const timing = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogRuleSustainedBadAt?: number;
      __catalogRuleSustainedHealthyAt?: number;
      __catalogRuleStallSpans?: number[];
    };
    return {
      actualBadMs: (probeWindow.__catalogRuleSustainedHealthyAt ?? 0)
        - (probeWindow.__catalogRuleSustainedBadAt ?? 0),
      stallSpans: probeWindow.__catalogRuleStallSpans ?? [],
    };
  });
  expect(timing.stallSpans).toHaveLength(3);
  expect(timing.stallSpans.every((duration) => duration >= 800)).toBe(true);
  expect(timing.actualBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
  expect(snapshot.latest?.resolvedTracks).toBe(7);
  expect(snapshot.latest?.acceptedColumns).toBe(7);
  expect(snapshot.violations).toHaveLength(1);
  const violation = snapshot.violations[0];
  expect(violation?.invariant).toBe('resolved-track-count');
  expect(violation?.durationEvidence).toBe('measured-transition');
  expect(violation?.observedBadMs).toBeGreaterThan(CATALOG_LAYOUT_HEAL_MS);
  expect(Math.abs((violation?.observedBadMs ?? 0) - timing.actualBadMs)).toBeLessThan(100);
  expect(snapshot.unhealed.map(({ invariant }) => invariant)).toEqual([
    'resolved-track-count',
  ]);
});

test('grid Typed OM writes stay measured while unrelated declarations stay diagnostic', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    #grid {
      width: 1168px;
      height: 100px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
    }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="7"><div>x</div></div>
  <script>
    const grid = document.querySelector('#grid');
    const bad = () => grid.attributeStyleMap.set('grid-template-columns', '1168px');
    const good = () => grid.attributeStyleMap.set(
      'grid-template-columns',
      'repeat(7, minmax(0, 1fr))',
    );
    const block = (duration) => {
      const until = performance.now() + duration;
      while (performance.now() < until) {}
    };
    window.__catalogTypedOmSupported = Boolean(grid.attributeStyleMap);
    window.__catalogTypedOmBadSpans = [];
    window.__catalogTypedOmHealthySpans = [];
    for (let cycle = 0; cycle < 4; cycle += 1) {
      const badAt = performance.now();
      bad();
      document.body.style.setProperty('--unrelated-catalog-probe', String(cycle));
      good();
      const healthyAt = performance.now();
      window.__catalogTypedOmBadSpans.push(healthyAt - badAt);
      block(600);
      window.__catalogTypedOmHealthySpans.push(performance.now() - healthyAt);
    }
    good();
    document.body.style.setProperty('--unrelated-catalog-probe', 'done');
    window.__catalogTypedOmDone = true;
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.waitForFunction(() => (window as Window & { __catalogTypedOmDone?: boolean })
    .__catalogTypedOmDone === true);
  await waitForHealthySevenTrackGrid(page);

  const snapshot = await readCatalogLayoutWatchdog(page);
  const timing = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogTypedOmSupported?: boolean;
      __catalogTypedOmBadSpans?: number[];
      __catalogTypedOmHealthySpans?: number[];
    };
    const badSpans = probeWindow.__catalogTypedOmBadSpans ?? [];
    const healthySpans = probeWindow.__catalogTypedOmHealthySpans ?? [];
    return {
      supported: probeWindow.__catalogTypedOmSupported,
      badSpans,
      healthySpans,
      totalBadMs: badSpans.reduce((total, duration) => total + duration, 0),
      totalHealthyMs: healthySpans.reduce((total, duration) => total + duration, 0),
    };
  });
  expect(timing.supported).toBe(true);
  expect(timing.badSpans).toHaveLength(4);
  expect(timing.healthySpans).toHaveLength(4);
  expect(timing.totalBadMs).toBeLessThan(100);
  expect(timing.totalHealthyMs).toBeGreaterThanOrEqual(2_400);
  expect(snapshot.violations).toHaveLength(4);
  expect(snapshot.violations.every((violation) =>
    violation.invariant === 'resolved-track-count'
      && violation.durationEvidence === 'measured-transition')).toBe(true);
  const measuredBadMs = snapshot.violations.reduce(
    (total, violation) => total + violation.observedBadMs,
    0,
  );
  expect(Math.abs(measuredBadMs - timing.totalBadMs)).toBeLessThan(100);
  expect(snapshot.unhealed, JSON.stringify(snapshot, null, 2)).toEqual([]);
});

test('unrelated declaration cannot upgrade an animation-only bad state', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    @keyframes catalog-unhooked-bad-tracks {
      from { grid-template-columns: 1168px; }
      to { grid-template-columns: 1168px; }
    }
    #grid {
      width: 1168px;
      height: 100px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      animation: catalog-unhooked-bad-tracks 800ms 200ms linear 1;
    }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="7"><div>x</div></div>
  <script>
    const grid = document.querySelector('#grid');
    grid.addEventListener('animationstart', () => {
      window.__catalogAnimationBadAt = performance.now();
      window.__catalogAnimationTracksAtBodyWrite = getComputedStyle(grid)
        .gridTemplateColumns.trim().split(/\\s+/).length;
      document.body.style.setProperty('--unrelated-catalog-probe', 'while-grid-bad');
      window.__catalogAnimationDuringBad = window.__catalogLayoutWatchdog.snapshot();
    });
    grid.addEventListener('animationend', (event) => {
      window.__catalogAnimationHealthyAt = performance.now();
      window.__catalogAnimationElapsedMs = event.elapsedTime * 1000;
    });
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.waitForFunction(() => (window as Window & {
    __catalogAnimationHealthyAt?: number;
  }).__catalogAnimationHealthyAt !== undefined);
  await waitForHealthySevenTrackGrid(page);

  const snapshot = await readCatalogLayoutWatchdog(page);
  const probe = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogAnimationBadAt?: number;
      __catalogAnimationHealthyAt?: number;
      __catalogAnimationElapsedMs?: number;
      __catalogAnimationTracksAtBodyWrite?: number;
      __catalogAnimationDuringBad?: CatalogLayoutWatchdogSnapshot;
    };
    return {
      actualBadMs: (probeWindow.__catalogAnimationHealthyAt ?? 0)
        - (probeWindow.__catalogAnimationBadAt ?? 0),
      animationElapsedMs: probeWindow.__catalogAnimationElapsedMs,
      tracksAtBodyWrite: probeWindow.__catalogAnimationTracksAtBodyWrite,
      duringBad: probeWindow.__catalogAnimationDuringBad,
    };
  });
  expect(probe.animationElapsedMs).toBeCloseTo(800, 0);
  expect(probe.tracksAtBodyWrite).toBe(1);
  expect(probe.duringBad?.violations).toHaveLength(1);
  expect(probe.duringBad?.violations[0]?.durationEvidence).toBe('unmeasured-safety-net');
  expect(probe.duringBad?.violations[0]?.healedAtMs).toBeNull();
  expect(snapshot.violations).toHaveLength(1);
  expect(snapshot.violations[0]?.durationEvidence).toBe('unmeasured-safety-net');
  expect(snapshot.violations[0]?.observedBadMs).toBe(0);
  expect(snapshot.violations[0]?.healedAtMs).not.toBeNull();
  expect(snapshot.unhealed, JSON.stringify(snapshot, null, 2)).toEqual([]);
});

test('irrelevant grid declaration stays diagnostic while a real grid write is measured', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    @keyframes catalog-paused-track-probe {
      from { grid-template-columns: repeat(7, minmax(0, 1fr)); }
      to { grid-template-columns: 1168px; }
    }
    #grid {
      width: 1168px;
      height: 100px;
      --catalog-grid-min: 140px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      animation: catalog-paused-track-probe 1000ms linear paused both;
    }
  </style>
  <div id="grid" data-testid="catalog-grid" data-catalog-column-count="7"><div>x</div></div>
  <script>
    const grid = document.querySelector('#grid');
    const animation = grid.getAnimations()[0];
    const tracks = () => getComputedStyle(grid).gridTemplateColumns
      .trim().split(/\\s+/).filter(Boolean).length;
    const block = (duration) => {
      const until = performance.now() + duration;
      while (performance.now() < until) {}
    };
    window.__catalogIrrelevantGridBadSpans = [];
    window.__catalogIrrelevantGridHealthySpans = [];
    for (let cycle = 0; cycle < 4; cycle += 1) {
      animation.currentTime = 550;
      const badAt = performance.now();
      const badTracks = tracks();
      grid.style.setProperty('color', cycle % 2 ? 'red' : 'blue');
      animation.currentTime = 0;
      const healthyAt = performance.now();
      const healthyTracks = tracks();
      window.__catalogIrrelevantGridBadSpans.push({
        duration: healthyAt - badAt,
        badTracks,
        healthyTracks,
      });
      block(600);
      window.__catalogIrrelevantGridHealthySpans.push(performance.now() - healthyAt);
    }
    animation.currentTime = 0;
    grid.style.setProperty('color', 'green');
    window.__catalogIrrelevantGridDone = true;
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.waitForFunction(() => (window as Window & {
    __catalogIrrelevantGridDone?: boolean;
  }).__catalogIrrelevantGridDone === true);
  await waitForHealthySevenTrackGrid(page);

  const diagnosticSnapshot = await readCatalogLayoutWatchdog(page);
  const diagnosticTiming = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogIrrelevantGridBadSpans?: Array<{
        duration: number;
        badTracks: number;
        healthyTracks: number;
      }>;
      __catalogIrrelevantGridHealthySpans?: number[];
    };
    const badSpans = probeWindow.__catalogIrrelevantGridBadSpans ?? [];
    const healthySpans = probeWindow.__catalogIrrelevantGridHealthySpans ?? [];
    return {
      badSpans,
      healthySpans,
      totalBadMs: badSpans.reduce((total, span) => total + span.duration, 0),
      totalHealthyMs: healthySpans.reduce((total, duration) => total + duration, 0),
    };
  });
  expect(diagnosticTiming.badSpans).toHaveLength(4);
  expect(diagnosticTiming.badSpans.every(({ badTracks, healthyTracks }) =>
    badTracks === 1 && healthyTracks === 7)).toBe(true);
  expect(diagnosticTiming.totalBadMs).toBeLessThan(100);
  expect(diagnosticTiming.totalHealthyMs).toBeGreaterThanOrEqual(2_400);
  expect(diagnosticSnapshot.violations).toHaveLength(1);
  expect(diagnosticSnapshot.violations[0]?.durationEvidence).toBe('unmeasured-safety-net');
  expect(diagnosticSnapshot.violations[0]?.observedBadMs).toBe(0);
  expect(diagnosticSnapshot.unhealed, JSON.stringify(diagnosticSnapshot, null, 2)).toEqual([]);

  const relevantTiming = await page.evaluate(() => {
    const grid = document.querySelector<HTMLElement>('#grid');
    if (!grid) throw new Error('catalog grid missing from relevant-write probe');
    grid.getAnimations().forEach((animation) => animation.cancel());
    const tracks = () => getComputedStyle(grid).gridTemplateColumns
      .trim().split(/\s+/).filter(Boolean).length;
    const badAt = performance.now();
    grid.style.setProperty('grid-template-columns', '1168px', 'important');
    const badTracks = tracks();
    const until = performance.now() + 650;
    while (performance.now() < until) {}
    grid.style.setProperty(
      'grid-template-columns',
      'repeat(7, minmax(0, 1fr))',
      'important',
    );
    return {
      actualBadMs: performance.now() - badAt,
      badTracks,
      healthyTracks: tracks(),
    };
  });
  const finalSnapshot = await readCatalogLayoutWatchdog(page);
  expect(relevantTiming.badTracks).toBe(1);
  expect(relevantTiming.healthyTracks).toBe(7);
  expect(relevantTiming.actualBadMs).toBeGreaterThanOrEqual(650);
  expect(finalSnapshot.violations).toHaveLength(2);
  const relevantViolation = finalSnapshot.violations[1];
  expect(relevantViolation?.invariant).toBe('resolved-track-count');
  expect(relevantViolation?.durationEvidence).toBe('measured-transition');
  expect(Math.abs((relevantViolation?.observedBadMs ?? 0) - relevantTiming.actualBadMs))
    .toBeLessThan(100);
  expect(finalSnapshot.unhealed, JSON.stringify(finalSnapshot, null, 2)).toEqual([]);
});

test('truth-preserving input changes stay diagnostic and bad values remain one episode', async ({ page }) => {
  await installCatalogLayoutWatchdog(page);
  const html = `<!doctype html><style>
    @keyframes catalog-truth-preserving-probe {
      from { grid-template-columns: repeat(7, minmax(0, 1fr)); }
      to { grid-template-columns: 1240px; }
    }
    #grid {
      height: 100px;
      column-gap: 16px;
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      animation: catalog-truth-preserving-probe 1000ms linear paused both;
    }
    #grid.a { width: 1168px; --catalog-grid-min: 140px; }
    #grid.b { width: 1240px; --catalog-grid-min: 150px; }
  </style>
  <div id="grid" class="a" data-testid="catalog-grid" data-catalog-column-count="7">
    <div>x</div>
  </div>
  <script>
    const grid = document.querySelector('#grid');
    const animation = grid.getAnimations()[0];
    const read = () => {
      const style = getComputedStyle(grid);
      const width = grid.getBoundingClientRect().width;
      const min = Number.parseFloat(style.getPropertyValue('--catalog-grid-min'));
      const gap = Number.parseFloat(style.columnGap);
      const resolvedTracks = style.gridTemplateColumns.trim().split(/\\s+/).filter(Boolean).length;
      return {
        className: grid.className,
        width,
        min,
        gap,
        expected: Math.floor((width + gap) / (min + gap)),
        resolvedTracks,
      };
    };
    const block = (duration) => {
      const until = performance.now() + duration;
      while (performance.now() < until) {}
    };
    window.__catalogTruthPreservingCycles = [];
    window.__catalogTruthPreservingHealthySpans = [];
    for (let cycle = 0; cycle < 4; cycle += 1) {
      animation.currentTime = 1000;
      const badAt = performance.now();
      const before = read();
      grid.className = grid.className === 'a' ? 'b' : 'a';
      const after = read();
      animation.currentTime = 0;
      const healthyAt = performance.now();
      window.__catalogTruthPreservingCycles.push({
        duration: healthyAt - badAt,
        before,
        after,
        healthy: read(),
      });
      block(600);
      window.__catalogTruthPreservingHealthySpans.push(performance.now() - healthyAt);
    }
    animation.currentTime = 0;
    grid.className = grid.className === 'a' ? 'b' : 'a';
    window.__catalogTruthPreservingFinal = read();
    window.__catalogTruthPreservingDone = true;
  </script>`;

  await page.goto(`data:text/html,${encodeURIComponent(html)}`);
  await page.waitForFunction(() => (window as Window & {
    __catalogTruthPreservingDone?: boolean;
  }).__catalogTruthPreservingDone === true);
  await waitForHealthySevenTrackGrid(page);

  const diagnosticSnapshot = await readCatalogLayoutWatchdog(page);
  const diagnosticTiming = await page.evaluate(() => {
    const probeWindow = window as Window & {
      __catalogTruthPreservingCycles?: Array<{
        duration: number;
        before: { expected: number; resolvedTracks: number };
        after: { expected: number; resolvedTracks: number };
        healthy: { expected: number; resolvedTracks: number };
      }>;
      __catalogTruthPreservingHealthySpans?: number[];
      __catalogTruthPreservingFinal?: { expected: number; resolvedTracks: number };
    };
    const cycles = probeWindow.__catalogTruthPreservingCycles ?? [];
    const healthySpans = probeWindow.__catalogTruthPreservingHealthySpans ?? [];
    return {
      cycles,
      healthySpans,
      final: probeWindow.__catalogTruthPreservingFinal,
      totalBadMs: cycles.reduce((total, cycle) => total + cycle.duration, 0),
      totalHealthyMs: healthySpans.reduce((total, duration) => total + duration, 0),
    };
  });
  expect(diagnosticTiming.cycles).toHaveLength(4);
  expect(diagnosticTiming.cycles.every(({ before, after, healthy }) =>
    before.expected === 7
      && before.resolvedTracks === 1
      && after.expected === 7
      && after.resolvedTracks === 1
      && healthy.expected === 7
      && healthy.resolvedTracks === 7)).toBe(true);
  expect(diagnosticTiming.totalBadMs).toBeLessThan(100);
  expect(diagnosticTiming.totalHealthyMs).toBeGreaterThanOrEqual(2_400);
  expect(diagnosticTiming.final).toMatchObject({ expected: 7, resolvedTracks: 7 });
  expect(diagnosticSnapshot.violations).toHaveLength(1);
  expect(diagnosticSnapshot.violations[0]?.durationEvidence).toBe('unmeasured-safety-net');
  expect(diagnosticSnapshot.violations[0]?.observedBadMs).toBe(0);
  expect(diagnosticSnapshot.unhealed, JSON.stringify(diagnosticSnapshot, null, 2)).toEqual([]);

  const wrongValueTiming = await page.evaluate(() => {
    const grid = document.querySelector<HTMLElement>('#grid');
    if (!grid) throw new Error('catalog grid missing from wrong-value probe');
    grid.getAnimations().forEach((animation) => animation.cancel());
    const tracks = () => getComputedStyle(grid).gridTemplateColumns
      .trim().split(/\s+/).filter(Boolean).length;
    const badAt = performance.now();
    grid.style.setProperty('grid-template-columns', '1240px', 'important');
    const firstBadTracks = tracks();
    let until = performance.now() + 400;
    while (performance.now() < until) {}
    grid.style.setProperty(
      'grid-template-columns',
      'repeat(2, minmax(0, 1fr))',
      'important',
    );
    const differentBadAt = performance.now();
    const differentBadTracks = tracks();
    until = performance.now() + 400;
    while (performance.now() < until) {}
    grid.style.setProperty(
      'grid-template-columns',
      'repeat(7, minmax(0, 1fr))',
      'important',
    );
    return {
      actualBadMs: performance.now() - badAt,
      firstBadTracks,
      differentBadTracks,
      differentBadAt,
      healthyTracks: tracks(),
    };
  });
  const finalSnapshot = await readCatalogLayoutWatchdog(page);
  expect(wrongValueTiming.firstBadTracks).toBe(1);
  expect(wrongValueTiming.differentBadTracks).toBe(2);
  expect(wrongValueTiming.healthyTracks).toBe(7);
  expect(wrongValueTiming.actualBadMs).toBeGreaterThanOrEqual(800);
  expect(finalSnapshot.violations).toHaveLength(2);
  const measuredEpisodes = finalSnapshot.violations.filter((violation) =>
    violation.durationEvidence === 'measured-transition');
  expect(measuredEpisodes).toHaveLength(1);
  const wrongValueEpisode = measuredEpisodes[0];
  expect(wrongValueEpisode?.invariant).toBe('resolved-track-count');
  expect(wrongValueEpisode?.actual).toBe(2);
  expect(wrongValueEpisode?.lastSeenMs).toBeGreaterThan(wrongValueEpisode?.firstSeenMs ?? 0);
  expect(wrongValueEpisode?.lastSeenMs).toBeLessThan(wrongValueEpisode?.healedAtMs ?? 0);
  expect(Math.abs((wrongValueEpisode?.observedBadMs ?? 0) - wrongValueTiming.actualBadMs))
    .toBeLessThan(100);
  expect(finalSnapshot.unhealed, JSON.stringify(finalSnapshot, null, 2)).toEqual([]);
});
