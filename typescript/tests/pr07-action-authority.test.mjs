import { test } from 'node:test';
import assert from 'node:assert/strict';
import { promisify } from 'node:util';
import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setup, request, read } from './pr07-support.mjs';
test('PR07 external process effect with lost response retains unknown send authority', async () => {
    const env = await setup(), { ctx } = env;
    try {
        const target = join(env.directory, 'physical-effect');
        let partial;
        ctx.actions.register({ name: 'external.lost', inputSchema: {}, outputSchema: {}, effect: 'external', available: async () => true, async execute() { await promisify(execFile)(process.execPath, ['-e', 'require("node:fs").writeFileSync(process.argv[1],"physical effect committed")', target]); async function* bytes() { yield await readFile(target); } partial = await ctx.artifacts.stage(bytes(), 'text/plain'); throw Object.assign(Error('response lost after physical effect'), { artifacts: [partial] }); } });
        await assert.rejects(ctx.actions.execute('external.lost', null, env.context('lost-response')), /response lost/);
        assert.equal(await readFile(target, 'utf8'), 'physical effect committed');
        const record = await ctx.attempts.probe({ lineage: 'pr07', operationId: 'action:lost-response', attempt: 0 });
        assert.equal(record.record.status, 'unknown');
        assert.ok(record.record.artifacts.some(ref => ref.id === partial.id));
        let original = '';
        for await (const chunk of ctx.attempts.readArtifact(partial))
            original += Buffer.from(chunk).toString();
        assert.equal(original, 'physical effect committed');
        await assert.rejects(ctx.actions.execute('external.lost', null, env.context('lost-response')), /ACTION_ATTEMPT_UNKNOWN/);
    }
    finally {
        await env.close();
    }
});
test('PR07 world retry uses run SQL receipts rather than old failed evidence send gates', async () => { const env = await setup(), { ctx } = env; let fail = true; try {
    ctx.actions.register({ name: 'world.retry', inputSchema: {}, outputSchema: {}, effect: 'world', available: async () => true, async execute(input, context) { const operationId = 'retry-write:' + String(fail); const result = await context.transaction(request(operationId), async (tx) => { await ctx.actors.update(tx, { id: 'a', attributes: { completed: true } }); if (fail)
            throw Error('rollback before commit'); return { completed: true }; }); return { operationId, status: 'completed', value: result.value, original: { raw: 'full' }, artifacts: [] }; } });
    await assert.rejects(ctx.actions.execute('world.retry', null, env.context('retry-invocation')), /rollback before commit/);
    fail = false;
    assert.equal((await ctx.actions.execute('world.retry', null, env.context('retry-invocation'))).status, 'completed');
    await assert.rejects(ctx.actions.execute('world.retry', null, env.context('retry-invocation')), /ACTION_ALREADY_COMMITTED/);
    assert.equal((await read(ctx, v => v.query({ sql: 'SELECT count(*) AS n FROM action_receipts WHERE invocation_id=?', parameters: ['retry-invocation'] }, { maxRows: 1, maxBytes: 1024 })))[0].n, 1n);
}
finally {
    await env.close();
} });
test('PR07 ActionReceipt confirms every committed RPC and failure retains earlier committed facts', async () => { const env = await setup(), { ctx } = env; try {
    ctx.actions.register({ name: 'multi', inputSchema: {}, outputSchema: {}, effect: 'world', available: async () => true, async execute(input, context) { await context.transaction(request('multi-a'), tx => tx.execute({ sql: 'UPDATE inventory SET quantity=2' })); await context.transaction(request('multi-b'), tx => tx.execute({ sql: 'UPDATE inventory SET quantity=3' })); return { operationId: 'reported', status: 'completed', value: null, original: { raw: true }, artifacts: [], requiredOperations: ['fabricated'] }; } });
    const receipt = await ctx.actions.execute('multi', null, env.context('multi-invocation'));
    assert.deepEqual(receipt.requiredOperations, ['multi-a', 'multi-b']);
    const known = await ctx.attempts.probe({ lineage: 'pr07', operationId: 'action-run:' + JSON.stringify(['pr07', 'multi-invocation']), attempt: 0 });
    assert.deepEqual(known.record.result.receipt.value.requiredOperations, ['multi-a', 'multi-b']);
    ctx.actions.register({ name: 'duplicate', inputSchema: {}, outputSchema: {}, effect: 'world', available: async () => true, async execute(input, context) { await context.transaction(request('committed-once'), tx => tx.execute({ sql: 'UPDATE inventory SET quantity=4' })); await context.transaction(request('committed-once'), tx => tx.execute({ sql: 'UPDATE inventory SET quantity=5' })); throw Error('unreachable'); } });
    await assert.rejects(ctx.actions.execute('duplicate', null, env.context('duplicate-invocation')), error => { assert.equal(error.code, 'OPERATION_EXISTS'); assert.deepEqual(error.requiredOperations, ['committed-once']); return true; });
    const failed = await ctx.attempts.probe({ lineage: 'pr07', operationId: 'action-run:' + JSON.stringify(['pr07', 'duplicate-invocation']), attempt: 0 });
    assert.deepEqual(failed.record.result.requiredOperations, ['committed-once']);
    assert.equal((await read(ctx, v => v.query({ sql: 'SELECT quantity FROM inventory' }, { maxRows: 1, maxBytes: 1024 })))[0].quantity, 4n);
}
finally {
    await env.close();
} });
