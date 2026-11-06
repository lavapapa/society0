import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, activation, request } from './pr10-support.mjs';
import { DriverRegistry } from '../.build/core/dist/drivers/index.js';
import { ActivationPolicy as ActivationMailbox } from '../.build/core/dist/activation-mailbox/index.js';
import { ActivationService } from '../.build/core/dist/activation/index.js';
import ActivationPolicyDefault from '../.build/core/dist/activation-policy-default/index.js';

const actors = ['a', 'b', 'c', 'd'].map(id => ({ id, attributes: {} }));

test('C6 social 私信发 dm 唤醒、组广播只进收件箱', async () => {
    const f = await fixture({ actors, edges: [], social: { pairingGroups: [['a', 'b', 'c', 'd']], wakeDriver: 'rule' } });
    try {
        const c = f.ctx;
        await c.plugin(DriverRegistry);
        await c.plugin(ActivationMailbox);
        await c.plugin(ActivationService, { capacity: 4, maxActivations: 64 });
        await c.plugin(ActivationPolicyDefault, { maxWakesPerTick: 10, maxTurnsPerPair: 10 });
        const calls = [];
        await c.plugin({ inject: ['drivers'], apply(ctx) {
            ctx.drivers.register('rule', { async activate(intent) { calls.push(intent.actorId); return { status: 'completed', value: null, evidence: {} }; } });
        } });
        const r = await c.executionFence.reserve({ id: 'c6-social' });
        try {
            await c.database.transaction(r.lease, request('round'), tx => c.social.startRound(tx, 1));
            const execution = { ctx: c, runId: 'social', generation: 'g', operationId: 'c6-social-proposal', signal: r.signal, database: c.database, lease: r.lease, logicalTime: '1', selections: [] };

            const direct = await c.actions.execute('social.sendPartnerMessage', { content: '伙伴私信' }, activation(f, r, 'c6-dm', 'a'));
            assert.equal(direct.status, 'completed');
            await c.activation.drain(execution);
            assert.deepEqual(calls, ['d']);

            calls.length = 0;
            const broadcast = await c.actions.execute('social.broadcastToGroup', { content: '组广播' }, activation(f, r, 'c6-broadcast', 'a'));
            assert.equal(broadcast.status, 'completed');
            await c.activation.drain(execution);
            assert.deepEqual(calls, []);
            assert.equal(c.activation.inboxCount('moment'), 3);
        }
        finally {
            await r.release();
        }
    }
    finally {
        await f.host.close();
    }
});
