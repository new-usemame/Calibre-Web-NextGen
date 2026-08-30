import type { Device } from './DeviceInventory';
import { useT } from '../lib/i18n';

export function DeviceSummary({ device }: { device: Device }) {
  const t = useT();
  const authority = device.authority;
  return (
    <dl>
      <div><dt>{t('Kind')}</dt><dd>{device.kind_label || device.kind || device.type}</dd></div>
      {device.model && <div><dt>{t('Model')}</dt><dd>{device.model}</dd></div>}
      <div><dt>{t('Highlights')}</dt><dd>{device.highlights ?? 0}</dd></div>
      <div><dt>{t('Notes')}</dt><dd>{device.notes ?? 0}</dd></div>
      <div><dt>{t('Dog-ears')}</dt><dd>{device.dogears ?? 0}</dd></div>
      <div><dt>{t('Seeded books')}</dt><dd>{device.seeded_books ?? 0}</dd></div>
      <div><dt>{t('Unseeded books')}</dt><dd>{device.unseeded_books ?? 0}</dd></div>
      {authority && (
        <>
          <div><dt>{t('Authoritative books')}</dt><dd>{authority.authoritative}</dd></div>
          <div><dt>{t('Books seeding')}</dt><dd>{authority.seeding}</dd></div>
          <div><dt>{t('Books awaiting authority')}</dt><dd>{authority.unseeded}</dd></div>
          <div><dt>{t('Quarantined books')}</dt><dd>{authority.quarantined}</dd></div>
          <div><dt>{t('Disabled books')}</dt><dd>{authority.disabled}</dd></div>
          <div>
            <dt>{t('Partially seeded books')}</dt>
            <dd>{authority.books_partially_seeded}</dd>
          </div>
        </>
      )}
    </dl>
  );
}
