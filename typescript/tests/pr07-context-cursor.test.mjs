import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ContextMount } from '../.build/core/dist/mount-context/index.js';
import { setup } from './pr07-support.mjs';

test('NEW-1-T02 /context pages are complete, unique and code-point ordered', async () => {
    const mount = Object.create(ContextMount.prototype);
    Object.assign(mount, {
        files: new Map(), held: new Set(), bytes: 0, version: 0, closing: false,
        config: { maxBytes: 1048576, maxFiles: 128 }, id: 'frozen-context', prefix: '/context'
    });
    const scope = { actorId: 'A', momentId: 'M', logicalTime: 1, query: null };
    const reader = { runId: 'r', revision: '1' };
    for (const operationId of ['a', 'B', 'c', '_x', '9'])
        mount.freeze(['text'], { operationId, actorId: 'A', momentId: 'M' });
    const expected = ['/context/9-0.txt', '/context/B-0.txt', '/context/_x-0.txt', '/context/a-0.txt', '/context/c-0.txt'];
    for (const maxItems of [1, 2, 3, 5]) {
        const all = [];
        let cursor;
        for (let guard = 0; guard < 10; guard++) {
            const page = await mount.list('/context', scope, reader, { maxItems, maxResponseBytes: 65536, ...(cursor ? { cursor } : {}) });
            all.push(...page.items.map(item => item.path));
            if (!page.next)
                break;
            cursor = page.next;
        }
        assert.deepEqual(all, expected, `maxItems=${maxItems}`);
        assert.equal(new Set(all).size, 5, `maxItems=${maxItems} unique`);
    }
    const first = await mount.list('/context', scope, reader, { maxItems: 5, maxResponseBytes: 65536 });
    assert.equal(first.total, '5');
    assert.deepEqual(first.items.map(item => item.path), expected);
    const budget = Object.create(ContextMount.prototype);
    Object.assign(budget, { files: new Map(), held: new Set(), bytes: 0, version: 0, closing: false, config: { maxBytes: 1048576, maxFiles: 1 }, id: 'budget', prefix: '/context' });
    assert.throws(() => budget.freeze(['a', 'b'], { operationId: 'budget', actorId: 'A', momentId: 'M' }), /CONTEXT_MOUNT_BUDGET/);
    const duplicate = Object.create(ContextMount.prototype);
    Object.assign(duplicate, { files: new Map(), held: new Set(), bytes: 0, version: 0, closing: false, config: { maxBytes: 1048576, maxFiles: 2 }, id: 'duplicate', prefix: '/context' });
    duplicate.freeze(['one'], { operationId: 'duplicate', actorId: 'A', momentId: 'M' });
    assert.throws(() => duplicate.freeze(['two'], { operationId: 'duplicate', actorId: 'A', momentId: 'M' }), /CONTEXT_MOUNT_EXISTS/);
    const closed = Object.create(ContextMount.prototype);
    Object.assign(closed, { files: new Map(), held: new Set(), bytes: 0, version: 0, closing: true, config: { maxBytes: 1048576, maxFiles: 2 }, id: 'closed', prefix: '/context' });
    assert.throws(() => closed.freeze(['one'], { operationId: 'closed', actorId: 'A', momentId: 'M' }), /CONTEXT_MOUNT_CLOSED/);
    const bytesBefore = mount.bytes;
    const lease = mount.freeze(['released'], { operationId: 'release', actorId: 'A', momentId: 'M' });
    assert.equal(mount.files.has('/context/release-0.txt'), true);
    await lease.release();
    assert.equal(mount.files.has('/context/release-0.txt'), false);
    assert.equal(mount.bytes, bytesBefore);
});

test('G12 native ContextMount open/stat/referencedFile behavior', async () => {
    const env = await setup();
    const { ctx } = env;
    const scope = { actorId: 'a', momentId: 'm', logicalTime: 1, query: null };
    try {
        let mount;
        await ctx.plugin(ContextMount, {});
        await ctx.plugin({ inject: ['contextMount'], apply(c) { mount = c.contextMount; } });
        const activation = env.context('context-open');
        const lease = mount.freeze(['hello'], activation);
        const path = lease.value[0];
        const node = await mount.stat(path, scope);
        assert.equal(node.path, path);
        assert.equal(node.kind, 'file');
        const handle = await mount.open(node, scope);
        const chunks = [];
        for await (const chunk of handle.value.read({ offset: '0', length: 5, maxBytes: 5 }, activation.signal))
            chunks.push(chunk);
        assert.equal(Buffer.concat(chunks).toString(), 'hello');
        await handle.release();
        await assert.rejects((async () => {
            for await (const chunk of handle.value.read({ offset: '0', length: 1, maxBytes: 1 }, activation.signal))
                void chunk;
        })(), /CONTEXT_HANDLE_RELEASED/);
        await assert.rejects(mount.open({ ...node, objectId: 'stale' }, scope), /EXPLORATION_STALE/);
        await assert.rejects(mount.stat('/context/missing.txt', scope), /EXPLORATION_NOT_VISIBLE/);
        await lease.release();
    }
    finally {
        await env.close();
    }
});
