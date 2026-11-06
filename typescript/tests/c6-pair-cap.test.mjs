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

async function environment(policy) {
    const { host, ctx } = await setup();
    await host.ctx.plugin(CompositionStrategies);
    await host.ctx.plugin(strategies);
    await host.ctx.plugin(Slots);
    await host.ctx.plugin(DriverRegistry);
    await host.ctx.plugin(ActivationMailbox);
    await host.ctx.plugin(ActivationService, { capacity: 4, maxActivations: 512 });
    await host.ctx.plugin(ActivationPolicyDefault, policy);
    const calls = [];
    await host.ctx.plugin({ inject: ['drivers'], apply(c) {
        c.drivers.register('rule', { async activate(intent) { calls.push(intent.actorId); return { status: 'completed', value: null, evidence: {} }; } });
    } });
    let owner;
    await host.ctx.plugin({ inject: ['activation', 'database', 'executionFence', 'wakePolicy'], apply(c) { owner = c; } });
    const reservation = await ctx.executionFence.reserve({ id: 'c6-pairs' });
    await ctx.database.transaction(reservation.lease, { ...request('c6-pair-seed'), maxChangedRows: 100 }, async (tx) => {
        for (const id of ['a', 'b'])
            await ctx.actors.create(tx, { id, attributes: {} });
    });
    const execution = {
        ctx: owner, runId: 'c6', generation: 'g', operationId: 'c6-pair-proposal', signal: reservation.signal,
        database: ctx.database, lease: reservation.lease, logicalTime: '1', selections: []
    };
    return { host, ctx: owner, calls, execution, reservation };
}

const dm = (from, to, index) => ({
    kind: 'dm', actor: to, moment: 'tick-1', source: 'test/dm', from, driver: 'rule', input: { index }, id: 'dm/' + index
});

test('C6 每对主体每 tick 的轮次上限把超出部分降级为 notify', async () => {
    const env = await environment({ maxWakesPerTick: 100, maxTurnsPerPair: 2 });
    const { ctx, calls, execution } = env;
    try {
        const signals = Array.from({ length: 200 }, (_, index) => dm(index % 2 ? 'b' : 'a', index % 2 ? 'a' : 'b', index));
        const decisions = await ctx.activation.wake(signals, execution);
        assert.equal(decisions.filter(item => item.decision === 'wake').length, 2);
        assert.equal(decisions.filter(item => item.decision === 'notify').length, 198);
        assert.ok(ctx.wakePolicy.metrics.degraded >= 198);
        await ctx.activation.drain(execution);
        assert.ok(calls.length <= 2, JSON.stringify(calls));
        assert.equal(ctx.activation.inboxCount('tick-1'), 198);
    }
    finally {
        await env.reservation.release();
        await env.host.close();
    }
});

test('C6 每主体每 tick 的唤醒上限按降级计数生效', async () => {
    const env = await environment({ maxWakesPerTick: 3, maxTurnsPerPair: 100 });
    const { ctx, execution } = env;
    try {
        const signals = Array.from({ length: 10 }, (_, index) => dm('b', 'a', index));
        const decisions = await ctx.activation.wake(signals, execution);
        assert.equal(decisions.filter(item => item.decision === 'wake').length, 3);
        assert.equal(decisions.filter(item => item.decision === 'notify').length, 7);
        assert.ok(ctx.wakePolicy.metrics.degraded >= 7);
        assert.equal(ctx.activation.inboxCount('tick-1'), 7);
    }
    finally {
        await env.reservation.release();
        await env.host.close();
    }
});
