import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Actors } from '../.build/core/dist/actors/index.js';
test('PR06 production actors entry exists', () => assert.equal(typeof Actors, 'function'));
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import { ExecutionFence } from '../.build/core/dist/execution-fence/index.js';
import { SqliteDatabase } from '../.build/core/dist/sqlite/index.js';
import { actorsMigration } from '../.build/core/dist/actors/index.js';
import { ActorEligibility } from '../.build/core/dist/actor-eligibility/index.js';
export const request = operationId => ({ operationId, maxDurationMs: 15000, maxStatements: 30, maxInputBytes: 65536, maxChangedRows: 100, maxCapturedBytes: 1048576 });
export async function setup() { const host = await createHost({ boot: [] }); await host.ctx.plugin(OwnedResources); await host.ctx.plugin(TaskSupervisor, { concurrency: 4 }); await host.ctx.plugin(ExecutionFence); const directory = await mkdtemp(join(tmpdir(), 'pr06-')); await host.ctx.plugin(SqliteDatabase, { directory, runId: 'pr06', generation: 'g', maxTransactionMs: 20000 }); await host.ctx.database.applyMigrations([actorsMigration]); await host.ctx.plugin(Actors); await host.ctx.plugin(ActorEligibility); let ctx; await host.ctx.plugin({ inject: ['actors', 'actorEligibility', 'database', 'executionFence'], apply(owner) { ctx = owner; } }); return { host, ctx, directory }; }
test('PR06 actors birth/exit atomic with domain SQL and eligibility recheck', async () => {
    const { host, ctx } = await setup();
    try {
        const r = await ctx.executionFence.reserve({ id: 'seed' });
        await ctx.database.transaction(r.lease, request('seed'), async (tx) => { await ctx.actors.create(tx, { id: 'a', attributes: { name: '甲' } }); await ctx.actors.create(tx, { id: 'b', attributes: {} }); });
        const input = { kind: 'activation', actorId: 'a', momentId: 'm', logicalTime: '0', intentId: 'i', driver: 'rule' };
        let snapshot = await ctx.database.snapshot({ maxAgeMs: 30000 });
        assert.equal((await ctx.actorEligibility.evaluate(input, snapshot)).allowed, true);
        await snapshot.close();
        await assert.rejects(ctx.database.transaction(r.lease, request('rollback'), async (tx) => { await ctx.actors.setParticipation(tx, 'a', 'inactive', { cause: 'exit' }); throw Error('rollback'); }), /rollback/);
        await ctx.database.transaction(r.lease, request('exit'), async (tx) => { await ctx.actors.setParticipation(tx, 'a', 'inactive', { cause: 'exit' }); assert.equal((await ctx.actorEligibility.evaluate(input, tx)).allowed, false); });
        snapshot = await ctx.database.snapshot({ maxAgeMs: 30000 });
        assert.equal((await ctx.actors.get('a', snapshot)).attributes.name, '甲');
        assert.equal((await ctx.actorEligibility.evaluate(input, snapshot)).allowed, false);
        await snapshot.close();
        await r.release();
    }
    finally {
        await host.close();
    }
});
