/* Send a book to a USB-connected Kindle straight from the browser.
 *
 * Modern Kindles present themselves over USB as an MTP device with a
 * vendor-specific interface (class 0xFF). The OS does not mount that as a
 * drive, but Chromium's WebUSB can claim it, so the page speaks MTP to the
 * Kindle itself and writes into /documents. That works even when the server is
 * remote, which is the usual self-hosted setup.
 *
 * Needs a Chromium browser and a secure context (https or localhost). On
 * Windows the stock MTP driver blocks WebUSB, so a WinUSB driver has to be
 * bound to the Kindle first.
 */

// WebUSB is not part of the DOM lib, so declare the small slice used here.
export interface UsbEndpointInfo { endpointNumber: number; direction: 'in' | 'out'; type: 'bulk' | 'interrupt' | 'isochronous'; packetSize: number }
export interface UsbAlternateInfo { alternateSetting: number; interfaceClass: number; endpoints: UsbEndpointInfo[] }
export interface UsbInterfaceInfo { interfaceNumber: number; alternates: UsbAlternateInfo[] }
export interface UsbConfigInfo { configurationValue: number; interfaces: UsbInterfaceInfo[] }
export interface UsbTransferResult { status: 'ok' | 'stall' | 'babble'; data?: DataView }
export interface UsbDeviceLike {
  vendorId: number;
  productName?: string | null;
  serialNumber?: string | null;
  opened: boolean;
  configuration: unknown;
  configurations: UsbConfigInfo[];
  open(): Promise<void>;
  close(): Promise<void>;
  selectConfiguration(value: number): Promise<void>;
  claimInterface(n: number): Promise<void>;
  releaseInterface(n: number): Promise<void>;
  selectAlternateInterface(n: number, alt: number): Promise<void>;
  transferIn(endpoint: number, length: number): Promise<UsbTransferResult>;
  transferOut(endpoint: number, data: Uint8Array): Promise<unknown>;
  clearHalt(direction: 'in' | 'out', endpoint: number): Promise<void>;
}
interface UsbApi {
  getDevices(): Promise<UsbDeviceLike[]>;
  requestDevice(options: { filters: Array<{ vendorId: number }> }): Promise<UsbDeviceLike>;
}

export const AMAZON_VENDOR_ID = 0x1949;

const OP = {
  OpenSession: 0x1002, CloseSession: 0x1003, GetStorageIDs: 0x1004, GetObjectHandles: 0x1007,
  GetObjectInfo: 0x1008, GetObject: 0x1009, SendObjectInfo: 0x100c, SendObject: 0x100d, DeleteObject: 0x100b,
} as const;
const RC_OK = 0x2001;
const RC_SESSION_ALREADY_OPEN = 0x201e;
const FMT_UNDEFINED = 0x3000;
const FMT_FOLDER = 0x3001;
export const ROOT_HANDLE = 0xffffffff;

export class MtpError extends Error {
  code: number;
  constructor(message: string, code: number) {
    super(message);
    this.name = 'MtpError';
    this.code = code;
  }
}

/** MTP string: u8 length counting the NUL, then UTF-16LE code units. */
export function encodeMtpString(s: string): Uint8Array {
  if (!s) return new Uint8Array([0]);
  const out = new Uint8Array(1 + (s.length + 1) * 2);
  out[0] = s.length + 1;
  for (let i = 0; i < s.length; i++) {
    out[1 + i * 2] = s.charCodeAt(i) & 0xff;
    out[2 + i * 2] = s.charCodeAt(i) >> 8;
  }
  return out;
}

class Reader {
  u8: Uint8Array;
  dv: DataView;
  p = 0;
  constructor(u8: Uint8Array) {
    this.u8 = u8;
    this.dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  }
  u16(): number { const v = this.dv.getUint16(this.p, true); this.p += 2; return v; }
  u32(): number { const v = this.dv.getUint32(this.p, true); this.p += 4; return v; }
  string(): string {
    const n = this.u8[this.p++];
    let s = '';
    for (let i = 0; i < n; i++) {
      const c = this.dv.getUint16(this.p, true);
      this.p += 2;
      if (c) s += String.fromCharCode(c);
    }
    return s;
  }
  u32Array(): number[] {
    const n = this.u32();
    const out: number[] = [];
    for (let i = 0; i < n; i++) out.push(this.u32());
    return out;
  }
}

export interface MtpObject { handle: number; format: number; size: number; parent: number; name: string; isFolder: boolean }

export interface MtpInterface { number: number; config: number; alt: number; epIn: number; epOut: number; packetSize: number }

/** The interface to claim: bulk in + out on a vendor-specific (0xFF) or still-image (0x06) class. */
export function findMtpInterface(device: Pick<UsbDeviceLike, 'configurations'>): MtpInterface | null {
  for (const cfg of device.configurations) {
    for (const itf of cfg.interfaces) {
      const alt = itf.alternates[0];
      if (!alt) continue;
      const bulkIn = alt.endpoints.find((e) => e.type === 'bulk' && e.direction === 'in');
      const bulkOut = alt.endpoints.find((e) => e.type === 'bulk' && e.direction === 'out');
      if ((alt.interfaceClass === 0xff || alt.interfaceClass === 0x06) && bulkIn && bulkOut) {
        return {
          number: itf.interfaceNumber, config: cfg.configurationValue, alt: alt.alternateSetting,
          epIn: bulkIn.endpointNumber, epOut: bulkOut.endpointNumber, packetSize: bulkIn.packetSize,
        };
      }
    }
  }
  return null;
}

export class MtpSession {
  private device: Pick<UsbDeviceLike, 'transferIn' | 'transferOut' | 'clearHalt'>;
  private iface: Pick<MtpInterface, 'epIn' | 'epOut' | 'packetSize'>;
  private buffer: Uint8Array = new Uint8Array(0);
  private transactionId = 0;
  private sessionOpen = false;

  constructor(device: MtpSession['device'], iface: MtpSession['iface']) {
    this.device = device;
    this.iface = iface;
  }

  private container(type: number, code: number, tid: number, params: number[] = [], payload?: Uint8Array): Uint8Array {
    const out = new Uint8Array(12 + params.length * 4 + (payload?.length ?? 0));
    const dv = new DataView(out.buffer);
    dv.setUint32(0, out.length, true);
    dv.setUint16(4, type, true);
    dv.setUint16(6, code, true);
    dv.setUint32(8, tid, true);
    params.forEach((p, i) => dv.setUint32(12 + i * 4, p >>> 0, true));
    if (payload) out.set(payload, 12 + params.length * 4);
    return out;
  }

  private async write(u8: Uint8Array): Promise<void> {
    const CHUNK = 1 << 20;
    for (let o = 0; o < u8.length; o += CHUNK) {
      await this.device.transferOut(this.iface.epOut, u8.subarray(o, o + CHUNK));
    }
    // A transfer ending exactly on a packet boundary is closed with a zero-length packet.
    if (u8.length % this.iface.packetSize === 0) {
      await this.device.transferOut(this.iface.epOut, new Uint8Array(0));
    }
  }

  private async fill(n: number): Promise<void> {
    while (this.buffer.length < n) {
      const r = await this.device.transferIn(this.iface.epIn, 65536);
      if (r.status === 'stall') {
        await this.device.clearHalt('in', this.iface.epIn);
        continue;
      }
      if (!r.data) continue;
      const chunk = new Uint8Array(r.data.buffer, r.data.byteOffset, r.data.byteLength);
      const grown = new Uint8Array(this.buffer.length + chunk.length);
      grown.set(this.buffer);
      grown.set(chunk, this.buffer.length);
      this.buffer = grown;
    }
  }

  private async readContainer(): Promise<{ type: number; code: number; payload: Uint8Array }> {
    await this.fill(12);
    const dv = new DataView(this.buffer.buffer, this.buffer.byteOffset, 12);
    const length = dv.getUint32(0, true);
    if (length === 0xffffffff) throw new MtpError('Unsupported unknown-length MTP data container', 0);
    await this.fill(length);
    const container = { type: dv.getUint16(4, true), code: dv.getUint16(6, true), payload: this.buffer.slice(12, length) };
    this.buffer = this.buffer.slice(length);
    return container;
  }

  async transact(code: number, params: number[] = [], data?: Uint8Array): Promise<{ params: number[]; data: Uint8Array | null }> {
    const tid = this.sessionOpen ? ++this.transactionId : 0;
    await this.write(this.container(1, code, tid, params));
    if (data) await this.write(this.container(2, code, tid, [], data));
    let received: Uint8Array | null = null;
    for (;;) {
      const c = await this.readContainer();
      if (c.type === 2) {
        received = c.payload;
      } else if (c.type === 3) {
        if (c.code !== RC_OK) {
          throw new MtpError(`MTP operation 0x${code.toString(16)} failed (0x${c.code.toString(16)})`, c.code);
        }
        const r = new Reader(c.payload);
        const out: number[] = [];
        while (r.p + 4 <= c.payload.length) out.push(r.u32());
        return { params: out, data: received };
      }
    }
  }

  async open(): Promise<void> {
    try {
      await this.transact(OP.OpenSession, [1]);
    } catch (e) {
      if (!(e instanceof MtpError) || e.code !== RC_SESSION_ALREADY_OPEN) throw e;
    }
    this.sessionOpen = true;
    this.transactionId = 0;
  }

  async close(): Promise<void> {
    if (!this.sessionOpen) return;
    try { await this.transact(OP.CloseSession); } catch { /* the device may already be gone */ }
    this.sessionOpen = false;
  }

  async storageId(): Promise<number> {
    const r = await this.transact(OP.GetStorageIDs);
    const ids = r.data ? new Reader(r.data).u32Array() : [];
    if (!ids.length) throw new MtpError('The Kindle reports no storage. Unlock it and try again.', 0);
    return ids[0];
  }

  /** Capacity and free space of a storage, in bytes. */
  async storageInfo(storageId: number): Promise<{ capacity: number; free: number }> {
    const r = await this.transact(0x1005, [storageId]);
    const rd = new Reader(r.data ?? new Uint8Array(0));
    rd.u16(); rd.u16(); rd.u16();
    const u64 = () => {
      const v = Number(new DataView(rd.u8.buffer, rd.u8.byteOffset + rd.p, 8).getBigUint64(0, true));
      rd.p += 8;
      return v;
    };
    const capacity = u64();
    return { capacity, free: u64() };
  }

  async objectInfo(handle: number): Promise<MtpObject> {
    const r = await this.transact(OP.GetObjectInfo, [handle]);
    const rd = new Reader(r.data ?? new Uint8Array(0));
    rd.u32();
    const format = rd.u16();
    rd.u16();
    const size = rd.u32();
    rd.u16(); rd.u32(); rd.u32(); rd.u32(); rd.u32(); rd.u32(); rd.u32();
    const parent = rd.u32();
    rd.u16(); rd.u32(); rd.u32();
    return { handle, format, size, parent, name: rd.string(), isFolder: format === FMT_FOLDER };
  }

  async list(storageId: number, parent: number): Promise<MtpObject[]> {
    const r = await this.transact(OP.GetObjectHandles, [storageId, 0, parent]);
    const handles = r.data ? new Reader(r.data).u32Array() : [];
    const out: MtpObject[] = [];
    for (const h of handles) out.push(await this.objectInfo(h));
    return out;
  }

  /** The whole content of one object. */
  async download(handle: number): Promise<Uint8Array> {
    const r = await this.transact(OP.GetObject, [handle]);
    return r.data ?? new Uint8Array(0);
  }

  async remove(handle: number): Promise<void> {
    await this.transact(OP.DeleteObject, [handle, 0]);
  }

  async upload(storageId: number, parent: number, name: string, bytes: Uint8Array): Promise<void> {
    const fn = encodeMtpString(name);
    const empty = encodeMtpString('');
    const info = new Uint8Array(52 + fn.length + empty.length * 3);
    const dv = new DataView(info.buffer);
    dv.setUint16(4, FMT_UNDEFINED, true);
    dv.setUint32(8, bytes.length, true);
    dv.setUint32(38, parent, true);
    info.set(fn, 52);
    await this.transact(OP.SendObjectInfo, [storageId, parent], info);
    await this.transact(OP.SendObject, [], bytes);
  }
}

/** True where WebUSB can work: a Chromium browser on https or localhost. */
export function isWebUsbSupported(): boolean {
  return typeof navigator !== 'undefined' && 'usb' in navigator
    && typeof window !== 'undefined' && window.isSecureContext;
}

function usbApi(): UsbApi {
  return (navigator as unknown as { usb: UsbApi }).usb;
}

/** Kindles the browser already has permission for. */
export async function grantedKindles(): Promise<UsbDeviceLike[]> {
  return (await usbApi().getDevices()).filter((d) => d.vendorId === AMAZON_VENDOR_ID);
}

/** A remembered Kindle when exactly one is granted and not skipped (already connected), otherwise the
 *  browser's chooser. The chooser needs a user gesture, so call this first in the click handler. */
export async function pickKindle(skip: (device: UsbDeviceLike) => boolean = () => false): Promise<UsbDeviceLike> {
  const candidates = (await grantedKindles()).filter((d) => !skip(d));
  if (candidates.length === 1) return candidates[0];
  return usbApi().requestDevice({ filters: [{ vendorId: AMAZON_VENDOR_ID }] });
}

export type SendPhase = 'connecting' | 'sending';

/** What counts as a book when listing the Kindle: sidecars, thumbnails and `.sdr` folders do not. */
export const DEVICE_BOOK_EXTENSIONS = new Set(['azw', 'azw3', 'kfx', 'mobi', 'prc', 'pdf', 'txt', 'epub']);
const MAX_FOLDER_DEPTH = 3;

export interface DeviceFile {
  handle: number;
  name: string;
  size: number;
  /** Folder path below documents/ ('' for the top level). Calibre files books into author folders. */
  folder: string;
  ext: string;
}

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot < 0 ? '' : name.slice(dot + 1).toLowerCase();
}

/** An open MTP session on a Kindle, kept claimed until close() so the app can list and send repeatedly.
 *  MTP is strictly one transaction at a time, so every public call is queued behind the previous. */
export class KindleConnection {
  readonly name: string;
  readonly serial: string;
  private device: UsbDeviceLike;
  private session: MtpSession;
  private claimed: number;
  private storage: number;
  private docs: number;
  private chain: Promise<unknown> = Promise.resolve();
  private closed = false;

  private constructor(device: UsbDeviceLike, session: MtpSession, claimed: number, storage: number, docs: number) {
    this.device = device;
    this.session = session;
    this.claimed = claimed;
    this.storage = storage;
    this.docs = docs;
    this.name = device.productName || 'Kindle';
    this.serial = device.serialNumber || '';
  }

  /** Claim the Kindle's MTP interface and locate its documents folder. */
  static async open(device: UsbDeviceLike, onPhase?: (phase: SendPhase) => void): Promise<KindleConnection> {
    onPhase?.('connecting');
    await device.open();
    let claimed: number | null = null;
    let session: MtpSession | null = null;
    try {
      if (device.configuration === null) await device.selectConfiguration(1);
      const iface = findMtpInterface(device);
      if (!iface) throw new MtpError('This USB device does not expose an MTP interface.', 0);
      await device.claimInterface(iface.number);
      claimed = iface.number;
      if (iface.alt) await device.selectAlternateInterface(iface.number, iface.alt);
      session = new MtpSession(device, iface);
      await session.open();
      const storage = await session.storageId();
      const root = await session.list(storage, ROOT_HANDLE);
      const docs = root.find((o) => o.isFolder && o.name.toLowerCase() === 'documents');
      if (!docs) throw new MtpError('No "documents" folder found on the Kindle.', 0);
      return new KindleConnection(device, session, iface.number, storage, docs.handle);
    } catch (e) {
      // Do not leave the interface claimed behind a failed open.
      try { if (session) await session.close(); } catch { /* ignore */ }
      try { if (claimed !== null) await device.releaseInterface(claimed); } catch { /* ignore */ }
      try { await device.close(); } catch { /* ignore */ }
      throw e;
    }
  }

  private run<T>(task: () => Promise<T>): Promise<T> {
    const next = this.chain.then(() => {
      if (this.closed) throw new MtpError('The Kindle is no longer connected.', 0);
      return task();
    });
    this.chain = next.catch(() => undefined);
    return next;
  }

  space(): Promise<{ capacity: number; free: number }> {
    return this.run(() => this.session.storageInfo(this.storage));
  }

  /** Every book file under documents/, to a few folders deep. */
  listBooks(): Promise<DeviceFile[]> {
    return this.run(async () => {
      const out: DeviceFile[] = [];
      const walk = async (parent: number, folder: string, depth: number): Promise<void> => {
        for (const o of await this.session.list(this.storage, parent)) {
          if (o.isFolder) {
            if (depth < MAX_FOLDER_DEPTH && !o.name.toLowerCase().endsWith('.sdr')) {
              await walk(o.handle, folder ? `${folder}/${o.name}` : o.name, depth + 1);
            }
            continue;
          }
          const ext = fileExtension(o.name);
          if (DEVICE_BOOK_EXTENSIONS.has(ext)) out.push({ handle: o.handle, name: o.name, size: o.size, folder, ext });
        }
      };
      await walk(this.docs, '', 0);
      return out;
    });
  }

  /** The bytes of a file found by listBooks(). */
  read(handle: number): Promise<Uint8Array> {
    return this.run(() => this.session.download(handle));
  }

  /** Delete a file found by listBooks(). Its .sdr sidecar folder (reading position, notes) is left to the Kindle. */
  delete(handle: number): Promise<void> {
    return this.run(() => this.session.remove(handle));
  }

  /** Write a book into documents/, replacing an earlier copy of the same name. */
  send(filename: string, bytes: Uint8Array, onPhase?: (phase: SendPhase) => void): Promise<void> {
    return this.run(async () => {
      onPhase?.('sending');
      const existing = (await this.session.list(this.storage, this.docs)).find((o) => o.name === filename);
      if (existing) await this.session.remove(existing.handle);
      await this.session.upload(this.storage, this.docs, filename, bytes);
    });
  }

  /** End the session and hand the interface back, so Calibre and similar tools can use the Kindle again. */
  async close(): Promise<void> {
    if (this.closed) return;
    // Let an in-flight transfer finish rather than cutting it off mid-file.
    await this.chain;
    this.closed = true;
    try { await this.session.close(); } catch { /* ignore */ }
    try { await this.device.releaseInterface(this.claimed); } catch { /* ignore */ }
    try { await this.device.close(); } catch { /* ignore */ }
  }
}

/** One-shot send: connect, write the book, disconnect. */
export async function sendToKindle(
  device: UsbDeviceLike, filename: string, bytes: Uint8Array, onPhase?: (phase: SendPhase) => void,
): Promise<void> {
  const connection = await KindleConnection.open(device, onPhase);
  try {
    await connection.send(filename, bytes, onPhase);
  } finally {
    await connection.close();
  }
}
