import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, request } from './pr06-foundations.mjs';
import { CompositionStrategies } from '../.build/host/dist/composition-strategies/index.js';
import strategies from '../.build/host/dist/slot-strategies/index.js';
import { Slots } from '../.build/host/dist/slots/index.js';
import { DriverRegistry } from '../.build/core/dist/drivers/index.js';
import { ActivationPolicy } from '../.build/core/dist/activation-mailbox/index.js';
import { ActivationService } from '../.build/core/dist/activation/index.js';
const intent = (id, actorId = 'a', momentId = 'm') => ({ id, actorId, momentId, source: 'message', driver: 'rule', input: id });
test('PR06-T01/T07 actor waiting consumes no other actor slot; stable followup', async () => {
    const { host, ctx } = await setup();
    const release = Promise.withResolvers();
    const started = Promise.withResolvers();
    const other = Promise.withResolvers();
    let r;
    try {
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(strategies);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(DriverRegistry);
        await host.ctx.plugin(ActivationPolicy);
        await host.ctx.plugin(ActivationService, { capacity: 2, maxActivations: 4 });
        const calls = [];
        await host.ctx.plugin({ inject: ['drivers'], apply(c) {
                c.drivers.register('rule', { async activate(i) {
                        calls.push(i);
                        if (i.id === 'first') {
                            started.resolve();
                            await release.promise;
                        }
                        if (i.actorId === 'b')
                            other.resolve();
                        return { status: 'completed', value: i.input, evidence: null };
                    } });
            } });
        let owner;
        await host.ctx.plugin({ inject: ['activation', 'database', 'executionFence'], apply(c) { owner = c; } });
        r = await ctx.executionFence.reserve({ id: 'proposal' });
        await ctx.database.transaction(r.lease, request('seed'), async (tx) => {
            for (const id of ['a', 'b'])
                await ctx.actors.create(tx, { id, attributes: {} });
        });
        const ex = { ctx: owner, runId: 'pr06', generation: 'g', operationId: 'proposal', signal: r.signal, database: ctx.database, lease: r.lease, logicalTime: '0', selections: [] };
        assert.equal((await owner.activation.submit(intent('first'), ex)).kind, 'accepted');
        await started.promise;
        const second = intent('second');
        await owner.activation.submit(second, ex);
        second.input = 'mutated-after-submit';
        const merged = await owner.activation.submit({ ...intent('third'), source: 'agenda' }, ex);
        assert.equal(merged.kind, 'coalesced');
        merged.mergedInput.push('mutated-result');
        await owner.activation.submit(intent('other', 'b'), ex);
        await other.promise;
        assert.deepEqual(calls.map(i => i.id), ['first', 'other']);
        release.resolve();
        await owner.activation.drain(ex);
        assert.deepEqual(calls[2].input, ['second', 'third']);
        assert.deepEqual(calls[2].signals.map(x => [x.id, x.source]), [['second', 'message'], ['third', 'agenda']]);
        assert.equal((await owner.activation.submit(intent('fourth'), ex)).reason, 'ACTIVATION_FOLLOWUP_LIMIT');
        await r.release();
        r = undefined;
    }
    finally {
        release.resolve();
        await r?.release();
        await host.close();
    }
});
test('PR06-T20 submit and dequeue recheck actor participation; cancel waits physical return', async () => {
    const { host, ctx } = await setup();
    const release = Promise.withResolvers();
    const started = Promise.withResolvers();
    let r;
    try {
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(strategies);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(DriverRegistry);
        await host.ctx.plugin(ActivationPolicy);
        await host.ctx.plugin(ActivationService, { capacity: 1, maxQueuedBytes: 2048 });
        let calls = 0, finished = false;
        await host.ctx.plugin({ inject: ['drivers'], apply(c) { c.drivers.register('rule', { async activate() { calls++; started.resolve(); await release.promise; finished = true; return { status: 'completed', value: null, evidence: null }; } }); } });
        let owner;
        await host.ctx.plugin({ inject: ['activation', 'database', 'executionFence'], apply(c) { owner = c; } });
        r = await ctx.executionFence.reserve({ id: 'proposal' });
        await ctx.database.transaction(r.lease, request('seed'), async (tx) => {
            for (const id of ['a', 'b'])
                await ctx.actors.create(tx, { id, attributes: {} });
        });
        const ex = { ctx: owner, runId: 'pr06', generation: 'g', operationId: 'proposal', signal: r.signal, database: ctx.database, lease: r.lease, logicalTime: '0', selections: [] };
        await owner.activation.submit(intent('first'), ex);
        await started.promise;
        await owner.activation.submit(intent('queued', 'b'), ex);
        assert.equal((await owner.activation.submit({ ...intent('conflict', 'a'), driver: 'other' }, ex)).reason, 'ACTIVATION_IDENTITY_CONFLICT');
        assert.equal((await owner.activation.submit({ ...intent('huge', 'b'), input: 'x'.repeat(4096) }, ex)).reason, 'ACTIVATION_BYTE_LIMIT');
        await ctx.database.transaction(r.lease, request('exit'), tx => ctx.actors.setParticipation(tx, 'b', 'inactive', { reason: 'exit' }));
        assert.equal((await owner.activation.submit(intent('new', 'b'), ex)).kind, 'rejected');
        release.resolve();
        const results = await owner.activation.drain(ex);
        assert.equal(calls, 1);
        assert.equal(results[1].status, 'incomplete');
        assert.equal(results[1].evidence.kind, 'eligibility-rejected');
        assert.equal(finished, true);
        await r.release();
        r = undefined;
    }
    finally {
        release.resolve();
        await r?.release();
        await host.close();
    }
});
test('PR06-T03 cancellation waiter does not release driver selection until physical completion', async () => {
    const { host, ctx } = await setup();
    const release = Promise.withResolvers();
    const started = Promise.withResolvers();
    let r;
    try {
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(strategies);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(DriverRegistry);
        await host.ctx.plugin(ActivationPolicy);
        await host.ctx.plugin(ActivationService);
        let retired = false, drained = false;
        const provider = await host.ctx.plugin({ inject: ['drivers'], apply(c) { c.drivers.register('rule', { async activate() { started.resolve(); await release.promise; return { status: 'completed', value: null, evidence: null }; } }); } });
        let owner;
        await host.ctx.plugin({ inject: ['activation'], apply(c) { owner = c; } });
        r = await ctx.executionFence.reserve({ id: 'proposal' });
        await ctx.database.transaction(r.lease, request('seed'), tx => ctx.actors.create(tx, { id: 'a', attributes: {} }));
        const ex = { ctx: owner, runId: 'pr06', generation: 'g', operationId: 'proposal', signal: r.signal, database: ctx.database, lease: r.lease, logicalTime: '0', selections: [] };
        await owner.activation.submit(intent('first'), ex);
        await started.promise;
        const closing = provider.dispose().then(() => retired = true);
        r.cancel('cancel');
        const draining = owner.activation.drain(ex).catch(() => { }).then(() => drained = true);
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(retired, false);
        assert.equal(drained, false);
        assert.equal(host.ctx.tasks.snapshot().activeCount >= 1, true);
        release.resolve();
        await draining;
        await closing;
        assert.equal(retired, true);
        await r.release();
        r = undefined;
    }
    finally {
        release.resolve();
        await r?.release();
        await host.close();
    }
});
test('PR06-T07 drain joins admission already validating at the idle edge', async () => {
    const { host, ctx } = await setup();
    const release = Promise.withResolvers();
    const started = Promise.withResolvers();
    const validate = Promise.withResolvers();
    const validating = Promise.withResolvers();
    let r;
    try {
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(strategies);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(DriverRegistry);
        await host.ctx.plugin(ActivationPolicy);
        await host.ctx.plugin(ActivationService);
        await host.ctx.plugin({ inject: ['drivers', 'actorEligibility'], apply(c) {
                c.actorEligibility.register({ id: 'gate', async evaluate(request) {
                        if (request.intentId === 'queued') {
                            validating.resolve();
                            await validate.promise;
                        }
                        return { allowed: true, reason: 'ready', facts: null, versions: [] };
                    } });
                c.drivers.register('rule', { async activate(i) {
                        if (i.id === 'first') {
                            started.resolve();
                            await release.promise;
                        }
                        return { status: 'completed', value: i.id, evidence: null };
                    } });
            } });
        let owner;
        await host.ctx.plugin({ inject: ['activation'], apply(c) { owner = c; } });
        r = await ctx.executionFence.reserve({ id: 'proposal' });
        await ctx.database.transaction(r.lease, request('seed'), tx => ctx.actors.create(tx, { id: 'a', attributes: {} }));
        const ex = { ctx: owner, runId: 'pr06', generation: 'g', operationId: 'proposal', signal: r.signal, database: ctx.database, lease: r.lease, logicalTime: '0', selections: [] };
        await owner.activation.submit(intent('first'), ex);
        await started.promise;
        let done = false;
        const drain = owner.activation.drain(ex).then(result => { done = true; return result; });
        const submit = owner.activation.submit(intent('queued', 'a', 'next'), ex);
        await validating.promise;
        release.resolve();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(done, false);
        validate.resolve();
        await submit;
        assert.equal((await drain).length, 2);
        await r.release();
        r = undefined;
    }
    finally {
        release.resolve();
        validate.resolve();
        await r?.release();
        await host.close();
    }
});
