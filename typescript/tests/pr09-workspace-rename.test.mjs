import { test } from 'node:test';
import assert from 'node:assert/strict';
import { posix } from 'node:path';
import { setup } from './pr09-support.mjs';
import Workspace, { workspaceMigration } from '../.build/extras/dist/workspace/index.js';

test('RV3-14-T01 rename bumps source and target parent versions', async () => {
    const f = await setup([workspaceMigration({ consumer: 'g13-workspace-rename' })]);
    const scope = { actorId: 'a', momentId: 'm', query: null };
    let reservation;
    try {
        await f.ctx.plugin(Workspace);
        await f.write('g13-workspace-init', tx => f.ctx.workspace.initialize(tx, 'a'));
        reservation = await f.ctx.executionFence.reserve({ id: 'g13-workspace-rename' });
        const context = operationId => ({
            ctx: f.ctx, runId: f.runId, generation: 'g', operationId,
            signal: reservation.signal, actorId: 'a', momentId: 'm', logicalTime: 0,
            database: f.ctx.database, writeLease: reservation.lease
        });
        const read = path => f.read(view => f.ctx.workspace.stat(path, scope, view));
        const version = async path => Number((await read(path)).versions[0].version);
        const visible = path => f.read(view => f.ctx.workspace.visible(path, scope, view));
        f.ctx.actions.register({
            name: 'g13.workspace.write', effect: 'world', inputSchema: {}, outputSchema: {}, available: async () => true,
            async execute(input, c) {
                async function* bytes() { yield Buffer.from(input.text); }
                const node = await f.ctx.workspace.writeFile(input.path, bytes(), null, scope, c);
                return { operationId: c.operationId, status: 'completed', value: node, original: input, artifacts: [] };
            }
        });
        f.ctx.actions.register({
            name: 'g13.workspace.mutate', effect: 'world', inputSchema: {}, outputSchema: {}, available: async () => true,
            async execute(input, c) {
                await f.ctx.workspace.mutate(input.mutation, scope, c);
                return { operationId: c.operationId, status: 'completed', value: null, original: input, artifacts: [] };
            }
        });
        const mkdir = async path => {
            const parent = await read(posix.dirname(path));
            await f.ctx.actions.execute('g13.workspace.mutate', {
                mutation: { kind: 'mkdir', path, expected: null, expectedParent: parent }
            }, context('mkdir-' + path));
            return read(path);
        };
        const write = async (path, text) => {
            await f.ctx.actions.execute('g13.workspace.write', { path, text }, context('write-' + path));
            return read(path);
        };
        const rename = async (from, to) => {
            const expected = await read(from);
            const expectedParent = await read(posix.dirname(to));
            await f.ctx.actions.execute('g13.workspace.mutate', {
                mutation: { kind: 'rename', from, to, expected, targetExpected: null, expectedParent }
            }, context('rename-' + from + '-' + to));
            return expected;
        };

        await mkdir('/workspace/a');
        await mkdir('/workspace/b');
        await mkdir('/workspace/b/d');
        const x0 = await write('/workspace/a/x', 'alpha');
        const f0 = await write('/workspace/b/d/f', 'child');
        const w0 = await version('/workspace');
        const a0 = await version('/workspace/a');
        const b0 = await version('/workspace/b');

        await rename('/workspace/a/x', '/workspace/b/x');
        assert.equal(await version('/workspace/a'), a0 + 1);
        assert.equal(await version('/workspace/b'), b0 + 1);
        assert.equal(await version('/workspace'), w0);
        const moved = await read('/workspace/b/x');
        assert.equal(moved.objectId, x0.objectId);
        assert.equal(await visible('/workspace/a/x'), false);

        const b1 = await version('/workspace/b');
        const a1 = await version('/workspace/a');
        await rename('/workspace/b/x', '/workspace/b/y');
        assert.equal(await version('/workspace/b'), b1 + 1);
        assert.equal(await version('/workspace/a'), a1);
        assert.equal(await visible('/workspace/b/x'), false);
        assert.equal((await read('/workspace/b/y')).objectId, x0.objectId);

        const a2 = await version('/workspace/a');
        const b2 = await version('/workspace/b');
        await rename('/workspace/b/d', '/workspace/a/d');
        assert.equal(await version('/workspace/a'), a2 + 1);
        assert.equal(await version('/workspace/b'), b2 + 1);
        assert.equal(await version('/workspace'), w0);
        assert.equal(await visible('/workspace/b/d'), false);
        const child = await read('/workspace/a/d/f');
        assert.equal(child.objectId, f0.objectId);
        const page = await f.read(view => f.ctx.workspace.list('/workspace/a/d', scope, view, { maxItems: 10, maxResponseBytes: 65536 }));
        assert.deepEqual(page.items.map(item => item.path), ['/workspace/a/d/f']);
    }
    finally {
        await reservation?.release();
        await f.close();
    }
});
