import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compareCodePoints } from '../.build/host/dist/order.js';

test('NEW-1-T01 compareCodePoints equals UTF-8 byte order for well-formed strings', () => {
    const input = ['a_b', 'aB', 'Ab', '中', 'é', 'Z', 'a', '𝒜', 'Ａ', '9', '-', ''];
    const expected = ['', '-', '9', 'Ab', 'Z', 'a', 'aB', 'a_b', 'é', '中', 'Ａ', '𝒜'];
    assert.deepEqual([...input].sort(compareCodePoints), expected);
    const pool = ['a', 'B', '_', '-', '9', 'é', '中', String.fromCodePoint(0xE000), String.fromCodePoint(0xFF21), String.fromCodePoint(0xFFFF), String.fromCodePoint(0x1D49C), String.fromCodePoint(0x10FFFF)];
    let seed = 123456789;
    const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
    for (let i = 0; i < 50000; i++) {
        let a = '', b = '';
        const la = 1 + Math.floor(random() * 6), lb = 1 + Math.floor(random() * 6);
        for (let j = 0; j < la; j++)
            a += pool[Math.floor(random() * pool.length)];
        for (let j = 0; j < lb; j++)
            b += pool[Math.floor(random() * pool.length)];
        assert.ok(a.isWellFormed() && b.isWellFormed(), `pair ${i} is not well-formed`);
        assert.equal(Math.sign(compareCodePoints(a, b)), Math.sign(Buffer.compare(Buffer.from(a), Buffer.from(b))), `pair ${i}: ${JSON.stringify([a, b])}`);
    }
    assert.ok(compareCodePoints('\uD800', '\uFFFD') > 0);
    assert.equal(Buffer.compare(Buffer.from('\uD800'), Buffer.from('\uFFFD')), 0);
});
