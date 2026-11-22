import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, activation, request } from './pr10-support.mjs';
import Drivers from '../.build/core/dist/drivers/index.js';
import Repository, { measurementMigration } from '../.build/social/dist/measurement-repository/index.js';
import Measurements from '../.build/social/dist/measurements/index.js';
import Every from '../.build/social/dist/measurement-every/index.js';

async function environment(options) {
    const f = await fixture({ migrations: [measurementMigration], artifacts: options.artifacts });
    const c = f.ctx;
    await c.plugin(Drivers);
    await c.plugin(Repository, options.repository ?? {});
    await c.plugin(Measurements, options.measurements ?? {});
    await c.plugin(Every);
    await c.plugin({
        inject: ['measurements'], apply(ctx) {
            for (const spec of options.specs) ctx.measurements.register(spec);
        }
    });
    const held = await c.executionFence.reserve({ id: options.id });
    return {
        f, c, held, context: activation(f, held, options.operationId ?? options.id),
        async close() { await held.release(); await f.host.close(); }
    };
}

const session = { actorId: 'a', momentId: 'moment' };
const address = (context, specId) => ({ operationId: context.operationId + '/measurement/' + specId, attempt: 0 });

async function load(c, stored) {
    assert.ok(stored && (stored.kind === 'inline' || stored.kind === 'reference'), 'StoredValue shape: ' + JSON.stringify(stored));
    if (stored.kind === 'inline') return stored.value;
    const length = Number(stored.ref.rawBytes), chunks = [];
    for await (const chunk of c.measurementRepository.readPayload(stored.ref, { offset: '0', length, maxBytes: length })) chunks.push(Buffer.from(chunk));
    return JSON.parse(Buffer.concat(chunks).toString());
}

async function page(c, filter = {}) {
    const view = await c.database.snapshot({ maxAgeMs: 30000 });
    try {
        return await c.measurementRepository.page(view, { maxItems: 10, maxResponseBytes: 65536 }, filter);
    } finally { await view.close(); }
}

test('RV3-06-T01 interview observation is recorded verbatim in the session log', async () => {
    const observation = { value: { answer: '完整答案' }, original: { status: 503, body: '完整外部回答'.repeat(12000) }, source: { provider: 'native-interview' } };
    const spec = { id: 'large-interview', kind: 'interview', required: true, async observe() { return observation; } };
    const s = await environment({ id: 'rv3-06-t01', specs: [spec], measurements: { maxResultBytes: 262144 }, repository: { inlineBytes: 1024 } });
    try {
        const outcome = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(outcome.status, 'completed', String(outcome.failure));
        const recorded = await s.c.sessions.find(session, address(s.context, spec.id));
        assert.equal(recorded.type, 'measurement');
        assert.deepEqual(recorded.data.observation, observation);
        assert.equal(recorded.data.specId, spec.id);
        assert.equal((await page(s.c)).total, '1');
    } finally { await s.close(); }
});

test('RV3-06-T03 failed interview records verbatim failure text and original', async () => {
    const body = '完整原始失败响应'.repeat(12000);
    const spec = { id: 'failed', kind: 'interview', required: true, async observe() { throw Object.assign(Error('HTTP_INTERVIEW_503'), { original: { status: 503, body } }); } };
    const s = await environment({ id: 'rv3-06-t03', specs: [spec] });
    try {
        const outcome = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(outcome.status, 'incomplete');
        assert.equal(outcome.failure, 'Error: HTTP_INTERVIEW_503');
        const recorded = await s.c.sessions.find(session, address(s.context, spec.id));
        assert.equal(recorded.type, 'attempt/failed');
        assert.equal(recorded.data.cause, 'Error: HTTP_INTERVIEW_503');
        assert.deepEqual(recorded.data.original, { status: 503, body });
        assert.equal((await page(s.c)).total, '0');
    } finally { await s.close(); }
});

test('RV3-06-T04 oversized interview still records the full observation before the check fails', async () => {
    const observation = { value: { answer: '完整答案' }, original: { response: '完整原始响应'.repeat(400) }, source: { provider: 'native-oversize' } };
    const spec = { id: 'oversize-interview', kind: 'interview', required: true, async observe() { return observation; } };
    const s = await environment({ id: 'rv3-06-t04', specs: [spec], measurements: { maxResultBytes: 1024 } });
    try {
        const outcome = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(outcome.status, 'incomplete');
        assert.match(String(outcome.failure), /MEASUREMENT_RESULT_BYTES/);
        const recorded = await s.c.sessions.find(session, address(s.context, spec.id));
        assert.equal(recorded.type, 'measurement');
        assert.deepEqual(recorded.data.observation, observation);
        assert.equal((await page(s.c)).total, '0');
    } finally { await s.close(); }
});

test('RV3-06-T06 shared Json observations are accepted while Date and BigInt are invalid', async () => {
    const shared = { body: 's'.repeat(2000) };
    const calls = { shared: 0, date: 0, bigint: 0 };
    const specs = [
        {
            id: 'shared', kind: 'measurement', required: true, async observe() {
                calls.shared++;
                return { value: shared, original: shared, source: { provider: 'shared-reference' } };
            }
        },
        {
            id: 'date', kind: 'measurement', required: true, async observe() {
                calls.date++;
                return { value: { at: new Date(0) }, original: { at: new Date(0) }, source: null };
            }
        },
        {
            id: 'bigint', kind: 'measurement', required: true, async observe() {
                calls.bigint++;
                return { value: { answer: 1n }, original: null, source: null };
            }
        }
    ];
    const s = await environment({ id: 'rv3-06-t06', specs });
    try {
        const first = await s.c.measurements.collect(s.context);
        const second = await s.c.measurements.collect(activation(s.f, s.held, 'rv3-06-t06b'));
        for (const result of [first, second]) {
            const [sharedOutcome, dateOutcome, bigintOutcome] = result.value;
            assert.equal(sharedOutcome.status, 'completed');
            assert.equal(sharedOutcome.value.kind, 'inline');
            assert.deepEqual(sharedOutcome.value.value, shared);
            assert.equal(sharedOutcome.original.kind, 'inline');
            assert.deepEqual(sharedOutcome.original.value, shared);
            assert.equal(dateOutcome.status, 'incomplete');
            assert.equal(dateOutcome.failure, 'MEASUREMENT_RESULT_INVALID');
            assert.equal(bigintOutcome.status, 'incomplete');
            assert.equal(bigintOutcome.failure, 'MEASUREMENT_RESULT_INVALID');
        }
        assert.deepEqual(calls, { shared: 2, date: 2, bigint: 2 });
        const sharedRows = await page(s.c, { specId: 'shared' });
        assert.equal(sharedRows.total, '2');
        assert.deepEqual(await load(s.c, sharedRows.items[0].value), shared);
        assert.deepEqual(await load(s.c, sharedRows.items[0].original), shared);
        assert.equal((await page(s.c, { specId: 'date' })).total, '0');
        assert.equal((await page(s.c, { specId: 'bigint' })).total, '0');
        await assert.rejects(s.c.database.transaction(s.held.lease, request('rv3-06-prepare'), tx => s.c.measurementRepository.append(tx, {
            id: 'unprepared', specId: 'shared', kind: 'measurement', actorId: 'a', logicalTime: '1', value: { kind: 'inline', value: { body: 'x'.repeat(20000) } }, original: { kind: 'inline', value: null }, source: { kind: 'inline', value: null }, required: false
        })), /MEASUREMENT_PREPARE_REQUIRED/, 'append rejects unprepared inline fields');
    } finally { await s.close(); }
});
