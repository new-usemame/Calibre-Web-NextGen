/* USB-connected Kindles, shared across the app.
 *
 * One KindleConnection per plugged-in Kindle is held for the whole session so the sidebar, the device
 * page and every book page can show what is on each without re-negotiating USB. A Kindle the browser
 * has been granted before is picked up on load with no prompt; the first time (and after "Eject") the
 * user clicks to connect, because the browser only shows its device chooser for a user gesture.
 * Each Kindle is told apart by its USB serial number.
 */
import {
  useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { apiPost, resourceUrl } from '../api';
import {
  AMAZON_VENDOR_ID, KindleConnection, grantedKindles, isWebUsbSupported, pickKindle,
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

/** A Kindle's identity: its USB serial number, which is stable across unplugging. */
function deviceId(device: UsbDeviceLike): string {
  return device.serialNumber || 'usb';
}

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
export function useKindleDevices(enabled: boolean): { devices: ConnectedDevice[]; source: DeviceSource } {
  const supported = enabled && isWebUsbSupported();
  const [slots, setSlots] = useState<Record<string, Slot>>({});
  const connections = useRef(new Map<string, KindleConnection>());
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
    patch(id, { entries: files.map((f, i) => ({ ...f, book: matched[i]?.book ?? null })), space });
  }, [patch]);

  const drop = useCallback(async (id: string) => {
    const conn = connections.current.get(id);
    connections.current.delete(id);
    if (conn) { try { await conn.close(); } catch { /* the device is already gone */ } }
    patch(id, { entries: [], space: null });
  }, [patch]);

  /** Open a Kindle and read what is on it. Resolves with its id. */
  const attach = useCallback(async (device: UsbDeviceLike): Promise<string> => {
    const id = deviceId(device);
    if (connections.current.has(id)) return id;
    if (attaching.current.has(id)) throw new Error('This Kindle is already connecting.');
    attaching.current.add(id);
    patch(id, { status: 'connecting', error: null, name: device.productName || LABEL });
    try {
      const conn = await KindleConnection.open(device);
      connections.current.set(id, conn);
      setEjected(id, false);
      patch(id, { name: conn.name });
      await load(id, conn);
      patch(id, { status: 'connected' });
      return id;
    } catch (err) {
      await drop(id);
      const message = describeKindleError(err);
      if (message === null) forget(id); else patch(id, { status: 'error', error: message });
      throw err;
    } finally {
      attaching.current.delete(id);
    }
  }, [drop, forget, load, patch, setEjected]);

  const connectedUsb = useCallback((d: UsbDeviceLike) => connections.current.has(deviceId(d)), []);

  const eject = useCallback(async (id: string) => {
    setEjected(id, true);
    await drop(id);
    patch(id, { status: 'ejected', error: null });
  }, [drop, patch, setEjected]);

  const refresh = useCallback(async (id: string) => {
    const conn = connections.current.get(id);
    if (!conn) return;
    patch(id, { busy: 'Refreshing…' });
    try { await load(id, conn); } catch (err) { patch(id, { error: describeKindleError(err) }); } finally { patch(id, { busy: null }); }
  }, [load, patch]);

  const send = useCallback(async (
    id: string, bookId: number, onPhase?: (phase: string) => void,
  ): Promise<DeviceSendResult> => {
    const conn = connections.current.get(id);
    if (!conn) throw new Error('The Kindle is not connected.');
    patch(id, { busy: 'Sending…' });
    try {
      onPhase?.('preparing');
      const plan = await apiPost<{ format: string; converted: boolean; filename: string; download_url: string }>(
        `/api/v1/books/${bookId}/kindle-usb`, {});
      onPhase?.('downloading');
      const response = await fetch(resourceUrl(plan.download_url), { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      const bytes = new Uint8Array(await response.arrayBuffer());
      await conn.send(plan.filename, bytes, (p: SendPhase) => onPhase?.(p));
      await load(id, conn);
      return { format: plan.format, converted: plan.converted };
    } finally {
      patch(id, { busy: null });
    }
  }, [load, patch]);

  const read = useCallback(async (id: string, handle: number): Promise<Uint8Array> => {
    const conn = connections.current.get(id);
    if (!conn) throw new Error('The Kindle is not connected.');
    return conn.read(handle);
  }, []);

  const remove = useCallback(async (id: string, handle: number): Promise<void> => {
    const conn = connections.current.get(id);
    if (!conn) throw new Error('The Kindle is not connected.');
    patch(id, { busy: 'Removing…' });
    try {
      await conn.delete(handle);
      await load(id, conn);
    } finally {
      patch(id, { busy: null });
    }
  }, [load, patch]);

  /** Ask for a Kindle (chooser or the one remembered one) and connect it. */
  const pickAndAttach = useCallback(async (): Promise<string | null> => {
    if (!supported) return null;
    try {
      return await attach(await pickKindle(connectedUsb));
    } catch (err) {
      if (describeKindleError(err) === null) return null; // closed the chooser
      throw err;
    }
  }, [attach, connectedUsb, supported]);

  // Pick up Kindles the browser already has permission for, and follow plug/unplug.
  useEffect(() => {
    if (!supported) return;
    const usb = (navigator as unknown as { usb: UsbEvents }).usb;
    let cancelled = false;

    const adoptGranted = async () => {
      for (const device of await grantedKindles()) {
        const id = deviceId(device);
        if (cancelled || connections.current.has(id) || attaching.current.has(id) || ejected.current!.has(id)) continue;
        try { await attach(device); } catch { /* recorded in state */ }
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
      void drop(id).then(() => { if (!cancelled) forget(id); });
    };
    usb.addEventListener('connect', onConnect);
    usb.addEventListener('disconnect', onDisconnect);
    return () => {
      cancelled = true;
      usb.removeEventListener('connect', onConnect);
      usb.removeEventListener('disconnect', onDisconnect);
    };
  }, [supported, attach, drop, forget, setEjected]);

  // Hand the Kindles back when the app unmounts (sign-out).
  useEffect(() => {
    const open = connections.current;
    return () => { for (const id of [...open.keys()]) void drop(id); };
  }, [drop]);

  const view = useCallback((slot: Slot): ConnectedDevice => {
    const byBook = new Map<number, DeviceEntry>();
    for (const e of slot.entries) if (e.book && !byBook.has(e.book.id)) byBook.set(e.book.id, e);
    const { id } = slot;
    return {
      id, kind: 'kindle', label: LABEL,
      status: slot.status, name: slot.name, entries: slot.entries, space: slot.space,
      error: slot.error, busy: slot.busy,
      connect: async () => { await pickAndAttach(); },
      eject: () => eject(id),
      refresh: () => refresh(id),
      send: async (bookId, onPhase) => {
        // After an eject or an error there is no connection: the chooser needs this click's gesture,
        // so connect before any slow work. Whichever Kindle gets connected receives the book.
        let target = id;
        if (!connections.current.has(id)) {
          onPhase?.('connecting');
          const connected = await pickAndAttach();
          if (connected === null) throw new DOMException('No device chosen', 'NotFoundError');
          target = connected;
        }
        return send(target, bookId, onPhase);
      },
      read: (handle) => read(id, handle),
      remove: (handle) => remove(id, handle),
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
