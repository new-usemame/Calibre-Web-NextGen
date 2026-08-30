import type { Device } from './DeviceInventory';
import { useT } from '../lib/i18n';

type Tone = 'ok' | 'info' | 'muted' | 'warn';

/* A count only earns its tone when it is nonzero — a red "0 quarantined" would
   cry wolf. Pages style [data-tone] / [data-seg] attribute selectors from their
   own CSS modules (attributes survive module hashing; classes would not). */
function tone(count: number, kind: Tone): Tone | undefined {
  return count > 0 ? kind : undefined;
}

export function DeviceSummary({ device }: { device: Device }) {
  const t = useT();
  const authority = device.authority;
  const tracked = authority
    ? authority.authoritative + authority.seeding + authority.unseeded
      + authority.quarantined + authority.disabled
    : 0;
  return (
    <>
      <dl>
        <div><dt>{t('Kind')}</dt><dd>{device.kind_label || device.kind || device.type}</dd></div>
        {device.model && <div><dt>{t('Model')}</dt><dd>{device.model}</dd></div>}
        <div data-num=""><dt>{t('Highlights')}</dt><dd>{device.highlights ?? 0}</dd></div>
        <div data-num=""><dt>{t('Notes')}</dt><dd>{device.notes ?? 0}</dd></div>
        <div data-num=""><dt>{t('Dog-ears')}</dt><dd>{device.dogears ?? 0}</dd></div>
        <div data-num="" data-tone={tone(device.seeded_books ?? 0, 'ok')}>
          <dt>{t('Seeded books')}</dt><dd>{device.seeded_books ?? 0}</dd>
        </div>
        <div data-num="" data-tone={tone(device.unseeded_books ?? 0, 'muted')}>
          <dt>{t('Unseeded books')}</dt><dd>{device.unseeded_books ?? 0}</dd>
        </div>
        {authority && (
          <>
            <div data-num="" data-tone={tone(authority.authoritative, 'ok')}>
              <dt>{t('Authoritative books')}</dt><dd>{authority.authoritative}</dd>
            </div>
            <div data-num="" data-tone={tone(authority.seeding, 'info')}>
              <dt>{t('Books seeding')}</dt><dd>{authority.seeding}</dd>
            </div>
            <div data-num="" data-tone={tone(authority.unseeded, 'muted')}>
              <dt>{t('Books awaiting authority')}</dt><dd>{authority.unseeded}</dd>
            </div>
            <div data-num="" data-tone={tone(authority.quarantined, 'warn')}>
              <dt>{t('Quarantined books')}</dt><dd>{authority.quarantined}</dd>
            </div>
            <div data-num="" data-tone={tone(authority.disabled, 'muted')}>
              <dt>{t('Disabled books')}</dt><dd>{authority.disabled}</dd>
            </div>
            <div data-num="" data-tone={tone(authority.books_partially_seeded, 'info')}>
              <dt>{t('Partially seeded books')}</dt>
              <dd>{authority.books_partially_seeded}</dd>
            </div>
          </>
        )}
      </dl>
      {authority && tracked > 0 && (
        <div data-meter="" role="presentation" aria-hidden="true">
          {authority.authoritative > 0 && (
            <span data-seg="ok" style={{ width: `${(authority.authoritative / tracked) * 100}%` }} />
          )}
          {authority.seeding > 0 && (
            <span data-seg="info" style={{ width: `${(authority.seeding / tracked) * 100}%` }} />
          )}
          {authority.unseeded > 0 && (
            <span data-seg="muted" style={{ width: `${(authority.unseeded / tracked) * 100}%` }} />
          )}
          {authority.quarantined > 0 && (
            <span data-seg="warn" style={{ width: `${(authority.quarantined / tracked) * 100}%` }} />
          )}
          {authority.disabled > 0 && (
            <span data-seg="off" style={{ width: `${(authority.disabled / tracked) * 100}%` }} />
          )}
        </div>
      )}
    </>
  );
}
