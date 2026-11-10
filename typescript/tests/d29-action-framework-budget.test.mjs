// cases: D29 框架语句单独计数，不占插件声明的 maxStatements；run 32 的 memory.write 输入在同 activation 内成功写入
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './pr09-support.mjs';
import Memory from '../.build/extras/dist/memory/index.js';
import { ACTION_FRAMEWORK_STATEMENTS_CEILING, actionFrameworkStatements } from '../.build/core/dist/actions/execution.js';

const threadId = f => JSON.stringify([f.runId, 'a', 'tick_1_strategy']);
const body = "Tick 1 策略执行总结：\n1. 生产：在 fac_cathode_head 启动 rc_lfp 配方，产量 200，预计 11-12 完成。\n2. 下游销售：向 cell_head (头部电芯), cell_makers (电芯环节) 提出 LFP 长期供应协议。\n3. 上游采购：向 salt_head (头部锂盐厂) 提出碳酸锂长期采购协议。\n4. 行业协调：向 cathode_makers 提出供应链稳定协调协议。\n\n目标：确立龙头地位，锁定上下游关键渠道。";

/** run 32 的 offer 形状：每个 provider 的 versions/offers 各一条查询。 */
function providers(ctx, count) {
    for (let index = 0; index < count; index++)
        ctx.actions.provide({
            id: 'd29-offers-' + String(index).padStart(2, '0'),
            actions: [],
            async versions(_query, view) { await view.query({ sql: 'SELECT 1 AS n' }, { maxRows: 1, maxBytes: 64 }); return []; },
            async offers(_query, view) { await view.query({ sql: 'SELECT 1 AS n' }, { maxRows: 1, maxBytes: 64 }); return []; }
        });
}

function step(ctx) {
    ctx.actions.register({
        name: 'd29.step', effect: 'world',
        inputSchema: { type: 'object', properties: {}, additionalProperties: false },
        outputSchema: { type: 'object' }, available: async () => true,
        async execute(_input, context) {
            await context.transaction({ operationId: context.operationId + ':step', maxDurationMs: 10000, maxStatements: 1, maxInputBytes: 65536, maxChangedRows: 8, maxCapturedBytes: 65536 }, async () => undefined);
            return { operationId: context.operationId, status: 'completed', value: { ok: true }, original: null, artifacts: [] };
        }
    });
}

function heavyBody(ctx) {
    ctx.actions.register({
        name: 'd29.body', effect: 'world',
        inputSchema: { type: 'object', properties: {}, additionalProperties: false },
        outputSchema: { type: 'object' }, available: async () => true,
        async execute(_input, context) {
            await context.transaction({ operationId: context.operationId + ':body', maxDurationMs: 10000, maxStatements: 16, maxInputBytes: 65536, maxChangedRows: 8, maxCapturedBytes: 65536 }, async tx => {
                for (let index = 0; index < 17; index++)
                    await tx.query({ sql: 'SELECT 1 AS n' }, { maxRows: 1, maxBytes: 64 });
            });
            return { operationId: context.operationId, status: 'completed', value: { ok: true }, original: null, artifacts: [] };
        }
    });
}

/** 按语句标记分类统计：框架语句由 transaction() 用 {framework:true} 标记，其余归 body。 */
function counter(ctx) {
    const original = ctx.database.transaction.bind(ctx.database);
    let current;
    ctx.database.transaction = (lease, request, work) => original(lease, request, tx => {
        const state = { request, framework: [], body: [] };
        current = state;
        const record = (args, statements) => { (args.some(value => value && typeof value === 'object' && value.framework === true) ? state.framework : state.body).push(statements); };
        return work({
            id: tx.id, operationId: tx.operationId, runId: tx.runId, baseRevision: tx.baseRevision,
            execute: (...args) => { record(args, args[0].sql); return tx.execute(...args); },
            query: (...args) => { record(args, args[0].sql); return tx.query(...args); },
            executeMany: (...args) => { record(args, args[0]); return tx.executeMany(...args); }
        });
    });
    return () => current;
}

async function fixture(count) {
    const f = await setup();
    await f.ctx.plugin(Memory);
    providers(f.ctx, count);
    step(f.ctx);
    heavyBody(f.ctx);
    await f.write('d29-thread', tx => f.ctx.threads.append(tx, threadId(f), { id: 'd29-strategy-seed', interactionId: threadId(f), role: 'user', content: 'seed', originalProviderFields: null }));
    const reservation = await f.ctx.executionFence.reserve({ id: 'd29-action' });
    const context = { ctx: f.ctx, runId: f.runId, generation: 'g', operationId: 'd29-step',
        signal: new AbortController().signal, actorId: 'a', momentId: 'm', logicalTime: 0,
        database: f.ctx.database, writeLease: reservation.lease };
    return { f, reservation, context };
}

async function rows(f) {
    return f.read(view => view.query({ sql: 'SELECT id,body FROM memory_experiences WHERE thread_id=?', parameters: [threadId(f)] }, { maxRows: 2, maxBytes: 65536 }));
}

test('D29 empty body framework statements equal the measured count for 0, 6 and 12 providers', async () => {
    for (const count of [0, 6, 12]) {
        const { f, reservation, context } = await fixture(count);
        try {
            const current = counter(f.ctx);
            const receipt = await f.ctx.actions.execute('d29.step', {}, context);
            assert.equal(receipt.status, 'completed');
            assert.equal(current().framework.length, actionFrameworkStatements(count), `${count} providers: ${current().framework.join('\n')}`);
            assert.equal(current().body.length, 0);
            assert.ok(current().framework.length <= ACTION_FRAMEWORK_STATEMENTS_CEILING);
        }
        finally {
            await reservation.release();
            await f.close();
        }
    }
});

test('D29 memory.write with the run 32 input completes after another action in the same activation', async () => {
    const { f, reservation, context } = await fixture(6);
    try {
        const current = counter(f.ctx);
        const first = await f.ctx.actions.execute('d29.step', {}, context);
        assert.equal(first.status, 'completed');
        const receipt = await f.ctx.actions.execute('memory.write', { threadId: threadId(f), body }, { ...context, operationId: 'd29-memory' });
        assert.equal(receipt.status, 'completed', JSON.stringify(receipt));
        assert.equal(current().request.maxStatements, 16);
        assert.equal(current().framework.length, actionFrameworkStatements(6));
        assert.equal(current().body.length, 1);
        assert.equal(current().framework.length + current().body.length, 17);
        const written = await rows(f);
        assert.equal(written.length, 1);
        assert.equal(JSON.parse(written[0].body), body);
    }
    finally {
        await reservation.release();
        await f.close();
    }
});

test('D29 memory.write completes in a 12 provider world', async () => {
    const { f, reservation, context } = await fixture(12);
    try {
        const current = counter(f.ctx);
        const receipt = await f.ctx.actions.execute('memory.write', { threadId: threadId(f), body }, context);
        assert.equal(receipt.status, 'completed', JSON.stringify(receipt));
        assert.equal(current().framework.length, actionFrameworkStatements(12));
        assert.equal(current().body.length, 1);
        assert.equal(current().framework.length + current().body.length, 29);
        assert.equal((await rows(f)).length, 1);
    }
    finally {
        await reservation.release();
        await f.close();
    }
});

test('D29 the plugin statement budget still rejects a 17 statement body', async () => {
    const { f, reservation, context } = await fixture(6);
    try {
        await assert.rejects(f.ctx.actions.execute('d29.body', {}, context), error => {
            assert.equal(error.code, 'SQLITE_TX_BUDGET');
            assert.match(error.message, /maxStatements 17 > 16/);
            return true;
        });
    }
    finally {
        await reservation.release();
        await f.close();
    }
});
