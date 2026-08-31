import { parseApiTimestamp } from './relativeTime.ts';

export interface PairingDevice {
  label: string;
  type: string;
  last_seen: string | null;
}

export interface PairingVisibility {
  showKoreader: boolean;
  showStockSetup: boolean;
  canGenerateStockToken: boolean;
}

/** Current pairing-surface visibility, kept explicit so every feature state is testable. */
export function pairingVisibility(koboEnabled: boolean, tokenConfigured: boolean): PairingVisibility {
  return {
    showKoreader: true,
    showStockSetup: tokenConfigured,
    canGenerateStockToken: koboEnabled && !tokenConfigured,
  };
}

/** Newest Kobo/KOReader check-in that can confirm the pairing reached CWNG. */
export function latestPairingDevice<T extends PairingDevice>(devices: T[]): T | null {
  const eligible = devices.flatMap((device) => {
    if (device.type !== 'kobo' && device.type !== 'koreader') return [];
    const timestamp = device.last_seen ? parseApiTimestamp(device.last_seen) : null;
    return timestamp === null ? [] : [{ device, timestamp }];
  });
  return eligible.sort((left, right) => right.timestamp - left.timestamp)[0]?.device ?? null;
}

export function koboConfigLine(syncUrl: string): string {
  return `api_endpoint=${syncUrl}`;
}
