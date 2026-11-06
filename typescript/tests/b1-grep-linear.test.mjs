import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, scope } from './pr07-support.mjs';

function filesMount(files, counter) {
    const root = '/probe';
    const nodes = Array.from({ length: files }, (_, index) => ({
        path: `/probe/f${String(index).padStart(5, '0')}.jsonl`, objectId: 'file-' + index, kind: 'file',
        versions: [{ owner: 'probe', version: '1' }], rawBytes: '27'
    }));
    const body = Buffer.from('first line\nneedle in the middle\nlast line\n');
    const dir = { path: root, objectId: 'probe-root', kind: 'directory', versions: [{ owner: 'probe', version: '1' }] };
    return {
        id: 'probe/files', prefix: root,
        visible: async path => path === root || path.startsWith(root + '/'),
        stat: async path => path === root ? dir : nodes.find(node => node.path === path) ?? (() => { throw Error('PROBE_MISSING'); })(),
        list: async (path, _scope, view, request) => {
            counter.list++;
            const id = { probe: 'list', revision: view.revision, path };
            const last = request.cursor === undefined ? '' : JSON.parse(Buffer.from(request.cursor, 'base64url').toString()).after;
            const remaining = nodes.filter(node => node.path > String(last));
            const items = remaining.slice(0, request.maxItems);
            const hasMore = remaining.length > items.length;
            return {
                items, total: String(nodes.length), revision: view.revision,
                ...(hasMore ? { next: Buffer.from(JSON.stringify({ identity: id, after: items[items.length - 1].path })).toString('base64url') } : {}),
                responseBytes: 0
            };
        },
        open: async node => ({ value: { node, read: async function* (range, signal) {
            signal.throwIfAborted();
            const start = Number(range.offset);
            yield body.subarray(start, Math.min(body.length, start + range.length));
        } }, release: async () => {} }),
        collect: async () => { throw Error('UNUSED'); }
    };
}

// B1：grep 用一次遍历取文件，list 次数与文件数线性相关；基线会为每一页重新 find，约 N²/32。
test('B1 grep 线性：2000 个文件时 list 调用 ≤ 文件数/64 + 目录数', async () => {
    const env = await setup();
    let session;
    const counter = { list: 0 };
    try {
        env.ctx.exploration.register(filesMount(2000, counter));
        session = await env.ctx.exploration.acquire(scope, env.context('b1-grep-linear'));
        const result = await session.value.grep({
            root: '/probe', pattern: 'needle', mode: 'literal',
            page: { maxItems: 4000, maxResponseBytes: 16777216 }
        });
        assert.equal(result.total, '2000');
        const limit = Math.ceil(2000 / 64) + 1;
        assert.ok(counter.list <= limit, `list 调用 ${counter.list} 超过线性上限 ${limit}`);
    }
    finally {
        try { await session?.release(); } catch {}
        await env.close();
    }
});
