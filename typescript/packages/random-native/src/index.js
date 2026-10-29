import { createRequire } from 'node:module';
const native = createRequire(import.meta.url)('../build/random_native.node');
export const algorithm = 'philox4x32-10/v1';
export const mapping = 'length-prefixed-utf8-seed-seq/v1';
// Each encoded byte is one std::seed_seq input integer; independent of machine byte order.
export function encodeAddress(seed, address) {
  const fields = [seed, address?.run, address?.actor, address?.operation, address?.stream];
  const parts = [Buffer.from('society0/random/address/v1\0', 'utf8')];
  for (const field of fields) {
    if (typeof field !== 'string' || !field.isWellFormed()) throw new TypeError('RANDOM_ADDRESS_STRING');
    const data = Buffer.from(field, 'utf8');
    const length = Buffer.alloc(8); length.writeBigUInt64LE(BigInt(data.length));
    parts.push(length, data);
  }
  return Buffer.concat(parts);
}
export function at(seed, address) {
  const encoded = encodeAddress(seed, address);
  const value = address.index;
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]*)$/.test(value)) throw new TypeError('RANDOM_INDEX_DECIMAL');
  if (value.length > 20) throw new RangeError('RANDOM_INDEX_RANGE');
  const index = BigInt(value);
  if (index > 0xffffffffffffffffn) throw new RangeError('RANDOM_INDEX_RANGE');
  const words = native.address(encoded, index);
  return { uint32: words[0], uniform: ((words[0] >>> 5) * 67108864 + (words[1] >>> 6)) / 9007199254740992 };
}
// Official primitive exposed for known-answer conformance tests; no hidden state.
export const philox = native.philox;
