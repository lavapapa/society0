import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import { CompositionStrategies } from '../.build/host/dist/composition-strategies/index.js';
import strategies from '../.build/host/dist/slot-strategies/index.js';
import { Slots } from '../.build/host/dist/slots/index.js';
import { ExecutionFence } from '../.build/core/dist/execution-fence/index.js';
import { SqliteDatabase } from '../.build/core/dist/sqlite/index.js';
import { Information } from '../.build/core/dist/information/index.js';
import { page as makePage } from '../.build/core/dist/information/page.js';
const req = operationId => ({ operationId, maxDurationMs: 15000, maxStatements: 30, maxInputBytes: 65536, maxChangedRows: 100, maxCapturedBytes: 1048576 });
const request = { maxItems: 2, maxResponseBytes: 8192 }, scope = { actorId: 'a', momentId: 'm', query: { channel: 'notes' } };
async function setup(runId) { const host = await createHost({ boot: [] }); await host.ctx.plugin(OwnedResources); await host.ctx.plugin(TaskSupervisor, { concurrency: 4, maxBytes: 33554432 }); await host.ctx.plugin(CompositionStrategies); await host.ctx.plugin(strategies); await host.ctx.plugin(Slots); await host.ctx.plugin(ExecutionFence); const directory = await mkdtemp(join(tmpdir(), 'pr07-info-')); await host.ctx.plugin(SqliteDatabase, { directory, runId, generation: 'g', maxTransactionMs: 20000 }); await host.ctx.database.applyMigrations([{ owner: 'notes', from: 0, to: 1, source: { consumer: 'pr07' }, after: [], statements: [{ sql: 'CREATE TABLE notes(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,body TEXT NOT NULL,original TEXT NOT NULL)' }] }]); await host.ctx.plugin(Information); let ctx, dispose; await host.ctx.plugin({ inject: ['information', 'database', 'executionFence'], apply(c) { ctx = c; dispose = c.information.register({ id: 'notes', schema: { type: 'object' }, capabilities: ['sql', 'keyset'], available: async () => true, async query(input, scope, view, request) { const last = request.cursor ? JSON.parse(Buffer.from(request.cursor, 'base64url').toString()).after : 0; const [count] = await view.query({ sql: 'SELECT count(*) n FROM notes WHERE actor=?', parameters: [scope.actorId] }, { maxRows: 1, maxBytes: 1024 }); const rows = await view.query({ sql: 'SELECT * FROM notes WHERE actor=? AND id>? ORDER BY id LIMIT ?', parameters: [scope.actorId, last, request.maxItems + 1] }, { maxRows: request.maxItems + 1, maxBytes: 16384 }); return makePage(rows.map(r => ({ kind: 'inline', value: { ...r, id: Number(r.id) } })), String(count.n), view.revision, request, { source: 'notes' }, x => x.value.id); } }); } }); const reservation = await ctx.executionFence.reserve({ id: 'seed' }); await ctx.database.transaction(reservation.lease, req('seed'), tx => tx.executeMany('INSERT INTO notes VALUES(?,?,?,?)', Array.from({ length: 9 }, (_, i) => [i + 1, i === 8 ? 'hidden' : 'a', runId + ' body ' + i, JSON.stringify({ extra: true, i })]), { maxRows: 9, maxInputBytes: 65536 })); await reservation.release(); return { host, ctx, dispose }; }
test('PR07-T02/T04/T06 SQL pages preserve all visible raw fields and reject foreign cursor scopes', async () => {
    const a = await setup('one'), b = await setup('two');
    try {
        const view = await a.ctx.database.snapshot({ maxAgeMs: 30000 }), other = await b.ctx.database.snapshot({ maxAgeMs: 30000 });
        const first = await a.ctx.information.query('notes', {}, scope, view, request);
        assert.equal(first.total, '8');
        assert.equal(first.items[0].value.original, '{"extra":true,"i":0}');
        await assert.rejects(b.ctx.information.query('notes', {}, scope, other, { ...request, cursor: first.next }), /CURSOR_STALE/);
        for (const changed of [{ ...scope, actorId: 'b' }, { ...scope, momentId: 'later' }, { ...scope, query: { different: true } }])
            await assert.rejects(a.ctx.information.query('notes', {}, changed, view, { ...request, cursor: first.next }), /CURSOR_STALE/);
        const all = [...first.items];
        let cursor = first.next;
        while (cursor) {
            const result = await a.ctx.information.query('notes', {}, scope, view, { ...request, maxItems: 3, cursor });
            assert.equal(Buffer.byteLength(JSON.stringify(result)), result.responseBytes);
            all.push(...result.items);
            cursor = result.next;
        }
        assert.deepEqual(all.map(x => x.value.id), [1, 2, 3, 4, 5, 6, 7, 8]);
        await view.close();
        await other.close();
    }
    finally {
        await a.host.close();
        await b.host.close();
    }
});
