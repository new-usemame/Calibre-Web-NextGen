import { useEffect, useState } from 'react';
import { Link } from 'wouter';
import { useMutation, useQuery } from '@tanstack/react-query';
import { apiGet, apiPost } from '../lib/api';
import { clampOffset } from '../lib/pagination';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useT } from '../lib/i18n';
import styles from '../pages/Devices.module.css';

export interface AuthorityRollup {
  unseeded: number;
  seeding: number;
  authoritative: number;
  quarantined: number;
  disabled: number;
  books_partially_seeded: number;
}

export interface Device {
  public_id: string;
  label: string;
  type: string;
  kind?: string;
  kind_label?: string;
  model: string | null;
  firmware: string | null;
  first_seen: string | null;
  last_seen: string | null;
  annotation_count: number;
  highlights?: number;
  notes?: number;
  dogears?: number;
  inventory_count: number;
  inventory_observed: string | null;
  storage_free: number | null;
  storage_total: number | null;
  storage_observed: string | null;
  seeded_books?: number;
  unseeded_books?: number;
  authority?: AuthorityRollup;
  active: boolean;
}

interface InventoryBook {
  inventory_item_id: number;
  book_id: number | null;
  lpath: string;
  checksum: string;
  size: number;
  mtime: number;
}

interface InventoryPayload {
  observed_at: string | null;
  books: InventoryBook[];
  limit: number;
  offset: number;
  total: number;
}

const DEVICE_INVENTORY_WINDOW = 200;

export function DeviceInventory({ device }: { device: Device }) {
  const t = useT();
  const announce = useAnnouncer();
  const [requested, setRequested] = useState<Set<number>>(() => new Set());
  const [offset, setOffset] = useState(0);
  const { data, isLoading, error } = useQuery<InventoryPayload>({
    queryKey: ['device-inventory', device.public_id, offset],
    queryFn: () => apiGet(
      `/api/annotations/devices/${device.public_id}/inventory?limit=${DEVICE_INVENTORY_WINDOW}&offset=${offset}`,
    ),
  });
  const correctedOffset = data
    ? clampOffset(offset, data.total, DEVICE_INVENTORY_WINDOW)
    : offset;
  const stalePage = correctedOffset !== offset;
  useEffect(() => {
    if (stalePage) setOffset(correctedOffset);
  }, [correctedOffset, stalePage]);
  const deletion = useMutation({
    mutationFn: (book: InventoryBook) => apiPost(
      `/api/annotations/devices/${device.public_id}/inventory/${book.inventory_item_id}/delete`,
    ),
    onSuccess: (_result, book) => {
      setRequested((current) => new Set(current).add(book.inventory_item_id));
      announce(t('Deletion requested'));
    },
    onError: () => announce(t('Could not request deletion from this device.'), { assertive: true }),
  });
  const requestDeletion = (book: InventoryBook) => {
    if (window.confirm(t('Delete {path} from {name} on its next sync?', {
      path: book.lpath, name: device.label,
    }))) deletion.mutate(book);
  };
  // Bounded independently of the server's cap, so a mixed-version deployment
  // or a future contract regression still cannot flood this list.
  const books = (data?.books ?? []).slice(0, DEVICE_INVENTORY_WINDOW);
  const status = isLoading || stalePage
    ? t('Loading device library…')
    : error
      ? t('Could not load this device library.')
      : books.length === 0
        ? t('No books were reported in the latest device inventory.')
        : t('Showing {shown} of {total} books from the latest device inventory.', {
          shown: books.length,
          total: data?.total ?? 0,
        });
  return (
    <>
      <p role={error ? 'alert' : 'status'}>{status}</p>
      {!isLoading && !stalePage && !error && books.length > 0 && (
        <ul className={styles.inventoryList} role="list">
          {books.map((book) => (
            <li key={`${book.lpath}:${book.checksum}`}>
              <div className={styles.inventoryRow}>
                <div>
                  {book.book_id
                    ? <Link href={`/book/${book.book_id}`}>{book.lpath}</Link>
                    : <span>{book.lpath}</span>}
                  <span className={styles.onDevice}>{t('On this device')}</span>
                  {!book.book_id && (
                    <span className={styles.unmatched}>{t('Not matched to this library')}</span>
                  )}
                </div>
                <button
                  type="button"
                  className={styles.inventoryDelete}
                  disabled={requested.has(book.inventory_item_id)
                    || (deletion.isPending
                      && deletion.variables?.inventory_item_id === book.inventory_item_id)}
                  onClick={() => requestDeletion(book)}
                >
                  {requested.has(book.inventory_item_id)
                    ? t('Deletion requested')
                    : t('Delete from device')}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {!isLoading && !stalePage && !error && (data?.total ?? 0) > DEVICE_INVENTORY_WINDOW && (
        <nav className={styles.pagination} aria-label={t('Device library')}>
          <button type="button" disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - DEVICE_INVENTORY_WINDOW))}>
            {t('Previous')}
          </button>
          <span>{t('Page {page} of {pages}', {
            page: Math.floor(offset / DEVICE_INVENTORY_WINDOW) + 1,
            pages: Math.ceil((data?.total ?? 0) / DEVICE_INVENTORY_WINDOW),
          })}</span>
          <button type="button"
            disabled={offset + DEVICE_INVENTORY_WINDOW >= (data?.total ?? 0)}
            onClick={() => setOffset(offset + DEVICE_INVENTORY_WINDOW)}>
            {t('Next')}
          </button>
        </nav>
      )}
    </>
  );
}
