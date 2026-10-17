import assert from 'node:assert/strict';
import test from 'node:test';
import { encodeMtpString, findMtpInterface, sendToKindle, MtpError, KindleConnection, fileExtension } from '../src/lib/device/kindleUsb.ts';
import type { UsbDeviceLike } from '../src/lib/device/kindleUsb.ts';

const OP = { OpenSession: 0x1002, CloseSession: 0x1003, GetStorageIDs: 0x1004, GetObjectHandles: 0x1007,
  GetObjectInfo: 0x1008, GetObject: 0x1009, DeleteObject: 0x100b, SendObjectInfo: 0x100c, SendObject: 0x100d };

const cat = (...parts: Uint8Array[]) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
};
const le32 = (...v: number[]) => {
  const out = new Uint8Array(v.length * 4);
  v.forEach((x, i) => new DataView(out.buffer).setUint32(i * 4, x, true));
  return out;
};
const container = (type: number, code: number, tid: number, payload: Uint8Array = new Uint8Array(0), params: number[] = []) => {
  const out = new Uint8Array(12 + params.length * 4 + payload.length);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, out.length, true); dv.setUint16(4, type, true); dv.setUint16(6, code, true); dv.setUint32(8, tid, true);
  params.forEach((p, i) => dv.setUint32(12 + i * 4, p, true));
  out.set(payload, 12 + params.length * 4);
  return out;
};
const objectInfo = (name: string, format: number, size: number, parent = 0) => cat(
  le32(0x10001), new Uint8Array([format & 0xff, format >> 8]), new Uint8Array(2), le32(size),
  new Uint8Array(2), le32(0, 0, 0, 0, 0, 0), le32(parent), new Uint8Array(2), le32(0, 0),
  encodeMtpString(name), new Uint8Array([0, 0, 0]));

/** A fake Kindle: root has /documents (handle 7) holding one stale copy of the book (handle 8). */
function fakeKindle(opts: { contents?: Map<number, Uint8Array>; existing?: string; extra?: Array<[number, { name: string; format: number; size: number; parent: number }]> } = {}) {
  const existingName = opts.existing ?? 'Dune - Frank Herbert.azw3';
  const objects = new Map<number, { name: string; format: number; size: number; parent: number }>([
    [7, { name: 'documents', format: 0x3001, size: 0, parent: 0 }],
    [8, { name: existingName, format: 0x3000, size: 10, parent: 7 }],
    ...(opts.extra ?? []),
  ]);
  const queue: Uint8Array[] = [];
  const log = { released: [] as number[], closed: false, deleted: [] as number[], uploaded: null as null | { name: string; bytes: Uint8Array; parent: number }, zlp: 0 };
  let pendingInfo: Uint8Array | null = null;
  let pendingParent = 0;

  const respond = (u8: Uint8Array) => {
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    const type = dv.getUint16(4, true), code = dv.getUint16(6, true), tid = dv.getUint32(8, true);
    const p0 = u8.length >= 16 ? dv.getUint32(12, true) : 0;
    if (type === 2 && code === OP.SendObjectInfo) { pendingInfo = u8.slice(12); queue.push(container(3, 0x2001, tid, new Uint8Array(0), [0x10001, pendingParent, 99])); return; }
    if (type === 2 && code === OP.SendObject) {
      const nameLen = pendingInfo![52];
      let name = '';
      for (let i = 0; i < nameLen - 1; i++) name += String.fromCharCode(pendingInfo![53 + i * 2]);
      log.uploaded = { name, bytes: u8.slice(12), parent: pendingParent };
      queue.push(container(3, 0x2001, tid));
      return;
    }
    if (type !== 1) return;
    if (code === OP.SendObjectInfo) { pendingParent = dv.getUint32(16, true); return; }
    if (code === OP.OpenSession || code === OP.CloseSession) queue.push(container(3, 0x2001, tid));
    else if (code === 0x1005) {
      const u64 = (n: number) => { const b = new Uint8Array(8); new DataView(b.buffer).setBigUint64(0, BigInt(n), true); return b; };
      queue.push(container(2, code, tid, cat(new Uint8Array(6), u64(8_000_000_000), u64(5_000_000_000), le32(0), encodeMtpString('Internal Storage'), encodeMtpString(''))), container(3, 0x2001, tid));
    }
    else if (code === OP.GetStorageIDs) queue.push(container(2, code, tid, cat(le32(1, 0x10001))), container(3, 0x2001, tid));
    else if (code === OP.GetObjectHandles) {
      const parent = dv.getUint32(20, true);
      const handles = [...objects].filter(([, o]) => (parent === 0xffffffff ? o.parent === 0 : o.parent === parent)).map(([h]) => h);
      queue.push(container(2, code, tid, cat(le32(handles.length, ...handles))), container(3, 0x2001, tid));
    } else if (code === OP.GetObjectInfo) {
      const o = objects.get(p0)!;
      queue.push(container(2, code, tid, objectInfo(o.name, o.format, o.size, o.parent)), container(3, 0x2001, tid));
    } else if (code === OP.GetObject) {
      queue.push(container(2, code, tid, opts.contents?.get(p0) ?? new Uint8Array(0)), container(3, 0x2001, tid));
    } else if (code === OP.DeleteObject) { log.deleted.push(p0); objects.delete(p0); queue.push(container(3, 0x2001, tid)); }
  };

  const device: UsbDeviceLike = {
    vendorId: 0x1949, opened: false, configuration: {},
    configurations: [{ configurationValue: 1, interfaces: [{ interfaceNumber: 0, alternates: [{
      alternateSetting: 0, interfaceClass: 0xff, endpoints: [
        { endpointNumber: 1, direction: 'in', type: 'bulk', packetSize: 512 },
        { endpointNumber: 2, direction: 'out', type: 'bulk', packetSize: 512 },
        { endpointNumber: 3, direction: 'in', type: 'interrupt', packetSize: 28 },
      ] }] }] }],
    async open() { this.opened = true; },
    async close() { log.closed = true; this.opened = false; },
    async selectConfiguration() {},
    async claimInterface() {},
    async releaseInterface(n) { log.released.push(n); },
    async selectAlternateInterface() {},
    async clearHalt() {},
    async transferOut(_ep, data) {
      if (data.length === 0) { log.zlp++; return {}; }
      if (data.length >= 12) respond(data);
      return {};
    },
    async transferIn() {
      const next = queue.shift();
      assert.ok(next, 'device was asked for data it never queued');
      return { status: 'ok', data: new DataView(next.buffer, next.byteOffset, next.byteLength) };
    },
  };
  return { device, log };
}

test('MTP strings carry the NUL in the length byte and are UTF-16LE', () => {
  assert.deepEqual([...encodeMtpString('')], [0]);
  assert.deepEqual([...encodeMtpString('ab')], [3, 0x61, 0, 0x62, 0, 0, 0]);
  assert.equal(encodeMtpString('Zażółć').length, 1 + 7 * 2);
});

test('the Kindle MTP interface (vendor class 0xFF, bulk in/out) is found; mass storage is not', () => {
  const k = fakeKindle().device;
  assert.deepEqual(findMtpInterface(k), { number: 0, config: 1, alt: 0, epIn: 1, epOut: 2, packetSize: 512 });
  const storage = { configurations: [{ configurationValue: 1, interfaces: [{ interfaceNumber: 0, alternates: [{
    alternateSetting: 0, interfaceClass: 0x08, endpoints: [
      { endpointNumber: 1, direction: 'in' as const, type: 'bulk' as const, packetSize: 512 },
      { endpointNumber: 2, direction: 'out' as const, type: 'bulk' as const, packetSize: 512 }] }] }] }] };
  assert.equal(findMtpInterface(storage), null);
});

test('sendToKindle uploads into /documents, replaces an earlier copy, and releases the interface', async () => {
  const { device, log } = fakeKindle();
  const book = new Uint8Array(1000).fill(7);
  const phases: string[] = [];
  await sendToKindle(device, 'Dune - Frank Herbert.azw3', book, (p) => phases.push(p));
  assert.deepEqual(phases, ['connecting', 'sending']);
  assert.deepEqual(log.deleted, [8], 'stale copy removed first');
  assert.equal(log.uploaded?.parent, 7, 'written into the documents folder');
  assert.equal(log.uploaded?.name, 'Dune - Frank Herbert.azw3');
  assert.deepEqual(log.uploaded?.bytes, book);
  assert.deepEqual(log.released, [0]);
  assert.equal(log.closed, true);
});

test('a different filename is not treated as a duplicate', async () => {
  const { device, log } = fakeKindle({ existing: 'Other.azw3' });
  await sendToKindle(device, 'Dune.azw3', new Uint8Array(10));
  assert.deepEqual(log.deleted, []);
  assert.equal(log.uploaded?.name, 'Dune.azw3');
});

test('a payload that ends on a packet boundary is closed with a zero-length packet', async () => {
  const { device, log } = fakeKindle();
  await sendToKindle(device, 'Edge.azw3', new Uint8Array(1012)); // 12-byte header + 1012 = 1024 = 2 x 512
  assert.ok(log.zlp >= 1);
});

test('the interface is released even when the transfer fails', async () => {
  const { device, log } = fakeKindle();
  device.transferIn = async () => { throw new MtpError('cable pulled', 0); };
  await assert.rejects(() => sendToKindle(device, 'x.azw3', new Uint8Array(4)), /cable pulled/);
  assert.deepEqual(log.released, [0]);
  assert.equal(log.closed, true);
});

const FOLDER = 0x3001, FILE = 0x3000;

test('listBooks walks author folders, skips .sdr sidecars and non-book files', async () => {
  const { device } = fakeKindle({
    existing: 'Top Level.azw3',
    extra: [
      [20, { name: 'Mafi, Tahereh', format: FOLDER, size: 0, parent: 7 }],
      [21, { name: 'Shatter Me - Tahereh Mafi.azw3', format: FILE, size: 500, parent: 20 }],
      [22, { name: 'Shatter Me - Tahereh Mafi.sdr', format: FOLDER, size: 0, parent: 7 }],
      [23, { name: 'Shatter Me - Tahereh Mafi.azw3r', format: FILE, size: 5, parent: 22 }],
      [24, { name: 'cover.jpg', format: FILE, size: 99, parent: 7 }],
      [25, { name: 'Notes.TXT', format: FILE, size: 7, parent: 7 }],
    ],
  });
  const conn = await KindleConnection.open(device);
  const books = await conn.listBooks();
  assert.deepEqual(
    books.map((b) => [b.folder, b.name, b.ext]).sort(),
    [['', 'Notes.TXT', 'txt'], ['', 'Top Level.azw3', 'azw3'], ['Mafi, Tahereh', 'Shatter Me - Tahereh Mafi.azw3', 'azw3']],
  );
  await conn.close();
});

test('a connection reports free space and its device name', async () => {
  const { device } = fakeKindle();
  (device as { productName?: string }).productName = 'Kindle Paperwhite';
  const conn = await KindleConnection.open(device);
  assert.equal(conn.name, 'Kindle Paperwhite');
  assert.deepEqual(await conn.space(), { capacity: 8_000_000_000, free: 5_000_000_000 });
  await conn.close();
});

test('one connection serves several operations in order and stays claimed until closed', async () => {
  const { device, log } = fakeKindle();
  const conn = await KindleConnection.open(device);
  const [first, second] = await Promise.all([
    conn.send('A.azw3', new Uint8Array(5)),
    conn.listBooks(),
  ]);
  void first;
  assert.ok(second.length >= 1);
  assert.equal(log.uploaded?.name, 'A.azw3');
  assert.deepEqual(log.released, [], 'still claimed between operations');
  await conn.close();
  assert.deepEqual(log.released, [0]);
  assert.equal(log.closed, true);
  await assert.rejects(() => conn.listBooks(), /no longer connected/);
});

test('a failed open releases the interface it claimed', async () => {
  const { device, log } = fakeKindle();
  device.transferIn = async () => { throw new MtpError('no answer', 0); };
  await assert.rejects(() => KindleConnection.open(device), /no answer/);
  assert.deepEqual(log.released, [0]);
});

test('file extensions are lower-cased and a name without one is empty', () => {
  assert.equal(fileExtension('Book.AZW3'), 'azw3');
  assert.equal(fileExtension('README'), '');
});

test('delete removes the file from the Kindle', async () => {
  const { device, log } = fakeKindle({
    extra: [[20, { name: 'Loose.azw3', format: 0x3000, size: 5, parent: 7 }]],
  });
  const conn = await KindleConnection.open(device);
  await conn.delete(20);
  assert.deepEqual(log.deleted, [20]);
  assert.deepEqual((await conn.listBooks()).map((f) => f.name), ['Dune - Frank Herbert.azw3']);
  await conn.close();
});

test('read returns the content of a file on the Kindle, larger than one USB packet', async () => {
  const bytes = Uint8Array.from({ length: 70_000 }, (_, i) => i % 251);
  const { device } = fakeKindle({
    extra: [[20, { name: 'Loose.azw3', format: 0x3000, size: bytes.length, parent: 7 }]],
    contents: new Map([[20, bytes]]),
  });
  const conn = await KindleConnection.open(device);
  assert.deepEqual(await conn.read(20), bytes);
  await conn.close();
});
