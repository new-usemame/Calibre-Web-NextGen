import { useT } from '../lib/i18n';
import type { Me } from '../lib/api';
import { shelfMarkAudience, shelfMarksReachDevices } from '../lib/ereaderWording';
import styles from '../pages/Shelves.module.css';

/** The two shelf types share visibility/device choices; device marks stay
 * owner-only and OPDS exposure belongs to the viewer's own account. */
export function ShelfOptions({ me, smart = false, owner = true, showSharing = true, canShare, isPublic,
  koboSync, opdsExpose, onPublic, onKobo, onOpds }: {
  me?: Me | null; smart?: boolean; owner?: boolean; showSharing?: boolean; canShare: boolean;
  isPublic: boolean; koboSync: boolean; opdsExpose: boolean;
  onPublic: (value: boolean) => void; onKobo: (value: boolean) => void; onOpds: (value: boolean) => void;
}) {
  const t = useT();
  const syncAvailable = shelfMarksReachDevices(me?.features)
    && (!smart || !!me?.features?.kobo_sync_magic_shelves);
  return <fieldset className={styles.options}>
    <legend>{t('Shelf options')}</legend>
    {showSharing && <label className={styles.option}>
      <input type="checkbox" checked={isPublic} disabled={!canShare && !(owner && isPublic)}
        onChange={(e) => onPublic(e.target.checked)} />
      <span>{t('Share with everyone')}</span>
    </label>}
    {showSharing && owner && !canShare && !isPublic && <p className={styles.hint}>{t('Your administrator has disabled sharing your own shelves.')}</p>}
    {owner && <>
      <label className={styles.option}>
        <input type="checkbox" checked={koboSync} disabled={!syncAvailable}
          onChange={(e) => onKobo(e.target.checked)} />
        <span>{shelfMarkAudience(me?.features) === 'ereader' ? t('Sync to e-readers') : t('Sync to Kobo')}</span>
      </label>
      {!syncAvailable && <p className={styles.hint}>{t('An administrator must enable shelf sync before this option is available.')}</p>}
      {syncAvailable && me?.kobo_only_shelves_sync === false && <p className={styles.hint}>{t('Your account currently syncs the whole library. Select shelf-only sync in your account settings to use shelf marks.')}</p>}
    </>}
    <label className={styles.option}>
      <input type="checkbox" checked={me?.opds_only_shelves_sync ? opdsExpose : true}
        disabled={!me?.opds_only_shelves_sync} onChange={(e) => onOpds(e.target.checked)} />
      <span>{t('Show in my OPDS feed')}</span>
    </label>
    {!me?.opds_only_shelves_sync && <p className={styles.hint}>{t('Your OPDS feed includes all visible shelves. Enable selected shelves only in your account settings to choose individual shelves.')}</p>}
  </fieldset>;
}
