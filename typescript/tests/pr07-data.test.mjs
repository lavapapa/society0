import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import { ExecutionFence } from '../../../evidence/pr07/stage/core/dist/execution-fence/index.js';
import { SqliteDatabase } from '../../../evidence/pr07/stage/core/dist/sqlite/index.js';
import { ArtifactsLocal, artifactsMigration } from '../../../evidence/pr07/stage/core/dist/artifacts-local/index.js';
import { Threads, threadMigration } from '../../../evidence/pr07/stage/core/dist/thread/index.js';
import { Results, resultsMigration } from '../../../evidence/pr07/stage/core/dist/results/index.js';
const req = operationId => ({ operationId, maxDurationMs: 15000, maxStatements: 100, maxInputBytes: 1048576, maxChangedRows: 200, maxCapturedBytes: 1048576 });
const page = { maxItems: 2, maxResponseBytes: 16384 };
async function setup() { const host = await createHost({ boot: [] }); const directory = await mkdtemp(join(tmpdir(), 'pr07-data-')); await host.ctx.plugin(OwnedResources); await host.ctx.plugin(TaskSupervisor, { concurrency: 4, maxBytes: 33554432 }); await host.ctx.plugin(ExecutionFence); await host.ctx.plugin(SqliteDatabase, { directory, runId: 'data', generation: 'g', maxTransactionMs: 20000 }); await host.ctx.plugin(ArtifactsLocal, { directory: join(directory, 'objects') }); await host.ctx.database.applyMigrations([artifactsMigration, threadMigration({ test: 'pr07' }), resultsMigration({ test: 'pr07' }), { owner: 'result-fault', from: 0, to: 1, after: ['results'], source: { consumer: 'pr07' }, statements: [{ sql: "CREATE TRIGGER result_second_batch BEFORE INSERT ON result_records WHEN NEW.set_id='second-fail' AND NEW.ordinal=3 BEGIN SELECT RAISE(ABORT,'second SQL batch failed'); END" }] }]); await host.ctx.plugin(Threads, { bodyInlineBytes: 256 }); await host.ctx.plugin(Results, { batchRows: 2, batchBytes: 4096 }); let ctx; await host.ctx.plugin({ inject: ['database', 'executionFence', 'artifacts', 'threads', 'results'], apply(c) { ctx = c; } }); const reservation = await ctx.executionFence.reserve({ id: 'data' }); return { host, ctx, reservation }; }
test('Thread retains provider/tool linkage and atomically rolls back input cursors', async () => {
    const { host, ctx, reservation } = await setup();
    try {
        let view = await ctx.database.snapshot({ maxAgeMs: 30000 });
        const thread = await ctx.threads.open('a', 'm', view);
        await view.close();
        const message = { id: 'msg', interactionId: thread.interactionId, role: 'assistant', content: { toolCallId: 'call1', body: '中文'.repeat(400) }, originalProviderFields: { vendor: { thinking: 'raw' }, tool_calls: [{ id: 'call1', type: 'function' }] } };
        const prepared = await ctx.threads.prepare(message);
        await assert.rejects(ctx.database.transaction(reservation.lease, req('fail'), async (tx) => { await ctx.threads.appendInput(tx, { threadId: thread.id, expectedConsumer: null, nextConsumer: { consumerId: 'source', cursor: 1 }, message: prepared, contexts: [] }); throw Error('second participant'); }), /second participant/);
        view = await ctx.database.snapshot({ maxAgeMs: 30000 });
        assert.equal(await ctx.threads.consumer(thread.id, 'source', view), undefined);
        assert.equal((await ctx.threads.page(thread.id, { actorId: 'a', momentId: 'm', query: null }, view, page)).total, '0');
        await view.close();
        await ctx.database.transaction(reservation.lease, req('ok'), tx => ctx.threads.append(tx, thread.id, prepared));
        view = await ctx.database.snapshot({ maxAgeMs: 30000 });
        const messages = await ctx.threads.page(thread.id, { actorId: 'a', momentId: 'm', query: null }, view, page);
        assert.equal(messages.total, '1');
        await assert.rejects(ctx.threads.page(thread.id, { actorId: 'b', momentId: 'm', query: null }, view, page), /VISIBLE/);
        await view.close();
        const chunks = [];
        for await (const chunk of ctx.threads.readPayload(messages.items[0].payload.ref, { offset: '0', length: Number(messages.items[0].payload.ref.rawBytes), maxBytes: 8192 }))
            chunks.push(chunk);
        assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()), message);
    }
    finally {
        await reservation.release();
        await host.close();
    }
});
test('PR07-T07 one-shot bounded results preserve failed prefix and fixed through', async () => {
    const { host, ctx, reservation } = await setup();
    try {
        let calls = 0;
        async function* rows() {
            for (let i = 0; i < 5; i++) {
                calls++;
                if (i === 4)
                    throw Error('second batch source failure');
                yield { i, raw: '字'.repeat(400) };
            }
        }
        const bounds = { maxBatchRows: 2, maxInputBytes: 4096, maxCapturedBytes: 65536, maxRecordBytes: 8192 };
        await assert.rejects(ctx.results.write(reservation.lease, 'result-write', 'r', rows(), bounds, reservation.signal), /source failure/);
        assert.equal(calls, 5);
        const view = await ctx.database.snapshot({ maxAgeMs: 30000 });
        const description = await ctx.results.describe('r', view);
        assert.equal(description.finished, false);
        assert.equal(description.records, '4');
        const first = await ctx.results.page('r', view, { ...page, maxItems: 1 });
        const second = await ctx.results.page('r', view, { ...page, cursor: first.next, maxItems: 2 });
        assert.equal(second.through, first.through);
        assert.equal(second.items[0].ordinal, '2');
        assert.equal(Buffer.byteLength(JSON.stringify(second)), second.responseBytes);
        await view.close();
    }
    finally {
        await reservation.release();
        await host.close();
    }
});
test('PR07-T07 second SQL batch rolls back; huge rows reject; successful EOF and fixed prefix differ', async () => {
    const { host, ctx, reservation } = await setup();
    try {
        let closed = false, yielded = 0;
        async function* rows() {
            try {
                for (let i = 0; i < 5; i++) {
                    yielded++;
                    yield { i, raw: '原文'.repeat(200) };
                }
            }
            finally {
                closed = true;
            }
        }
        const bounds = { maxBatchRows: 2, maxInputBytes: 4096, maxCapturedBytes: 65536, maxRecordBytes: 8192 };
        await assert.rejects(ctx.results.write(reservation.lease, 'second-write', 'second-fail', rows(), bounds, reservation.signal), /second SQL batch failed/);
        assert.equal(closed, true);
        assert.equal(yielded, 4);
        let view = await ctx.database.snapshot({ maxAgeMs: 30000 });
        assert.deepEqual(await ctx.results.describe('second-fail', view), { id: 'second-fail', through: '2', records: '2', finished: false });
        await view.close();
        async function* huge() { yield { body: 'x'.repeat(9000) }; }
        await assert.rejects(ctx.results.write(reservation.lease, 'huge-write', 'huge', huge(), bounds, reservation.signal), /RESULT_RECORD_BUDGET/);
        async function* good() {
            for (let i = 0; i < 3; i++)
                yield { i };
        }
        assert.equal((await ctx.results.write(reservation.lease, 'good-write', 'good', good(), bounds, reservation.signal)).finished, true);
        view = await ctx.database.snapshot({ maxAgeMs: 30000 });
        const first = await ctx.results.page('good', view, { ...page, maxItems: 1, through: '2' });
        assert.equal(first.finished, false);
        const next = await ctx.results.page('good', view, { ...page, cursor: first.next });
        assert.equal(next.through, '2');
        assert.equal(next.total, '2');
        assert.equal(next.items.length, 1);
        await assert.rejects(ctx.results.page('good', view, { maxItems: 1, maxResponseBytes: 32 }), /PAGE_RESPONSE_BUDGET|PAGE_BUDGET/);
        await view.close();
    }
    finally {
        await reservation.release();
        await host.close();
    }
});
