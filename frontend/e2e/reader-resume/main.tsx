import ePub from 'epubjs';
const probe = ePub();
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
renditionPrototype.display = function (...args: any[]) {
  (window as any).visiblePercentageRange = () => {
    const location = this.currentLocation();
    return ['start', 'end'].map(edge => this.book.locations.percentageFromCfi(location[edge].cfi) * 100);
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
