import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join, dirname, basename } from 'node:path';
import { fixture, activation, request } from './pr10-support.mjs';

const actors = ['a', 'b', 'c', 'd'].map(id => ({ id, attributes: { id } }));
const pages = { maxItems: 10, maxResponseBytes: 65536 };

async function exchange(f, reservation, actor, name, input, operationId) {
    return f.ctx.actions.execute('social.' + name, input, activation(f, reservation, operationId, actor));
}

test('Social B02 配对与消息在独立完整点恢复后保持当前轮、收件与历史', async () => {
    const f = await fixture({ actors, edges: [], social: { pairingGroups: [['a', 'b', 'c', 'd']], wakeDriver: 'rule' } });
    f.ctx.history.bindQuiescence(() => f.ctx.tasks.drain());
    let branch, reservation;
    try {
        const c = f.ctx;
        reservation = await c.executionFence.reserve({ id: 'b02-recovery-seed' });
        await c.database.transaction(reservation.lease, request('b02-round'), tx => c.social.startRound(tx, 1));
        const direct = await exchange(f, reservation, 'a', 'sendPartnerMessage', { content: '恢复前伙伴原文' }, 'b02-direct');
        const broadcast = await exchange(f, reservation, 'a', 'broadcastToGroup', { content: '恢复前组广播' }, 'b02-broadcast');
        assert.equal(direct.status, 'completed');
        assert.equal(broadcast.status, 'completed');
        const operations = [...(direct.requiredOperations ?? []), ...(broadcast.requiredOperations ?? [])];
        await reservation.release();
        reservation = undefined;

        const lease = await c.executionFence.seal();
        const point = await c.history.publish(lease, {
            operationId: 'publish-b02-recovery', id: 'b02-recovery', parentId: f.root.id,
            expectedRevision: await c.database.readRevision(), requiredOperations: operations
        });
        if (c.executionFence.snapshot().state === 'sealed')
            c.executionFence.resume(lease);

        const target = join(dirname(f.directory), basename(f.directory) + '-b02-recovery');
        await c.history.fork({ operationId: 'fork-b02-recovery', pointId: point.id, targetRunId: 'b02-branch', targetDirectory: target });
        branch = await fixture({
            restore: true, directory: target, runId: 'b02-branch', attemptDirectory: f.attemptDirectory,
            actors, edges: [], social: { pairingGroups: [['a', 'b', 'c', 'd']] }
        });
        let view = await branch.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            const pairing = await branch.ctx.social.pairing(view, 'a');
            assert.equal(pairing.currentPartner, 'd');
            assert.equal(pairing.currentRound, 1);
            assert.equal(pairing.canConverse, true);
            assert.deepEqual(pairing.partnerHistory, ['d']);
            const inbox = await branch.ctx.social.conversation(view, 'd', pages);
            assert.equal(inbox.total, '2');
            assert.deepEqual(inbox.items.map(item => item.content), ['恢复前伙伴原文', '恢复前组广播']);
            const history = await branch.ctx.social.conversation(view, 'd', pages, true);
            assert.equal(history.total, '2');
            assert.equal((await branch.ctx.social.conversation(view, 'a', pages)).total, '0');
        }
        finally {
            await view.close();
        }

        const next = await branch.ctx.executionFence.reserve({ id: 'b02-after-restore' });
        try {
            const reply = await exchange(branch, next, 'd', 'sendPartnerMessage', { content: '恢复后回复' }, 'b02-reply');
            assert.equal(reply.status, 'completed');
        }
        finally {
            await next.release();
        }
        view = await branch.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            assert.equal((await branch.ctx.social.conversation(view, 'a', pages)).total, '1');
            assert.equal((await branch.ctx.social.conversation(view, 'a', pages, true)).total, '1');
        }
        finally {
            await view.close();
        }
    }
    finally {
        await reservation?.release();
        await branch?.host.close();
        await f.host.close();
    }
});
