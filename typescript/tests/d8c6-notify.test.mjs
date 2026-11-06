import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './pr07-support.mjs';
import { DriverRegistry } from '../.build/core/dist/drivers/index.js';
import { ActivationPolicy as ActivationMailbox } from '../.build/core/dist/activation-mailbox/index.js';
import { ActivationService, wakePolicySlot } from '../.build/core/dist/activation/index.js';
import ActivationPolicyDefault from '../.build/core/dist/activation-policy-default/index.js';

function policyPlugin(decide, records) {
    return {
        inject: ['slots'],
        apply(ctx) {
            ctx.slots.contribute(wakePolicySlot, {
                id: 'd8c6-policy',
                value: {
                    decide(signal, ledger) {
                        records?.push(structuredClone(signal));
                        return decide(signal, ledger);
                    },
                },
            });
        },
    };
}

async function environment({ policy, config } = {}) {
    const env = await setup();
    const { host } = env;
    try {
        await host.ctx.plugin(DriverRegistry);
        await host.ctx.plugin(ActivationMailbox);
        await host.ctx.plugin(ActivationService, { capacity: 4, maxActivations: 64 });
        if (policy)
            await host.ctx.plugin(policy);
        else
            await host.ctx.plugin(ActivationPolicyDefault, config ?? { maxWakesPerTick: 100, maxTurnsPerPair: 100 });
        const calls = [];
        await host.ctx.plugin({
            inject: ['drivers'],
            apply(ctx) {
                ctx.drivers.register('llm/v1', {
                    async activate(intent) {
                        calls.push(intent.actorId);
                        return { status: 'completed', value: null, evidence: {} };
                    },
                });
            },
        });
        let ctx;
        await host.ctx.plugin({
            inject: ['database', 'executionFence', 'actions', 'activation'],
            apply(c) {
                ctx = c;
            },
        });
        ctx.actions.register({
            name: 'd8c6.send',
            inputSchema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] },
            outputSchema: {},
            effect: 'read',
            available: async () => true,
            async execute(input, context) {
                return {
                    operationId: context.operationId,
                    status: 'completed',
                    value: { sent: true },
                    original: null,
                    artifacts: [],
                    notify: [{ actorId: 'b', reason: 'message', ref: { messageId: 'm1', text: input.text } }],
                };
            },
        });
        const execution = {
            ctx,
            runId: 'pr07',
            generation: 'g',
            operationId: 'd8c6-drain',
            signal: env.reservation.signal,
            database: env.ctx.database,
            lease: env.reservation.lease,
            logicalTime: 1,
            selections: [],
        };
        return {
            env,
            ctx,
            calls,
            execution,
            send: (text, operationId = 'd8c6-send') => ctx.actions.execute('d8c6.send', { text }, env.context(operationId, 'a', 'm')),
            close: () => env.close(),
        };
    }
    catch (error) {
        await env.close();
        throw error;
    }
}

test('D8 notify 转成 dm 唤醒信号，策略 wake 且接纳时回执 scheduled', async () => {
    const records = [];
    const run = await environment({ policy: policyPlugin(() => 'wake', records) });
    try {
        const receipt = await run.send('hi', 'd8c6-wake');
        assert.equal(receipt.status, 'completed');
        assert.equal(receipt.delivery.wake, 'scheduled');
        assert.deepEqual(receipt.delivery.recipients, [{ actorId: 'b', visibleRevision: '1' }]);
        assert.equal(records.length, 1);
        assert.deepEqual(records[0], {
            kind: 'dm',
            actor: 'b',
            moment: 'm',
            source: 'action',
            id: 'd8c6-wake/notify/0',
            input: { messageId: 'm1', text: 'hi' },
            from: 'a',
        });
        await run.ctx.activation.drain(run.execution);
        assert.deepEqual(run.calls, ['b']);
    }
    finally {
        await run.close();
    }
});

test('D8 notify 在策略 notify 时进收件箱，回执 deferred', async () => {
    const run = await environment({ policy: policyPlugin(() => 'notify') });
    try {
        const receipt = await run.send('hi', 'd8c6-notify');
        assert.equal(receipt.delivery.wake, 'deferred');
        await run.ctx.activation.drain(run.execution);
        assert.deepEqual(run.calls, []);
        assert.equal(run.ctx.activation.inbox('b', 'm').length, 1);
        assert.equal(run.ctx.activation.inboxCount('m'), 1);
    }
    finally {
        await run.close();
    }
});

test('D8 notify 在策略 drop 时回执 none，不收件', async () => {
    const run = await environment({ policy: policyPlugin(() => 'drop') });
    try {
        const receipt = await run.send('hi', 'd8c6-drop');
        assert.equal(receipt.delivery.wake, 'none');
        await run.ctx.activation.drain(run.execution);
        assert.deepEqual(run.calls, []);
        assert.equal(run.ctx.activation.inboxCount('m'), 0);
    }
    finally {
        await run.close();
    }
});

test('D8 notify 受每对主体轮次上限约束，超出部分降级为 deferred', async () => {
    const run = await environment({ config: { maxWakesPerTick: 100, maxTurnsPerPair: 1 } });
    try {
        const first = await run.send('one', 'd8c6-pair-1');
        assert.equal(first.delivery.wake, 'scheduled');
        const second = await run.send('two', 'd8c6-pair-2');
        assert.equal(second.delivery.wake, 'deferred');
        await run.ctx.activation.drain(run.execution);
        assert.deepEqual(run.calls, ['b']);
        assert.equal(run.ctx.activation.inboxCount('m'), 1);
    }
    finally {
        await run.close();
    }
});
