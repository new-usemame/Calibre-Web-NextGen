/* What the app knows about a device plugged into the computer the browser runs on.
 *
 * Each kind of device (today only a Kindle over USB) is a driver in this folder that returns a
 * ConnectedDevice. The sidebar, the device page and the book page only ever see this shape, so a new
 * kind is one driver plus an entry in the list in ./index.tsx.
 */

export type DeviceStatus = 'unsupported' | 'none' | 'connecting' | 'connected' | 'ejected' | 'error';

export interface DeviceBook {
  id: number;
  title: string;
  authors: string[];
  /** null when the library book has no cover */
  cover_url: string | null;
}

/** A book file on the device, with the library book it is when one could be identified. */
export interface DeviceEntry {
  /** Unique within the device. */
  handle: number;
  name: string;
  /** Lower-case extension without the dot. */
  ext: string;
  size: number;
  /** Folder the file sits in, relative to the device's books folder; '' for the top level. */
  folder: string;
  book: DeviceBook | null;
}

export interface DeviceSendResult {
  format: string;
  converted: boolean;
}

export interface ConnectedDevice {
  /** Identifies this one device for as long as it stays plugged in; used in the URL: /device/<id>.
   *  Several devices, of one kind or of different kinds, can be connected at once. */
  id: string;
  /** Which driver handles it, e.g. 'kindle'. Never shown in the URL. */
  kind: string;
  /** What to call the device before it reports a name of its own, e.g. "Kindle". */
  label: string;
  status: DeviceStatus;
  /** Device name as it reports itself, e.g. "Kindle Paperwhite". Empty until connected. */
  name: string;
  entries: DeviceEntry[];
  space: { capacity: number; free: number } | null;
  error: string | null;
  /** Text for what a running send/refresh is doing, or null when idle. */
  busy: string | null;
  /** Connect it again after an eject or an error. Needs a user gesture. */
  connect: () => Promise<void>;
  eject: () => Promise<void>;
  refresh: () => Promise<void>;
  /** Prepare (convert if needed), download and push one book. Connects first when needed. */
  send: (bookId: number, onPhase?: (phase: string) => void) => Promise<DeviceSendResult>;
  /** The content of one of `entries`, to bring a book that is only on the device into the library. */
  read: (handle: number) => Promise<Uint8Array>;
  /** Delete one of `entries` from the device. */
  remove: (handle: number) => Promise<void>;
  /** The device's copy of a library book, or undefined. */
  onDevice: (bookId: number) => DeviceEntry | undefined;
  /** Text for a failure from this device, or null for something that is not a failure
   *  (the user closing the browser's device chooser). */
  describeError: (err: unknown) => string | null;
}

/** Whether a device should get a place in the sidebar: something to connect, or connected. */
export function isPresent(device: ConnectedDevice): boolean {
  return device.status !== 'unsupported' && device.status !== 'none';
}

/** A kind of device the browser can connect: what the "Send to device" button falls back to when
 *  nothing of that kind is connected yet, and how a further one is added. */
export interface DeviceSource {
  kind: string;
  label: string;
  /** False when this browser, or this account, cannot use the kind at all. */
  supported: boolean;
  /** Ask the browser for a device (its chooser needs a user gesture, so call this straight from a click)
   *  and connect it. Resolves with the device, or null when the person closed the chooser. */
  connect: () => Promise<ConnectedDevice | null>;
  /** Text for a failure from `connect`, or null for the person closing the chooser. */
  describeError: (err: unknown) => string | null;
}
