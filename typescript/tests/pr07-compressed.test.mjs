import { test } from 'node:test';
import assert from 'node:assert/strict';
import { zstdCompressSync, zstdDecompressSync } from 'node:zlib';
import { setup, read, request, scope, page } from './pr07-support.mjs';
test('PR07-T20 genuine Zstd frames retain continuous Chinese/long-line offsets and bounded response', async () => {
    const env = await setup({ migrations: [{ owner: 'archive', from: 0, to: 1, after: ['artifacts-local'], source: { consumer: 'compressed-original' }, statements: [{ sql: 'CREATE TABLE archive_frames(id TEXT PRIMARY KEY NOT NULL,actor TEXT NOT NULL,artifact TEXT NOT NULL,frames TEXT NOT NULL,raw_bytes INTEGER NOT NULL,version INTEGER NOT NULL)' }] }] });
    const { ctx } = env;
    let session;
    try {
        const original = Buffer.from('x'.repeat(65535) + '跨块中文' + 'y'.repeat(70000) + '\n尾部');
        const frames = [], compressed = [];
        let stored = 0;
        for (let offset = 0; offset < original.length; offset += 65536) {
            const raw = original.subarray(offset, offset + 65536), encoded = zstdCompressSync(raw);
            frames.push({ rawOffset: offset, rawBytes: raw.length, offset: stored, length: encoded.length });
            compressed.push(encoded);
            stored += encoded.length;
        }
        assert.equal(original[65535], 0xe8);
        async function* source() { yield* compressed; }
        const object = await ctx.artifacts.stage(source(), 'application/zstd');
        await ctx.database.transaction(env.reservation.lease, request('archive'), async (tx) => { await ctx.artifacts.reference(tx, object); await tx.execute({ sql: 'INSERT INTO archive_frames VALUES(?,?,?,?,?,1)', parameters: ['raw', 'a', JSON.stringify(object), JSON.stringify(frames), original.length] }); });
        async function row(scope, view) { return (await view.query({ sql: 'SELECT * FROM archive_frames WHERE id=? AND actor=?', parameters: ['raw', scope.actorId] }, { maxRows: 1, maxBytes: 8192 }))[0]; }
        const mount = { id: 'archive/zstd-v1', prefix: '/world/compressed', visible: async (path, scope, view) => !!await row(scope, view), stat: async (path, scope, view) => { const value = await row(scope, view); return { path, objectId: JSON.parse(value.artifact).id, kind: 'file', rawBytes: String(value.raw_bytes), versions: [{ owner: 'archive', version: String(value.version) }] }; }, list: async () => { throw Error('not a directory'); }, collect: async () => { throw Error('not a collection'); }, open: async (node, scope, view) => {
                const data = await row(scope, view), object = JSON.parse(data.artifact), frames = JSON.parse(data.frames), pin = await ctx.artifacts.localFile(object);
                return { value: { node, read: async function* (range, signal) {
                            assert.ok(range.length <= range.maxBytes);
                            const end = Number(range.offset) + range.length;
                            for (const frame of frames) {
                                if (frame.rawOffset + frame.rawBytes <= Number(range.offset) || frame.rawOffset >= end)
                                    continue;
                                const encoded = [];
                                for await (const chunk of ctx.artifacts.read(object, { offset: String(frame.offset), length: frame.length }, { signal }))
                                    encoded.push(chunk);
                                const raw = zstdDecompressSync(Buffer.concat(encoded), { maxOutputLength: 65536 });
                                yield raw.subarray(Math.max(0, Number(range.offset) - frame.rawOffset), Math.min(raw.length, end - frame.rawOffset));
                            }
                        } }, release: () => pin.release() };
            } };
        ctx.exploration.register(mount);
        session = await ctx.exploration.acquire(scope, env.context('compressed-read'));
        const node = await session.value.stat('/world/compressed');
        const found = await session.value.grep({ root: node.path, pattern: '跨块中文', mode: 'literal', page });
        assert.equal(found.items[0].rawOffset, '65535');
        assert.equal(found.items[0].text, '跨块中文');
        const tail = await session.value.grep({ root: node.path, pattern: '尾部', mode: 'regex', page });
        assert.equal(tail.total, '1');
        await assert.rejects(session.value.grep({ root: node.path, pattern: 'x+', mode: 'regex', page: { maxItems: 1, maxResponseBytes: 256 } }), /PAGE_RESPONSE_BUDGET/);
        await ctx.database.transaction(env.reservation.lease, request('archive-version'), tx => tx.execute({ sql: 'UPDATE archive_frames SET version=version+1' }));
        await assert.rejects(session.value.open(node), /EXPLORATION_STALE/);
    }
    finally {
        await session?.release();
        await env.close();
    }
});
