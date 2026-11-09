import test from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './pr07-support.mjs';

const maxResponseBytes = 20000;
const reserve = 1200;
const itemText = 'x'.repeat(400);

function source(id, total, seen) {
    return {
        id,
        schema: {},
        cursorScope: 'thread',
        available: async () => true,
        versions: async () => [{ owner: 'd11', version: '1' }],
        collect: async (_scope, after, _through, _reader, bounds) => {
            seen[id] = bounds.maxResponseBytes;
            let cursor = typeof after === 'number' ? after : 0;
            const items = [];
            const build = () => ({ items, nextCursor: cursor, through: cursor, hasMore: cursor < total });
            while (items.length < bounds.maxItems && cursor < total) {
                items.push({ id: `${id}-${cursor}`, kind: 'notice', material: { kind: 'inline', value: { source: id, text: itemText } }, provenance: { provider: 'd11' } });
                cursor++;
                if (Buffer.byteLength(JSON.stringify(build())) > bounds.maxResponseBytes - reserve) {
                    items.pop();
                    cursor--;
                    break;
                }
            }
            return build();
        }
    };
}

test('D11 perception bytes are shared equally so a backlogged prefix cannot starve the last source', async () => {
    const env = await setup();
    const seen = {};
    let lease;
    try {
        const ids = ['bulk-0', 'bulk-1', 'bulk-2', 'bulk-3', 'social-recommendations'];
        for (const id of ids) env.ctx.perceptions.register(source(id, id === 'social-recommendations' ? 1 : 1000, seen));
        lease = await env.ctx.perceptions.acquire(ids, env.context('d11-share'));
        const batch = await lease.value.collect({ actorId: 'a', momentId: 'm', threadId: 'd11-thread', reason: {} }, { maxItems: 20, maxResponseBytes }, env.context('d11-collect'));
        assert.deepEqual(batch.reads.map(read => read.sourceId), ids);
        for (const read of batch.reads.slice(0, 4)) {
            assert.ok(read.page.items.length > 0, JSON.stringify(read));
            assert.equal(read.page.hasMore, true, JSON.stringify(read));
        }
        const last = batch.reads.at(-1);
        assert.equal(last.page.items.length, 1, JSON.stringify(last));
        assert.ok(seen['social-recommendations'] >= Math.floor(maxResponseBytes / 5), JSON.stringify(seen));
        assert.ok(Buffer.byteLength(JSON.stringify(batch)) <= maxResponseBytes, JSON.stringify({ bytes: Buffer.byteLength(JSON.stringify(batch)) }));
    } finally {
        await lease?.release();
        await env.close();
    }
});
