import ePub from 'epubjs';
const probe = ePub();
const bookPrototype = Object.getPrototypeOf(probe);
const getRange = bookPrototype.getRange;
(window as any).resolvedRanges = [];
bookPrototype.getRange = async function (cfi: string) {
  try {
    const range = await getRange.call(this, cfi);
    (window as any).resolvedRanges.push({ cfi, result: range ? 'range' : String(range) });
    return range;
  } catch (error) {
    (window as any).resolvedRanges.push({ cfi, result: 'throw' });
    throw error;
  }
};
const locationsPrototype = Object.getPrototypeOf(probe.locations);
const generate = locationsPrototype.generate;
(window as any).locationGenerationMs = [];
locationsPrototype.generate = async function (...args: any[]) {
  const start = performance.now();
  try { return await generate.apply(this, args); }
  finally { (window as any).locationGenerationMs.push(performance.now() - start); }
};
probe.destroy();
const renditionPrototype = (ePub as any).Rendition.prototype;
const display = renditionPrototype.display;
(window as any).displayTargets = [];
renditionPrototype.display = function (...args: any[]) {
  (window as any).displayTargets.push(args[0]);
  (window as any).pointVisible = async (cfi: string, id: string) => {
    const range = await this.book.getRange(cfi);
    const location = this.currentLocation();
    if (!location?.start?.cfi || !location?.end?.cfi) return false;
    const compare = new (ePub as any).CFI().compare;
    return range.startContainer.parentElement.id === id
      && compare(location.start.cfi, cfi) <= 0 && compare(cfi, location.end.cfi) <= 0;
  };
  (window as any).visiblePercentageRange = () => {
    // display() can be queued before epub.js has attached its stage. Poll an
    // empty observation until a real location exists, rather than throwing
    // from the probe before the Reader has had a chance to open the book.
    try {
      const location = this.currentLocation();
      if (!location?.start?.cfi || !location?.end?.cfi) return [];
      return ['start', 'end'].map(edge => this.book.locations.percentageFromCfi(location[edge].cfi) * 100);
    } catch { return []; }
  };
  return display.apply(this, args);
};
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Reader } from '../../src/pages/Reader';
import { I18nProvider } from '../../src/lib/i18n';
import { AnnouncerProvider } from '../../src/lib/a11y/announcer';
import '../../src/styles/tokens.css';
import '../../src/styles/global.css';
ReactDOM.createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
    <I18nProvider locale="en"><AnnouncerProvider><Reader id="42" /></AnnouncerProvider></I18nProvider>
  </QueryClientProvider>,
);
