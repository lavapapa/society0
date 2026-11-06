import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, request } from './pr06-foundations.mjs';
import { CompositionStrategies } from '../.build/host/dist/composition-strategies/index.js';
import strategies from '../.build/host/dist/slot-strategies/index.js';
import { Slots } from '../.build/host/dist/slots/index.js';
import { DriverRegistry } from '../.build/core/dist/drivers/index.js';
import { ActivationPolicy as ActivationMailbox } from '../.build/core/dist/activation-mailbox/index.js';
import { ActivationService } from '../.build/core/dist/activation/index.js';
import ActivationPolicyDefault from '../.build/core/dist/activation-policy-default/index.js';

async function environment(policy = {}) {
    const { host, ctx } = await setup();
    await host.ctx.plugin(CompositionStrategies);
    await host.ctx.plugin(strategies);
    await host.ctx.plugin(Slots);
    await host.ctx.plugin(DriverRegistry);
    await host.ctx.plugin(ActivationMailbox);
    await host.ctx.plugin(ActivationService, { capacity: 8, maxActivations: 2048 });
    await host.ctx.plugin(ActivationPolicyDefault, policy);
    const calls = [];
    await host.ctx.plugin({ inject: ['drivers'], apply(c) {
        c.drivers.register('rule', { async activate(intent) { calls.push(intent.actorId); return { status: 'completed', value: null, evidence: {} }; } });
    } });
    let owner;
    await host.ctx.plugin({ inject: ['activation', 'database', 'executionFence', 'wakePolicy'], apply(c) { owner = c; } });
    const reservation = await ctx.executionFence.reserve({ id: 'c6' });
    await ctx.database.transaction(reservation.lease, { ...request('c6-seed'), maxChangedRows: 10000 }, async (tx) => {
        for (const id of ['a', 'b'])
            await ctx.actors.create(tx, { id, attributes: {} });
    });
    const execution = {
        ctx: owner, runId: 'c6', generation: 'g', operationId: 'c6-proposal', signal: reservation.signal,
        database: ctx.database, lease: reservation.lease, logicalTime: '1', selections: []
    };
    return { host, ctx: owner, calls, execution, reservation };
}

const signal = (kind, actor, from, index) => ({
    kind, actor, moment: 'tick-1', source: 'test/' + kind, from, driver: 'rule', input: { index }, id: `${kind}/${index}`
});

test('C6 broadcast 只写收件箱，私信唤醒一次', async () => {
    const env = await environment();
    const { ctx, calls, execution } = env;
    try {
        const broadcasts = Array.from({ length: 1000 }, (_, index) => signal('broadcast', 'r' + index, 'a', index));
        const decisions = await ctx.activation.wake(broadcasts, execution);
        assert.equal(decisions.filter(item => item.decision === 'notify').length, 1000);
        assert.equal(ctx.activation.inboxCount('tick-1'), 1000);
        await ctx.activation.drain(execution);
        assert.deepEqual(calls, []);

        const dm = await ctx.activation.wake([signal('dm', 'b', 'a', 1001)], execution);
        assert.deepEqual(dm.map(item => item.decision), ['wake']);
        await ctx.activation.drain(execution);
        assert.deepEqual(calls, ['b']);
        assert.equal(ctx.activation.inboxCount('tick-1'), 1000);

        const receipt = await ctx.activation.wake([signal('receipt', 'a', 'b', 1002)], execution);
        assert.deepEqual(receipt.map(item => item.decision), ['notify']);
        const consent = await ctx.activation.wake([signal('receipt', 'a', 'b', 1003), { ...signal('receipt', 'a', 'b', 1004), requiresResponse: true }], execution);
        assert.deepEqual(consent.map(item => item.decision), ['notify', 'wake']);
    }
    finally {
        await env.reservation.release();
        await env.host.close();
    }
});
