// C5：KV 估算读取线程最近一条 assistant usage，而不是只看本次输入估算。
import test from 'node:test';
import assert from 'node:assert/strict';
import {setup, request} from './pr07-support.mjs';
import Drivers from '../.build/core/dist/drivers/index.js';
import ActivationMailbox from '../.build/core/dist/activation-mailbox/index.js';
import ActivationPolicyDefault from '../.build/core/dist/activation-policy-default/index.js';
import Activation from '../.build/core/dist/activation/index.js';

const usage = {input: 9000, output: 10, cacheRead: 0, cacheWrite: 0, totalTokens: 9010, cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0}};
const assistant = {role: 'assistant', content: [{type: 'text', text: 'old'}], usage, stopReason: 'stop', timestamp: 1};

test('C5 KV estimate blocks a thread with large previous prompt usage and admits an empty thread', async t => {
    const run = await setup();
    t.after(() => run.close());
    const ctx = run.ctx;
    await ctx.database.transaction(run.reservation.lease, request('c5-estimate'), async tx => {
        for (const id of ['slow', 'big', 'empty']) await ctx.actors.create(tx, {id, attributes: {}});
        await tx.execute({sql: 'INSERT INTO threads VALUES(?,?,?,?,?)', parameters: ['thread-big', 'big', 'm', 'thread-big', 1n]});
        await tx.execute({sql: 'INSERT INTO thread_messages VALUES(?,?,?,?,?)', parameters: ['thread-big', 1n, 'old-assistant', 'assistant', JSON.stringify({kind: 'inline', value: assistant})]});
    });
    const order = [], active = [];
    await ctx.plugin(Drivers);
    ctx.get('drivers').register('c5', {async activate(intent) {
        order.push(intent.actorId);
        active.push(intent.actorId);
        await new Promise(resolve => setTimeout(resolve, 80));
        active.splice(active.indexOf(intent.actorId), 1);
        return {status: 'completed', value: null, evidence: {}, requiredOperations: []};
    }});
    await ctx.plugin(ActivationMailbox);
    await ctx.plugin(ActivationPolicyDefault);
    await ctx.plugin(Activation, {capacity: 16, admission: {kind: 'kv', replicas: 1, tokensPerReplica: 3000, utilization: 1}});
    const context = {...run.context('c5-estimate'), lease: run.reservation.lease};
    for (const intent of [
        {id: 'c5-slow', actorId: 'slow', momentId: 'm', source: 'test', driver: 'c5', input: {index: 0}, estimatedTokens: 1500},
        {id: 'c5-big', actorId: 'big', momentId: 'm', source: 'test', driver: 'c5', input: {index: 1}, estimatedTokens: 1000},
        {id: 'c5-empty', actorId: 'empty', momentId: 'm', source: 'test', driver: 'c5', input: {index: 2}, estimatedTokens: 1000}
    ]) await ctx.get('activation').submit(intent, context);
    const results = await ctx.get('activation').drain(context);
    assert.equal(results.length, 3);
    assert.ok(results.every(result => result.status === 'completed'));
    assert.deepEqual(order, ['slow', 'empty', 'big'], JSON.stringify({order, active}));
});
