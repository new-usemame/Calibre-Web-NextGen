import { useState } from 'react';
import { apiPatch } from '../lib/api';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import styles from '../pages/Devices.module.css';

type Mode = 'unset' | 'library' | 'my_library' | 'shelves';
interface ShelfChoice { id: number; name: string }
export interface BookScope {
  mode: Mode;
  shelf_ids: number[];
  magic_shelf_ids: number[];
  account_mode: Exclude<Mode, 'unset'>;
  effective_mode: Exclude<Mode, 'unset'>;
  fallback: null | { reason: 'shelf_deleted' | 'shelf_unavailable' };
  choices: { shelves: ShelfChoice[]; magic_shelves: ShelfChoice[]; my_library: boolean };
}

export function DeviceBookScope({ id, scope, onSaved }: {
  id: string; scope: BookScope; onSaved: () => void;
}) {
  const t = useT();
  const announce = useAnnouncer();
  const [mode, setMode] = useState<Mode>(scope.mode);
  const [shelves, setShelves] = useState(scope.shelf_ids.filter(
    sid => scope.choices.shelves.some(shelf => shelf.id === sid)));
  const [magic, setMagic] = useState(scope.magic_shelf_ids.filter(
    sid => scope.choices.magic_shelves.some(shelf => shelf.id === sid)));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const prefix = `book-scope-${id}`;
  const labels: Record<Mode, string> = {
    unset: t('Same as my account setting'), library: t('Everything in my library'),
    my_library: t('My Library'), shelves: t('Only these shelves'),
  };
  const canSave = mode !== 'shelves' || shelves.length + magic.length > 0;
  const save = async () => {
    if (pending || !canSave) return;
    setPending(true); setError(false);
    try {
      await apiPatch(`/api/annotations/devices/${id}`, {
        book_scope: { mode, shelf_ids: mode === 'shelves' ? shelves : [],
          magic_shelf_ids: mode === 'shelves' ? magic : [] },
      });
      onSaved(); announce(t('Saved.'));
    } catch {
      setError(true);
    } finally { setPending(false); }
  };
  const toggle = (ids: number[], sid: number, checked: boolean) =>
    checked ? [...ids, sid] : ids.filter(value => value !== sid);
  return (
    <fieldset className={styles.bookScope} aria-describedby={`${prefix}-help`}>
      <legend>{t('Books on this device')}</legend>
      <p className={styles.bookScopeHelp}>{t('Account setting: {scope}', { scope: labels[scope.account_mode] })}</p>
      {scope.fallback && <p role="status" className={styles.bookScopeHelp}>
        {scope.fallback.reason === 'shelf_deleted'
          ? t('The shelf chosen for this device was deleted. It uses your account setting until you choose again.')
          : t('The shelf chosen for this device is no longer available. It uses your account setting until you choose again.')}
      </p>}
      {(['unset', 'library', ...(scope.choices.my_library ? ['my_library'] : []), 'shelves'] as Mode[])
        .map(value => <label key={value} className={styles.bookScopeOption}>
          <input type="radio" name={prefix} value={value} checked={mode === value} disabled={pending}
            onChange={() => { setMode(value); setError(false); }} />
          <span>{labels[value]}</span>
        </label>)}
      {mode === 'shelves' && <div className={styles.bookScopeShelves}>
        {scope.choices.shelves.map(shelf => <label key={`shelf-${shelf.id}`} className={styles.bookScopeOption}>
          <input type="checkbox" checked={shelves.includes(shelf.id)} disabled={pending}
            onChange={event => setShelves(toggle(shelves, shelf.id, event.currentTarget.checked))} />
          <span>{shelf.name}</span>
        </label>)}
        {scope.choices.magic_shelves.map(shelf => <label key={`magic-${shelf.id}`} className={styles.bookScopeOption}>
          <input type="checkbox" checked={magic.includes(shelf.id)} disabled={pending}
            onChange={event => setMagic(toggle(magic, shelf.id, event.currentTarget.checked))} />
          <span>{shelf.name}</span>
        </label>)}
      </div>}
      <p id={`${prefix}-help`} className={styles.bookScopeHelp}>
        {t('Choose which books this device keeps. Books you take off are removed from the device at its next sync; your place, highlights and notes stay saved.')}
      </p>
      <button type="button" className={styles.button} disabled={pending || !canSave}
        onClick={() => void save()}>{t('Save')}</button>
      <p role="alert" className={styles.sharePlaceError}>
        {error ? t('Could not save the book choice. Try again.') : ''}
      </p>
    </fieldset>
  );
}
