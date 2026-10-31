import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, activation, request } from './pr10-support.mjs';
import Drivers from '../.build/core/dist/drivers/index.js';
import Repository, { measurementMigration } from '../.build/social/dist/measurement-repository/index.js';
import Measurements from '../.build/social/dist/measurements/index.js';
import Every from '../.build/social/dist/measurement-every/index.js';

const encoded = value => Buffer.from(JSON.stringify(value));

async function environment(options) {
    const f = await fixture({ migrations: [measurementMigration], attempts: options.attempts });
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

function identity(c, context, specId) {
    return { lineage: c.attempts.authority().lineage, operationId: context.operationId + '/measurement/' + specId, attempt: 0 };
}

function failAppendOnce(c) {
    const append = c.measurementRepository.append.bind(c.measurementRepository);
    let armed = true;
    c.measurementRepository.append = async (...args) => {
        if (armed) { armed = false; throw Error('MEASUREMENT_APPEND_FAILED'); }
        return append(...args);
    };
}

async function load(c, stored) {
    assert.ok(stored && (stored.kind === 'inline' || stored.kind === 'reference'), 'StoredValue shape: ' + JSON.stringify(stored));
    if (stored.kind === 'inline') return stored.value;
    const length = Number(stored.ref.rawBytes), chunks = [];
    for await (const chunk of c.measurementRepository.readPayload(stored.ref, { offset: '0', length, maxBytes: length })) chunks.push(Buffer.from(chunk));
    return JSON.parse(Buffer.concat(chunks).toString());
}

async function readArtifact(c, ref) {
    const chunks = [];
    for await (const chunk of c.attempts.readArtifact(ref)) chunks.push(Buffer.from(chunk));
    return Buffer.concat(chunks);
}

async function page(c, filter = {}) {
    const view = await c.database.snapshot({ maxAgeMs: 30000 });
    try {
        return await c.measurementRepository.page(view, { maxItems: 10, maxResponseBytes: 65536 }, filter);
    } finally { await view.close(); }
}

test('RV3-06-T01 large interview value replays from ledger without a second observe', async () => {
    const original = { status: 503, body: '完整外部回答'.repeat(12000) }, calls = { n: 0 };
    const spec = {
        id: 'large-interview', kind: 'interview', required: true, async observe() {
            calls.n++;
            return { value: { answer: '完整答案' }, original, source: { provider: 'native-interview' } };
        }
    };
    const s = await environment({
        id: 'rv3-06-t01', specs: [spec], measurements: { maxResultBytes: 262144 }, repository: { inlineBytes: 1024 }
    });
    try {
        failAppendOnce(s.c);
        const first = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(first.status, 'incomplete');
        assert.match(String(first.failure), /MEASUREMENT_APPEND_FAILED/);
        const probe = await s.c.attempts.probe(identity(s.c, s.context, spec.id));
        assert.equal(probe.status, 'found');
        assert.equal(probe.record.status, 'settled', 'observation persisted in ledger despite append failure');
        assert.equal(probe.record.result.ok, true);
        assert.equal(probe.record.result.observation.original.kind, 'reference');
        assert.equal((await page(s.c)).total, '0', 'append failure left no repository row');
        const second = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(second.status, 'completed');
        assert.equal(second.value.kind, 'inline');
        assert.deepEqual(second.value.value, { answer: '完整答案' });
        assert.equal(second.original.kind, 'reference');
        assert.deepEqual(await load(s.c, second.original), original);
        assert.equal(second.source.kind, 'inline');
        assert.equal(calls.n, 1, 'replay must not observe again');
        const records = await page(s.c);
        assert.equal(records.total, '1');
        const raw = [];
        for await (const chunk of s.c.measurementRepository.readPayload(records.items[0].original.ref, { offset: '0', length: Number(records.items[0].original.ref.rawBytes), maxBytes: Number(records.items[0].original.ref.rawBytes) })) raw.push(Buffer.from(chunk));
        assert.equal(Buffer.concat(raw).toString(), JSON.stringify(original), 'repository payload byte-identical');
    } finally { await s.close(); }
});

test('RV3-06-T02 observation at maxResultBytes-10 replays outside the envelope window', async () => {
    const max = 49152;
    const build = size => ({ value: { answer: 'a'.repeat(size) }, original: { response: 'b'.repeat(size) }, source: { provider: 'native-window' } });
    let low = 0, high = 200000;
    while (low < high) {
        const middle = (low + high + 1) >> 1;
        if (Buffer.byteLength(JSON.stringify(build(middle))) <= max - 10) low = middle; else high = middle - 1;
    }
    const observation = build(low);
    assert.equal(Buffer.byteLength(JSON.stringify(observation)), max - 10);
    assert.ok(Buffer.byteLength(JSON.stringify({ ok: true, observation })) > max, 'old envelope check would reject replay');
    const calls = { n: 0 };
    const spec = {
        id: 'window-interview', kind: 'interview', required: true, async observe() {
            calls.n++;
            return observation;
        }
    };
    const s = await environment({ id: 'rv3-06-t02', specs: [spec] });
    try {
        failAppendOnce(s.c);
        const first = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(first.status, 'incomplete');
        assert.match(String(first.failure), /MEASUREMENT_APPEND_FAILED/);
        assert.doesNotMatch(String(first.failure), /MEASUREMENT_RESULT_BYTES/);
        assert.equal((await s.c.attempts.probe(identity(s.c, s.context, spec.id))).record.status, 'settled');
        assert.equal((await page(s.c)).total, '0');
        const second = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(second.status, 'completed', String(second.failure));
        assert.deepEqual(await load(s.c, second.value), observation.value);
        assert.deepEqual(await load(s.c, second.original), observation.original);
        assert.deepEqual(await load(s.c, second.source), observation.source);
        assert.equal(calls.n, 1);
        assert.equal((await page(s.c)).total, '1');
    } finally { await s.close(); }
});

test('RV3-06-T03 failed interviews replay the verbatim original failure text', async () => {
    const body = '完整原始失败响应'.repeat(12000), large = 'f'.repeat(1048578);
    const cases = [
        {
            name: '3a', error: () => Object.assign(Error('HTTP_INTERVIEW_503'), { original: { status: 503, body } }), text: 'Error: HTTP_INTERVIEW_503', original: { kind: 'reference', value: { status: 503, body } }
        },
        { name: '3b', error: () => new TypeError('bad'), text: 'TypeError: bad', original: { kind: 'inline', value: null } },
        { name: '3c', error: () => 'plain', text: 'plain', original: { kind: 'inline', value: null } },
        { name: '3d', error: () => large, text: large, original: { kind: 'inline', value: null }, failureReference: true }
    ];
    for (const item of cases) {
        const calls = { n: 0 };
        const spec = {
            id: 'failed-' + item.name, kind: 'interview', required: true, async observe() {
                calls.n++;
                throw item.error();
            }
        };
        const s = await environment({ id: 'rv3-06-t03-' + item.name, specs: [spec] });
        try {
            const first = (await s.c.measurements.collect(s.context)).value[0];
            const second = (await s.c.measurements.collect(s.context)).value[0];
            assert.equal(first.status, 'incomplete', item.name);
            assert.equal(second.status, 'incomplete', item.name);
            assert.ok(first.failure === item.text, item.name + ' first verbatim');
            assert.ok(second.failure === item.text, item.name + ' replay verbatim');
            assert.equal(calls.n, 1, item.name + ' observe once');
            const probe = await s.c.attempts.probe(identity(s.c, s.context, spec.id));
            assert.equal(probe.record.status, 'settled');
            assert.equal(probe.record.result.ok, false);
            assert.equal(probe.record.result.original.kind, item.original.kind);
            if (item.original.kind === 'reference') assert.deepEqual(await load(s.c, probe.record.result.original), item.original.value);
            assert.equal(probe.record.result.failure.kind, item.failureReference ? 'reference' : 'inline');
            if (item.failureReference) assert.ok(JSON.parse((await readArtifact(s.c, probe.record.result.failure.ref.object)).toString()) === item.text, item.name + ' failure artifact verbatim');
            assert.equal((await page(s.c)).total, '0');
        } finally { await s.close(); }
    }
});

test('RV3-06-T03 toString-throwing observed error normalizes to unknown', async () => {
    class Hostile extends Error {
        toString() { throw Error('STRINGIFY_FAILED'); }
    }
    const calls = { n: 0 };
    const spec = {
        id: 'hostile-error', kind: 'interview', required: true, async observe() {
            calls.n++;
            throw new Hostile('hostile');
        }
    };
    const s = await environment({ id: 'rv3-06-t03-hostile', specs: [spec] });
    try {
        const first = (await s.c.measurements.collect(s.context)).value[0];
        const second = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(first.status, 'incomplete');
        assert.ok(first.failure === 'MEASUREMENT_ATTEMPT_UNKNOWN', 'first unknown');
        assert.ok(second.failure === 'MEASUREMENT_ATTEMPT_UNKNOWN', 'replay unknown');
        assert.equal(calls.n, 1);
        const probe = await s.c.attempts.probe(identity(s.c, s.context, spec.id));
        assert.equal(probe.record.status, 'pending');
        assert.equal((await page(s.c)).total, '0');
    } finally { await s.close(); }
});

test('RV3-06-T04 oversized interview keeps the full observation as attempt evidence', async () => {
    const observation = { value: { answer: '完整答案' }, original: { response: '完整原始响应'.repeat(400) }, source: { provider: 'native-oversize' } };
    assert.ok(Buffer.byteLength(JSON.stringify(observation)) > 1024);
    const calls = { n: 0 };
    const spec = {
        id: 'oversize-interview', kind: 'interview', required: true, async observe() {
            calls.n++;
            return observation;
        }
    };
    const s = await environment({ id: 'rv3-06-t04', specs: [spec], measurements: { maxResultBytes: 1024 } });
    try {
        const first = (await s.c.measurements.collect(s.context)).value[0];
        const second = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(first.status, 'incomplete');
        assert.equal(second.status, 'incomplete');
        assert.match(String(first.failure), /MEASUREMENT_RESULT_BYTES/);
        assert.equal(second.failure, first.failure);
        assert.equal(calls.n, 1);
        const probe = await s.c.attempts.probe(identity(s.c, s.context, spec.id));
        assert.equal(probe.record.status, 'settled');
        const evidence = JSON.parse((await readArtifact(s.c, probe.record.result.evidence)).toString());
        assert.deepEqual(evidence, observation, 'evidence is the observation itself');
        assert.equal((await page(s.c)).total, '0');
    } finally { await s.close(); }
});

test('RV3-06-T05 low ledger record budget stores the envelope by reference and settles unknown without a row', async () => {
    const calls = { n: 0 };
    const spec = {
        id: 'bounded-interview', kind: 'interview', required: true, async observe() {
            calls.n++;
            return { value: { answer: 'a'.repeat(900) }, original: { response: 'b'.repeat(900) }, source: { provider: 'c'.repeat(900) } };
        }
    };
    const s = await environment({
        id: 'rv3-06-t05', specs: [spec], attempts: { maxRecordBytes: 1024 }, repository: { inlineBytes: 1024 }
    });
    try {
        failAppendOnce(s.c);
        const first = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(first.status, 'incomplete');
        assert.match(String(first.failure), /MEASUREMENT_APPEND_FAILED/);
        const record = (await s.c.attempts.probe(identity(s.c, s.context, spec.id))).record;
        assert.equal(s.c.attempts.maxRecordBytes, 1024);
        assert.equal(record.status, 'settled');
        assert.equal(record.result.observation, undefined, 'envelope itself is a reference');
        const envelope = JSON.parse((await readArtifact(s.c, record.result.envelope)).toString());
        assert.equal(envelope.ok, true);
        assert.equal(envelope.observation.original.kind, 'inline');
        const second = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(second.status, 'completed', String(second.failure));
        assert.equal(calls.n, 1);
        assert.equal((await page(s.c)).total, '1');
    } finally { await s.close(); }

    const failing = {
        id: 'settle-failure', kind: 'interview', required: true, async observe() {
            return { value: { answer: 'small' }, original: null, source: null };
        }
    };
    const t = await environment({
        id: 'rv3-06-t05b', specs: [failing], attempts: { maxRecordBytes: 1024 }, repository: { inlineBytes: 1024 }
    });
    try {
        t.c.attempts.settle = async () => { throw Error('MEASUREMENT_SETTLE_FAILED'); };
        const first = (await t.c.measurements.collect(t.context)).value[0];
        assert.equal(first.status, 'incomplete');
        assert.equal(first.failure, 'MEASUREMENT_ATTEMPT_UNKNOWN');
        const probe = await t.c.attempts.probe(identity(t.c, t.context, failing.id));
        assert.equal(probe.record.status, 'pending');
        assert.equal((await page(t.c)).total, '0');
        const second = (await t.c.measurements.collect(t.context)).value[0];
        assert.equal(second.status, 'incomplete');
        assert.equal(second.failure, 'MEASUREMENT_ATTEMPT_UNKNOWN');
        assert.equal((await page(t.c)).total, '0');
    } finally { await t.close(); }
});

test('RV3-06-T05 default inline fields keep a 48KB envelope readable above four ledger budgets', async () => {
    const field = 'x'.repeat(16000);
    const observation = { value: { answer: field }, original: { response: field }, source: { provider: field } };
    assert.ok(Buffer.byteLength(JSON.stringify(observation)) <= 49152);
    const calls = { n: 0 };
    const spec = {
        id: 'wide-inline', kind: 'interview', required: true, async observe() {
            calls.n++;
            return observation;
        }
    };
    const s = await environment({ id: 'rv3-06-t05c', specs: [spec], attempts: { maxRecordBytes: 1024 } });
    try {
        failAppendOnce(s.c);
        const first = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(first.status, 'incomplete');
        assert.match(String(first.failure), /MEASUREMENT_APPEND_FAILED/);
        const record = (await s.c.attempts.probe(identity(s.c, s.context, spec.id))).record;
        assert.equal(record.status, 'settled');
        assert.equal(record.result.observation, undefined);
        assert.ok(Number(record.result.envelope.bytes) > 4096, 'envelope exceeds the old maxRecordBytes*4 cap');
        const envelope = JSON.parse((await readArtifact(s.c, record.result.envelope)).toString());
        assert.equal(envelope.ok, true);
        const second = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(second.status, 'completed', String(second.failure));
        assert.equal(calls.n, 1);
        assert.equal((await page(s.c)).total, '1');
    } finally { await s.close(); }
});

test('RV3-06-T05 failure text above ARTIFACT_LIMIT is never staged', async () => {
    const text = 'x'.repeat(67108864);
    const calls = { n: 0 };
    let stageCalls = 0;
    const spec = {
        id: 'oversize-failure', kind: 'interview', required: true, async observe() {
            calls.n++;
            throw new Error(text);
        }
    };
    const s = await environment({ id: 'rv3-06-t05-huge-failure', specs: [spec], artifacts: { maxObjectBytes: 134217728 } });
    try {
        s.c.artifacts.stage = async () => { stageCalls++; throw Error('ARTIFACT_STAGE_INTERCEPTED'); };
        const first = (await s.c.measurements.collect(s.context)).value[0];
        const second = (await s.c.measurements.collect(s.context)).value[0];
        assert.equal(first.status, 'incomplete');
        assert.ok(first.failure === 'MEASUREMENT_ATTEMPT_UNKNOWN', 'first unknown');
        assert.ok(second.failure === 'MEASUREMENT_ATTEMPT_UNKNOWN', 'replay unknown');
        assert.equal(stageCalls, 0, 'failure write precheck must skip staging');
        assert.equal(calls.n, 1);
        const probe = await s.c.attempts.probe(identity(s.c, s.context, spec.id));
        assert.equal(probe.record.status, 'pending');
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
