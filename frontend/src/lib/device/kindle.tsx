/* USB-connected Kindles, shared across the app.
 *
 * One KindleConnection per plugged-in Kindle is held for the whole session so the sidebar, the device
 * page and every book page can show what is on each without re-negotiating USB. A Kindle the browser
 * has been granted before is picked up on load with no prompt; the first time (and after "Eject") the
 * user clicks to connect, because the browser only shows its device chooser for a user gesture.
 * Each Kindle gets an opaque route key scoped to the tab.
 */
import {
  useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { ApiError, apiGet, apiPost, resourceUrl, type Me } from '../api';
import { canUseKindleUsb } from '../permissions';
import {
  AMAZON_VENDOR_ID, KindleConnection, createKindleDeviceKeys, grantedKindles, isWebUsbSupported, pickKindle,
  type SendPhase, type UsbDeviceLike,
} from './kindleUsb';
import type {
  ConnectedDevice, DeviceBook, DeviceEntry, DeviceSendResult, DeviceSource, DeviceStatus,
} from './types';

const EJECTED_KEY = 'cwng:device-ejected';
const LABEL = 'Kindle';

/** Message worth showing for a USB failure, or null for the user closing the device chooser. */
export function describeKindleError(err: unknown): string | null {
  if (err instanceof DOMException && err.name === 'NotFoundError') return null;
  const message = err instanceof Error ? err.message : String(err);
  if (/claim|access denied|busy|in use/i.test(message)) {
    return 'Another program (Calibre, Android File Transfer, OpenMTP) may be using the Kindle. '
      + 'Close it, then unplug and replug the Kindle.';
  }
  return message;
}

function readEjected(): Set<string> {
  try {
    const raw = JSON.parse(sessionStorage.getItem(EJECTED_KEY) ?? '[]');
    return new Set(Array.isArray(raw) ? raw.filter((v): v is string => typeof v === 'string') : []);
  } catch { return new Set(); }
}
function writeEjected(ids: Set<string>) {
  try {
    if (ids.size) sessionStorage.setItem(EJECTED_KEY, JSON.stringify([...ids]));
    else sessionStorage.removeItem(EJECTED_KEY);
  } catch { /* private mode: the flag just does not survive a reload */ }
}

const deviceId = createKindleDeviceKeys();

interface UsbEvents {
  addEventListener(type: 'connect' | 'disconnect', cb: (e: { device: UsbDeviceLike }) => void): void;
  removeEventListener(type: 'connect' | 'disconnect', cb: (e: { device: UsbDeviceLike }) => void): void;
}

/** What is known about one Kindle; the connection itself is kept in a ref beside it. */
interface Slot {
  id: string;
  status: DeviceStatus;
  name: string;
  entries: DeviceEntry[];
  space: ConnectedDevice['space'];
  error: string | null;
  busy: string | null;
}

/** The Kindle driver: owns a USB connection per plugged-in Kindle and what is known about its contents. */
export function useKindleDevices(ownerId: number | undefined): { devices: ConnectedDevice[]; source: DeviceSource } {
  const supported = ownerId !== undefined && isWebUsbSupported();
  const [slots, setSlots] = useState<Record<string, Slot>>({});
  const connections = useRef(new Map<string, KindleConnection>());
  const activeAccess = useRef<{ ownerId: number; controller: AbortController } | null>(null);
  const attaching = useRef(new Set<string>());
  const ejected = useRef<Set<string> | null>(null);
  if (ejected.current === null) ejected.current = readEjected();

  const patch = useCallback((id: string, change: Partial<Slot>) => {
    setSlots((cur) => {
      const base: Slot = cur[id] ?? { id, status: 'none', name: '', entries: [], space: null, error: null, busy: null };
      return { ...cur, [id]: { ...base, ...change } };
    });
  }, []);
  const forget = useCallback((id: string) => {
    setSlots((cur) => {
      if (!(id in cur)) return cur;
      const next = { ...cur };
      delete next[id];
      return next;
    });
  }, []);
  const setEjected = useCallback((id: string, on: boolean) => {
    const ids = ejected.current!;
    if (on) ids.add(id); else ids.delete(id);
    writeEjected(ids);
  }, []);

  const clearConnections = useCallback(() => {
    const open = [...connections.current.values()];
    connections.current.clear();
    setSlots({});
    for (const connection of open) void connection.close().catch(() => undefined);
  }, []);

  const verifyAccess = useCallback(async (access = activeAccess.current) => {
    if (!access || activeAccess.current !== access) throw new Error('Kindle USB access changed.');
    const me = await apiGet<Me>('/api/v1/auth/me', { signal: access.controller.signal }).catch((error) => {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return null;
      throw error;
    });
    if (activeAccess.current !== access) throw new Error('Kindle USB access changed.');
    if (me?.id !== access.ownerId || !canUseKindleUsb(me)) {
      activeAccess.current = null;
      access.controller.abort();
      clearConnections();
      throw new Error('Kindle USB access changed.');
    }
    return access;
  }, [clearConnections]);

  const load = useCallback(async (id: string, conn: KindleConnection) => {
    const files = await conn.listBooks();
    let matched: Array<{ book: DeviceBook | null }> = [];
    if (files.length > 0) {
      const res = await apiPost<{ items: Array<{ book: DeviceBook | null }> }>(
        '/api/v1/kindle-usb/resolve', { files: files.map((f) => f.name) });
      matched = res.items;
    }
    let space: ConnectedDevice['space'] = null;
    try { space = await conn.space(); } catch { /* the entries are still worth showing */ }
    if (connections.current.get(id) === conn) {
      patch(id, { entries: files.map((f, i) => ({ ...f, book: matched[i]?.book ?? null })), space });
    }
  }, [patch]);

  const drop = useCallback(async (id: string) => {
    const conn = connections.current.get(id);
    connections.current.delete(id);
    patch(id, { entries: [], space: null });
    if (conn) { try { await conn.close(); } catch { /* the device is already gone */ } }
  }, [patch]);

  /** Open a Kindle and read what is on it. Resolves with its id. */
  const attach = useCallback(async (device: UsbDeviceLike, scope = activeAccess.current): Promise<string> => {
    const access = await verifyAccess(scope);
    const id = deviceId(device);
    if (connections.current.has(id)) return id;
    if (attaching.current.has(id)) throw new Error('This Kindle is already connecting.');
    attaching.current.add(id);
    patch(id, { status: 'connecting', error: null, name: device.productName || LABEL });
    try {
      const conn = await KindleConnection.open(device);
      try {
        await verifyAccess(access);
      } catch (error) {
        await conn.close().catch(() => undefined);
        throw error;
      }
      connections.current.set(id, conn);
      setEjected(id, false);
      patch(id, { name: conn.name });
      await load(id, conn);
      if (connections.current.get(id) !== conn) throw new Error('This Kindle has disconnected.');
      patch(id, { status: 'connected' });
      return id;
    } catch (err) {
      if (activeAccess.current === access) {
        await drop(id);
        if (activeAccess.current === access) {
          const message = describeKindleError(err);
          if (message === null) forget(id); else patch(id, { status: 'error', error: message });
        }
      }
      throw err;
    } finally {
      attaching.current.delete(id);
    }
  }, [drop, forget, load, patch, setEjected, verifyAccess]);

  const connectedUsb = useCallback((d: UsbDeviceLike) => connections.current.has(deviceId(d)), []);

  const eject = useCallback(async (id: string) => {
    const connection = connections.current.get(id);
    if (!connection) throw new Error('The Kindle is not connected.');
    patch(id, { busy: 'Ejecting…' });
    try {
      await connection.close();
      if (connections.current.get(id) !== connection) return;
      connections.current.delete(id);
      setEjected(id, true);
      patch(id, { status: 'ejected', error: null, entries: [], space: null, busy: null });
    } catch (error) {
      if (connections.current.get(id) === connection) {
        if (connection.isOpen) {
          patch(id, { busy: null });
        } else {
          connections.current.delete(id);
          setEjected(id, true);
          patch(id, { status: 'error', entries: [], space: null, busy: null });
        }
      }
      throw error;
    }
  }, [patch, setEjected]);

  const refresh = useCallback(async (id: string) => {
    const conn = connections.current.get(id);
    if (!conn) return;
    patch(id, { busy: 'Refreshing…' });
    try {
      await verifyAccess();
      if (connections.current.get(id) === conn) await load(id, conn);
    } catch (err) {
      if (connections.current.get(id) === conn) patch(id, { error: describeKindleError(err) });
    } finally {
      if (connections.current.get(id) === conn) patch(id, { busy: null });
    }
  }, [load, patch, verifyAccess]);

  const send = useCallback(async (
    id: string, bookId: number, onPhase?: (phase: string) => void,
  ): Promise<DeviceSendResult> => {
    const conn = connections.current.get(id);
    if (!conn) throw new Error('The Kindle is not connected.');
    const access = await verifyAccess();
    if (connections.current.get(id) !== conn) throw new Error('This Kindle has disconnected.');
    patch(id, { busy: 'Sending…' });
    try {
      onPhase?.('preparing');
      const plan = await apiPost<{ format: string; converted: boolean; filename: string; download_url: string }>(
        `/api/v1/books/${bookId}/kindle-usb`, {});
      onPhase?.('downloading');
      const response = await fetch(resourceUrl(plan.download_url), { credentials: 'same-origin' });
      if (!response.ok || response.headers.get('Content-Type')?.startsWith('text/html')) {
        throw new Error(`Could not download the Kindle book (${response.status}).`);
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      await conn.send(plan.filename, bytes, (p: SendPhase) => onPhase?.(p), async () => {
        await verifyAccess(access);
        if (connections.current.get(id) !== conn) throw new Error('This Kindle has disconnected.');
      });
      await load(id, conn);
      return { format: plan.format, converted: plan.converted };
    } finally {
      if (connections.current.get(id) === conn) patch(id, { busy: null });
    }
  }, [load, patch, verifyAccess]);

  const read = useCallback(async (
    id: string, conn: KindleConnection | undefined, entry: DeviceEntry,
  ): Promise<Uint8Array> => {
    const access = await verifyAccess();
    if (!conn || connections.current.get(id) !== conn) throw new Error('This Kindle has disconnected.');
    const bytes = await conn.read(entry);
    await verifyAccess(access);
    if (connections.current.get(id) !== conn) throw new Error('This Kindle has disconnected.');
    return bytes;
  }, [verifyAccess]);

  const remove = useCallback(async (
    id: string, conn: KindleConnection | undefined, entry: DeviceEntry,
  ): Promise<void> => {
    const access = await verifyAccess();
    if (!conn || connections.current.get(id) !== conn) throw new Error('This Kindle has disconnected.');
    patch(id, { busy: 'Removing…' });
    try {
      await conn.delete(entry, async () => {
        await verifyAccess(access);
        if (connections.current.get(id) !== conn) throw new Error('This Kindle has disconnected.');
      });
      await load(id, conn);
    } finally {
      if (connections.current.get(id) === conn) patch(id, { busy: null });
    }
  }, [load, patch, verifyAccess]);

  /** Ask for a Kindle (chooser or the one remembered one) and connect it. */
  const pickAndAttach = useCallback(async (): Promise<string | null> => {
    const access = activeAccess.current;
    if (!supported || !access) return null;
    try {
      return await attach(await pickKindle(connectedUsb), access);
    } catch (err) {
      if (describeKindleError(err) === null) return null; // closed the chooser
      throw err;
    }
  }, [attach, connectedUsb, supported]);

  // Pick up Kindles the browser already has permission for, and follow plug/unplug.
  useEffect(() => {
    if (!supported || ownerId === undefined) return;
    const access = { ownerId, controller: new AbortController() };
    activeAccess.current = access;
    const usb = (navigator as unknown as { usb: UsbEvents }).usb;

    const adoptGranted = async () => {
      const devices = await grantedKindles().catch(() => []);
      for (const device of devices) {
        const id = deviceId(device);
        if (activeAccess.current !== access || connections.current.has(id)
            || attaching.current.has(id) || ejected.current!.has(id)) continue;
        try { await attach(device, access); } catch { /* recorded in state */ }
      }
    };
    void adoptGranted();

    const onConnect = (e: { device: UsbDeviceLike }) => {
      if (e.device.vendorId === AMAZON_VENDOR_ID) void adoptGranted();
    };
    const onDisconnect = (e: { device: UsbDeviceLike }) => {
      if (e.device.vendorId !== AMAZON_VENDOR_ID) return;
      const id = deviceId(e.device);
      setEjected(id, false); // unplugged: the next plug-in is a fresh start
      void drop(id).then(() => {
        if (activeAccess.current === access && !connections.current.has(id) && !attaching.current.has(id)) forget(id);
      });
    };
    usb.addEventListener('connect', onConnect);
    usb.addEventListener('disconnect', onDisconnect);
    return () => {
      activeAccess.current = null;
      access.controller.abort();
      usb.removeEventListener('connect', onConnect);
      usb.removeEventListener('disconnect', onDisconnect);
      clearConnections();
    };
  }, [supported, ownerId, attach, clearConnections, drop, forget, setEjected]);

  const view = useCallback((slot: Slot): ConnectedDevice => {
    const byBook = new Map<number, DeviceEntry>();
    for (const e of slot.entries) if (e.book && !byBook.has(e.book.id)) byBook.set(e.book.id, e);
    const { id } = slot;
    const connection = connections.current.get(id);
    return {
      id, kind: 'kindle', label: LABEL,
      status: slot.status, name: slot.name, entries: slot.entries, space: slot.space,
      error: slot.error, busy: slot.busy,
      connect: async () => { await pickAndAttach(); },
      eject: () => eject(id),
      refresh: () => refresh(id),
      send: async (bookId, onPhase) => {
        if (!connection || connections.current.get(id) !== connection) {
          throw new Error('This Kindle disconnected. Reconnect it before sending.');
        }
        return send(id, bookId, onPhase);
      },
      read: (entry) => read(id, connection, entry),
      remove: (entry) => remove(id, connection, entry),
      onDevice: (bookId) => byBook.get(bookId),
      describeError: describeKindleError,
    };
  }, [eject, pickAndAttach, read, refresh, remove, send]);

  const devices = useMemo(
    () => (supported ? Object.values(slots).map(view) : []),
    [supported, slots, view]);

  const source = useMemo<DeviceSource>(() => ({
    kind: 'kindle',
    label: LABEL,
    supported,
    connect: async () => {
      const id = await pickAndAttach();
      if (id === null) return null;
      // State may not have caught up with the connection made a moment ago, so build the view from
      // the connection itself.
      const conn = connections.current.get(id);
      return view({ id, status: 'connected', name: conn?.name ?? LABEL, entries: [], space: null, error: null, busy: null });
    },
    describeError: describeKindleError,
  }), [supported, pickAndAttach, view]);

  return { devices, source };
}
