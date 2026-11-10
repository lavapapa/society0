// cases: D26 memory-repository migration is collected, recall works, and writes stay inside the transaction budget
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { call, fixture, flatten, json, skip, waitFor, worldFile } from './d7-support.mjs';
import { setup, request } from './pr09-support.mjs';
import Memory from '../.build/extras/dist/memory/index.js';
import * as repository from '../.build/extras/dist/memory-repository/index.js';

const experience = (id, threadId) => ({ id, actorId: 'a', threadId, body: { id }, artifacts: [] });

async function write(f, operationId, maxStatements, body) {
    const reservation = await f.ctx.executionFence.reserve({ id: operationId });
    try {
        return await f.ctx.database.transaction(reservation.lease, { ...request(operationId), maxStatements }, body);
    }
    finally {
        await reservation.release();
    }
}

test('D26 memory-repository exports a CLI migration and one statement per experience', () => {
    assert.equal(repository.MEMORY_STATEMENTS_PER_EXPERIENCE, 1);
    const migrations = repository.migrations({ consumer: 'd26-memory-tables' });
    assert.equal(migrations.length, 1);
    assert.equal(migrations[0].owner, 'memory-repository');
    assert.deepEqual(migrations[0].after, ['thread', 'artifacts-local']);
    assert.match(migrations[0].statements[0].sql, /CREATE TABLE memory_experiences/);
    assert.match(migrations[0].statements[0].sql, /CREATE TRIGGER memory_experiences_change_insert/);
});

test('D26 one and many experiences fit the per-experience statement budget', async () => {
    const f = await setup();
    try {
        await write(f, 'd26-one', repository.MEMORY_STATEMENTS_PER_EXPERIENCE, tx => f.ctx.memoryRepository.append(tx, experience('one', f.threadId)));
        await write(f, 'd26-many-append', 8 * repository.MEMORY_STATEMENTS_PER_EXPERIENCE, async tx => {
            for (let index = 0; index < 8; index++)
                await f.ctx.memoryRepository.append(tx, experience('append-' + index, f.threadId));
        });
        const batch = Array.from({ length: 8 }, (_, index) => experience('batch-' + index, f.threadId));
        await write(f, 'd26-many-batch', repository.MEMORY_STATEMENTS_PER_EXPERIENCE, tx => f.ctx.memoryRepository.appendMany(tx, batch));
        for (const id of ['one', ...Array.from({ length: 8 }, (_, index) => 'append-' + index), ...batch.map(value => value.id)])
            assert.equal((await f.read(view => f.ctx.memoryRepository.read(view, id))).id, id);
        const changes = await f.read(view => f.ctx.memoryRepository.changes(view, { maxItems: 64, maxResponseBytes: 16384, throughRevision: view.revision }));
        assert.equal(changes.total, '17');
    }
    finally {
        await f.close();
    }
});

test('D26 memory.write action transaction stays inside the 16 statement budget', async () => {
    const f = await setup();
    try {
        await f.ctx.plugin(Memory);
        f.ctx.actions.provide({
            id: 'd26-budget',
            actions: ['memory.write'],
            async versions(_query, view) {
                for (let index = 0; index < 4; index++)
                    await view.query({ sql: 'SELECT 1 AS n' }, { maxRows: 1, maxBytes: 64 });
                return [{ owner: 'd26-budget', version: '1' }];
            },
            async offers(_query, view) {
                for (let index = 0; index < 6; index++)
                    await view.query({ sql: 'SELECT 1 AS n' }, { maxRows: 1, maxBytes: 64 });
                return [];
            }
        });
        const original = f.ctx.database.transaction.bind(f.ctx.database);
        let statements = 0;
        f.ctx.database.transaction = (lease, request, body) => original(lease, request, tx => {
            const wrapped = {
                id: tx.id, operationId: tx.operationId, runId: tx.runId, baseRevision: tx.baseRevision,
                execute: (...args) => { statements++; return tx.execute(...args); },
                query: (...args) => { statements++; return tx.query(...args); },
                executeMany: (...args) => { statements++; return tx.executeMany(...args); }
            };
            return body(wrapped);
        });
        const reservation = await f.ctx.executionFence.reserve({ id: 'd26-action-budget' });
        try {
            const context = {
                ctx: f.ctx, runId: f.runId, generation: 'g', operationId: 'd26-action-budget',
                signal: new AbortController().signal, actorId: 'a', momentId: 'm', logicalTime: 0,
                database: f.ctx.database, writeLease: reservation.lease
            };
            const receipt = await f.ctx.actions.execute('memory.write', { threadId: f.threadId, body: { id: 'action-budget' } }, context);
            assert.equal(receipt.status, 'completed');
            assert.ok(statements <= 16, `memory.write transaction used ${statements} statements`);
            assert.equal(statements, 15, `memory.write transaction baseline moved to ${statements}`);
        }
        finally {
            await reservation.release();
        }
    }
    finally {
        await f.close();
    }
});

test('D26 recall returns experiences after the memory tables exist', async () => {
    const f = await setup();
    try {
        await f.ctx.plugin(Memory);
        await f.write('d26-recall-seed', tx => f.ctx.memoryRepository.append(tx, experience('recall-me', f.threadId)));
        const found = await f.read(view => f.ctx.memory.recall({ actorId: 'a', text: 'recall-me', maxItems: 10, maxResponseBytes: 16384 }, view));
        assert.equal(found.items.length, 1);
        assert.equal(found.items[0].id, 'recall-me');
    }
    finally {
        await f.close();
    }
});

test('D26 a world without memory tables reports MEMORY_SCHEMA_MISSING for recall and write', async () => {
    const f = await setup([], { memoryMigration: false });
    try {
        await f.ctx.plugin(Memory);
        const missing = error => {
            assert.equal(error.code, 'MEMORY_SCHEMA_MISSING');
            assert.match(error.message, /memory-repository migration/);
            return true;
        };
        await assert.rejects(f.read(view => f.ctx.memory.recall({ actorId: 'a', maxItems: 10, maxResponseBytes: 16384 }, view)), missing);
        await assert.rejects(f.write('d26-write-missing', tx => f.ctx.memoryRepository.append(tx, experience('missing-table', f.threadId))), missing);
    }
    finally {
        await f.close();
    }
});

test('D26 check and run start collect the memory-repository migration', { skip, timeout: 300_000 }, async () => {
    const { directory, profile } = await fixture('rule-sqlite', value => {
        const run = flatten(value.patches[0].insert).find(entry => entry.id === 'run');
        assert.ok(run, 'rule-sqlite profile must contain the run group');
        run.config.push({ id: 'memory-repository', name: '@society0/extras/memory-repository', config: {} });
    });
    const checked = json(call(['check', '--profile', profile, '--json']));
    assert.equal(checked.ok, true, JSON.stringify(checked.missing));
    assert.ok(checked.migrations.includes('memory-repository'), JSON.stringify(checked.migrations));

    const world = await worldFile(directory);
    const runDir = join(directory, 'run-d26-memory');
    const started = json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--world', world, '--ticks', '1', '--detach', '--json']));
    assert.equal(started.schema, 'society0.run/1');
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));

    const db = new DatabaseSync(join(runDir, 'world.sqlite'), { readOnly: true });
    try {
        const names = new Set(db.prepare("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')").all().map(row => String(row.name)));
        assert.ok(names.has('memory_experiences'), JSON.stringify([...names]));
        assert.ok(names.has('memory_changes'), JSON.stringify([...names]));
        assert.ok(names.has('memory_experiences_change_insert'), JSON.stringify([...names]));
        const schema = db.prepare("SELECT version FROM s0_schema WHERE owner='memory-repository'").get();
        assert.equal(Number(schema?.version), 1);
    }
    finally {
        db.close();
    }
});
