import test from 'node:test';
import assert from 'node:assert/strict';
import {jsonByteBound, textChunks} from '../.build/core/dist/results/payload.js';

const codeOf = error => error && typeof error === 'object' ? error.code : undefined;
const expectCode = (body, code) => assert.throws(body, error => codeOf(error) === code);

test('RV3-08-T01 bounded JSON bytes and chunking match native stringify', async t => {
  await t.test('exact bytes for JSON containers and scalars', () => {
    const shared = '共享';
    const samples = [
      null, true, false, 0, -0, 1.5, NaN, Infinity, -Infinity,
      '', '中文', 'a\u0000b', '\ud800', '\udc00', '😀',
      [undefined, NaN, -0, Infinity],
      {a: undefined, b: () => 1, c: [undefined]},
      {a: shared, b: shared, c: [shared, shared]},
      {数字键: 1, '0': 'zero'},
    ];
    for (const value of samples) {
      assert.equal(jsonByteBound(value, 1 << 20), Buffer.byteLength(JSON.stringify(value)), JSON.stringify(value));
    }
    let nested = [];
    for (let i = 0; i < 3000; i++) nested = [nested];
    assert.equal(jsonByteBound(nested, 1 << 20), Buffer.byteLength(JSON.stringify(nested)));
    const many = Array.from({length: 200000}, (_, i) => ({i}));
    assert.equal(jsonByteBound(many, 64 << 20), Buffer.byteLength(JSON.stringify(many)));
  });

  await t.test('limit returns limit+1 and top-level non-JSON is rejected', () => {
    assert.equal(jsonByteBound({a: 'x'.repeat(100)}, 10), 11);
    const cycle = {};
    cycle.self = cycle;
    for (const value of [undefined, () => 1, Symbol('x'), new Date(), {toJSON: () => 1}, 1n, cycle]) {
      expectCode(() => jsonByteBound(value, 1024), 'PAYLOAD_JSON_VALUE');
    }
  });

  await t.test('textChunks preserves Unicode and rejects too-small chunks', async () => {
    const text = 'a😀中文\ud800\u0001'.repeat(100);
    for (const chunkBytes of [6, 7, 65536]) {
      const parts = [];
      for await (const part of textChunks(text, chunkBytes)) {
        assert.ok(part.byteLength <= chunkBytes);
        parts.push(part);
      }
      assert.deepEqual(Buffer.concat(parts), Buffer.from(text));
    }
    for (const chunkBytes of [1, 3, 5]) {
      const consume = (async () => {
        for await (const _ of textChunks(text, chunkBytes)) break;
      })();
      await assert.rejects(Promise.race([consume, new Promise((_, reject) => setTimeout(() => reject(Error('chunk-timeout')), 2000))]), error => codeOf(error) === 'PAYLOAD_CHUNK_BUDGET');
    }
  });

  await t.test('performance gates for large payloads', () => {
    const many = Array.from({length: 200000}, (_, i) => ({i}));
    const pass = [];
    for (let i = 0; i < 3; i++) {
      const start = performance.now();
      jsonByteBound(many, 64 << 20);
      pass.push(performance.now() - start);
    }
    pass.sort((a, b) => a - b);
    const passMedian = pass[1];
    assert.ok(passMedian <= 600, `jsonByteBound 200k objects ${passMedian}ms`);
    const big = '中文'.repeat(1398101);
    const stringify = [];
    const bounded = [];
    for (let i = 0; i < 3; i++) {
      let start = performance.now();
      JSON.stringify(big);
      stringify.push(performance.now() - start);
      start = performance.now();
      jsonByteBound(big, 64 << 20);
      bounded.push(performance.now() - start);
    }
    stringify.sort((a, b) => a - b);
    bounded.sort((a, b) => a - b);
    assert.ok(bounded[1] <= stringify[1] * 4, `bound ${bounded[1]}ms vs stringify ${stringify[1]}ms`);
  });
});
