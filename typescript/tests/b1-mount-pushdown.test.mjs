import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, scope } from './pr07-support.mjs';

// B1：挂载实现可选 find 时，会话直接下推，不再 walk（不调用 visible/stat/list）。
test('B1 挂载 find 下推：会话不遍历树', async () => {
    const env = await setup();
    let session;
    const counter = { list: 0, stat: 0, visible: 0, find: 0 };
    try {
        const nodes = ['a.jsonl', 'b.jsonl'].map(name => ({
            path: '/probe/' + name, objectId: name, kind: 'file', versions: [{ owner: 'probe', version: '1' }], rawBytes: '1'
        }));
        env.ctx.exploration.register({
            id: 'probe/pushdown', prefix: '/probe',
            visible: async () => { counter.visible++; return true; },
            stat: async path => { counter.stat++; return path === '/probe'
                ? { path: '/probe', objectId: 'probe-root', kind: 'directory', versions: [{ owner: 'probe', version: '1' }] }
                : nodes.find(node => node.path === path); },
            list: async (path, _scope, view) => { counter.list++; const items = path === '/probe' ? nodes : []; return { items, total: String(items.length), revision: view.revision, responseBytes: 0 }; },
            find: async (root, glob, _scope, view, request) => {
                counter.find++;
                const matched = nodes.filter(node => node.path.startsWith(root + '/'));
                return { items: matched, total: String(matched.length), revision: view.revision, responseBytes: 0 };
            },
            open: async () => { throw Error('UNUSED'); },
            collect: async () => { throw Error('UNUSED'); }
        });
        session = await env.ctx.exploration.acquire(scope, env.context('b1-pushdown'));
        const result = await session.value.find({ root: '/probe', glob: '*.jsonl', page: { maxItems: 8, maxResponseBytes: 16384 } });
        assert.equal(result.items.length, 2);
        assert.equal(counter.find, 1);
        assert.equal(counter.list, 0, '下推后不应 walk');
        assert.equal(counter.stat, 0, '下推后不应逐节点 stat');
        assert.equal(counter.visible, 0, '下推后不应逐节点 visible');
    }
    finally {
        try { await session?.release(); } catch {}
        await env.close();
    }
});
