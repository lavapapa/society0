// C5 follow1 竞态：准入估算的 await 不得把“判定 → 改 batch → enqueue”拆开。
import test from 'node:test';
import assert from 'node:assert/strict';
import {setup, request} from './pr07-support.mjs';
import Drivers from '../.build/core/dist/drivers/index.js';
import ActivationMailbox from '../.build/core/dist/activation-mailbox/index.js';
import ActivationPolicyDefault from '../.build/core/dist/activation-policy-default/index.js';
import Activation from '../.build/core/dist/activation/index.js';

async function fixture({actors = ['a'], capacity = 16} = {}) {
    const run = await setup();
    await run.ctx.database.transaction(run.reservation.lease, request('c5-race'), async tx => {
        for (const id of actors.filter(id => id !== 'a' && id !== 'b')) await run.ctx.actors.create(tx, {id, attributes: {}});
    });
    await run.ctx.plugin(Drivers);
    await run.ctx.plugin(ActivationMailbox);
    await run.ctx.plugin(ActivationPolicyDefault);
    await run.ctx.plugin(Activation, {capacity, admission: {kind: 'kv', replicas: 1, tokensPerReplica: 100000, utilization: 1}});
    const activation = run.ctx.get('activation');
    const context = {...run.context('c5-race'), lease: run.reservation.lease};
    return {run, ctx: run.ctx, activation, context, close: () => run.close()};
}

test('C5 concurrent same-key admission keeps one queued item and one byte charge', async t => {
    const env = await fixture({actors: ['blocker', 'a'], capacity: 1});
    t.after(() => env.close());
    const entered = Promise.withResolvers(), release = Promise.withResolvers();
    env.ctx.get('drivers').register('race', {async activate(intent) {
        if (intent.actorId === 'blocker') { entered.resolve(); await release.promise; }
        return {status: 'completed', value: null, evidence: {}, requiredOperations: []};
    }});
    const blocker = {id: 'blocker', actorId: 'blocker', momentId: 'm', source: 'test', driver: 'race', input: {block: true}, estimatedTokens: 1};
    await env.activation.submit(blocker, env.context);
    await entered.promise;

    const original = env.ctx.database.snapshot.bind(env.ctx.database);
    env.ctx.database.snapshot = async options => { await new Promise(resolve => setTimeout(resolve, 100)); return original(options); };

    const first = {id: 'race-1', actorId: 'a', momentId: 'm', source: 'test', driver: 'race', input: {value: 1}, estimatedTokens: 10};
    const second = {id: 'race-2', actorId: 'a', momentId: 'm', source: 'test', driver: 'race', input: {value: 2}, estimatedTokens: 10};
    const batch = env.activation.batch(env.context);
    batch.admission = Promise.resolve();
    const one = env.activation.submit(first, env.context);
    batch.admission = Promise.resolve();
    const two = env.activation.submit(second, env.context);
    try {
        const [a, b] = await Promise.all([one, two]);
        assert.deepEqual([a.kind, b.kind].sort(), ['accepted', 'coalesced'], JSON.stringify([a, b]));
        assert.equal(batch.queue.length, 1, JSON.stringify(batch.queue.map(item => item.intent.id)));
        assert.equal(batch.queue[0].inputs.length, 2);
        assert.equal(batch.queue[0].bytes, Buffer.byteLength(JSON.stringify(first)) + Buffer.byteLength(JSON.stringify(second)));
        assert.equal(batch.bytes, batch.queue[0].bytes);
    }
    finally {
        release.resolve();
        await env.activation.drain(env.context).catch(() => {});
    }
});

test('C5 closing during token estimation rejects instead of enqueueing', async t => {
    const env = await fixture();
    t.after(() => env.close());
    env.ctx.get('drivers').register('race', {async activate() {
        return {status: 'completed', value: null, evidence: {}, requiredOperations: []};
    }});
    const original = env.ctx.database.snapshot.bind(env.ctx.database);
    let calls = 0, release;
    const waiting = new Promise(resolve => { release = resolve; });
    const entered = Promise.withResolvers();
    env.ctx.database.snapshot = async options => {
        calls++;
        if (calls === 2) { entered.resolve(); await waiting; }
        return original(options);
    };
    const intent = {id: 'closing-race', actorId: 'a', momentId: 'm', source: 'test', driver: 'race', input: {value: 1}, estimatedTokens: 10};
    const pending = env.activation.submit(intent, env.context);
    await entered.promise;
    const closing = env.activation.cancel(env.context, 'closed during estimate');
    release();
    const admission = await pending;
    await closing.catch(() => {});
    assert.equal(admission.kind, 'rejected', JSON.stringify(admission));
    assert.equal(admission.reason, 'ACTIVATION_CLOSED');
});
