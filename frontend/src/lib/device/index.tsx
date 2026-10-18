/* Every connected device, available app-wide.
 *
 * Drivers are called in a fixed order from one component, so the hook count never changes. Each returns
 * the devices of its kind that are connected right now (any number) and a source for connecting more.
 * Add a kind by writing a driver (see ./kindle.tsx) and listing it in DRIVERS.
 */
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useKindleDevices } from './kindle';
import type { ConnectedDevice, DeviceSource } from './types';

export * from './types';

/** A driver is a hook: `enabled` is false when the account may not use devices at all. */
type DeviceDriver = (enabled: boolean) => { devices: ConnectedDevice[]; source: DeviceSource };

const DRIVERS: readonly DeviceDriver[] = [useKindleDevices];

interface DevicesValue {
  devices: readonly ConnectedDevice[];
  sources: readonly DeviceSource[];
}
const NONE: DevicesValue = { devices: [], sources: [] };
const DevicesContext = createContext<DevicesValue>(NONE);

export function DevicesProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  // DRIVERS is a module constant, so these hook calls happen in the same order on every render.
  const results = DRIVERS.map((driver) => driver(enabled));
  // Each driver memoises what it returns, so key on those and not on the fresh array.
  const value = useMemo<DevicesValue>(() => ({
    devices: results.flatMap((r) => r.devices),
    sources: results.map((r) => r.source),
  }), results.flatMap((r) => [r.devices, r.source]));
  return <DevicesContext.Provider value={value}>{children}</DevicesContext.Provider>;
}

/** The devices connected now, across every kind. */
export function useDevices(): readonly ConnectedDevice[] {
  return useContext(DevicesContext).devices;
}

/** The kinds of device this browser can connect, whether or not one is connected yet. */
export function useDeviceSources(): readonly DeviceSource[] {
  return useContext(DevicesContext).sources.filter((s) => s.supported);
}

export function useDevice(id: string): ConnectedDevice | undefined {
  return useDevices().find((d) => d.id === id);
}
