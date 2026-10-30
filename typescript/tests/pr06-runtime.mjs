import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import CompositionStrategies from '../.build/host/dist/composition-strategies/index.js';
import SlotStrategies from '../.build/host/dist/slot-strategies/index.js';
import Slots from '../.build/host/dist/slots/index.js';
const base = '../.build/core/dist/';
const modules = await Promise.all(['execution-fence', 'sqlite', 'sqlite-bootstrap', 'sqlite-history', 'artifacts-local', 'initializers', 'actors', 'actor-eligibility', 'drivers', 'activation-mailbox', 'activation', 'clock-ticks', 'proposal-sequence', 'runtime-staged', 'completion-strict', 'checkpoint-policy', 'termination', 'generation-views', 'generation-binding', 'run-controller'].map(n => import(base + n + '/index.js')));
const [Fence, Database, Bootstrap, History, Artifacts, Initializers, Actors, Eligibility, Drivers, Mailbox, Activation, Ticks, Sequence, Runtime, Completion, Checkpoint, Termination, Views, Binding, Runs] = modules;
const req = operationId => ({ operationId, maxDurationMs: 15000, maxStatements: 100, maxInputBytes: 1048576, maxChangedRows: 1000, maxCapturedBytes: 4194304 });
export async function fixture(options = {}) {
    const host = await createHost({ boot: [] });
    const c = host.ctx;
    const directory = options.directory ?? await mkdtemp(join(tmpdir(), 'pr06-runtime-'));
    const runId = options.runId ?? 'run';
    try {
        for (const plugin of [OwnedResources, TaskSupervisor, CompositionStrategies, SlotStrategies, Slots, Fence.default])
            await c.plugin(plugin);
        await c.plugin(Database.default, { directory, runId, generation: 'g', maxTransactionMs: 20000 });
        await c.plugin(Artifacts.default, { directory: join(directory, 'artifacts') });
        await c.plugin(Initializers.default);
        await c.plugin(Bootstrap.default, { maxTransactionMs: 15000 });
        await c.plugin(History.default, { directory, runId, generation: 'g', controlSequence: '0' });
        await c.plugin(Actors.default);
        await c.plugin(Eligibility.default);
        await c.plugin(Drivers.default);
        await c.plugin(Mailbox.default);
        await c.plugin(Activation.default, options.activationConfig ?? {});
        await c.plugin({ inject: ['initializers', 'actors'], apply(ctx) { ctx.initializers.register({ id: 'seed', after: [], async initialize(context) { await context.database.transaction(context.writeLease, req('seed'), async (tx) => { await ctx.actors.create(tx, { id: 'a', attributes: { count: 0 } }); await ctx.actors.create(tx, { id: 'b', attributes: { count: 0 } }); }); return { operationIds: ['seed'] }; } }); } });
        const root = options.restore ? await c.history.activate() : await c.sqliteBootstrap.create({ operationId: 'create', runId, directory, input: {}, migrations: [Artifacts.artifactsMigration, Actors.actorsMigration, ...(options.migrations ?? [])], initialFrontier: { codec: options.codec ?? 'staged/1', value: '0', logicalTime: options.time ?? '0', completedCount: '0', pendingAcknowledgements: [] }, initialControl: { generation: 'g', controlSequence: '0', planId: 'plan' }, requiredInitializerIds: ['seed'] });
        const clock = await c.plugin(options.clock ?? Ticks.default);
        await c.plugin(options.source ?? Sequence.default, options.sourceConfig ?? { proposals: [] });
        await c.plugin(options.runtime ?? Runtime.default);
        await c.plugin(options.completion ?? Completion.default);
        await c.plugin(Checkpoint.default, { every: 3 });
        await c.plugin(Termination.default);
        await c.plugin(Views.default);
        const binding = await c.plugin(Binding.default, { generation: 'g' });
        await c.plugin(Runs.default);
        return { host, ctx: c, root, directory, binding, clock };
    }
    catch (error) {
        await host.close();
        throw error;
    }
}
const proposals = Array.from({ length: 4 }, (_, i) => ({ id: 'p' + i, planKind: 'staged/1', payload: { phases: [{ id: 'phase', activations: [{ id: 'a' + i, actorId: 'a', momentId: 'm' + i, source: 'agenda', driver: 'rule', input: null }] }], nextCursor: String(i + 1) } }));
test('PR06-T05 real root -> deterministic actor SQL -> every N checkpoint then final forced cut', async () => {
    const f = await fixture({ sourceConfig: { proposals } });
    try {
        await f.ctx.plugin({ inject: ['drivers', 'actors'], apply(ctx) { ctx.drivers.register('rule', { async activate(intent, context) { const operationId = 'write-' + intent.id; await context.database.transaction(context.writeLease, req(operationId), async (tx) => { const actor = await ctx.actors.get('a', tx); await ctx.actors.update(tx, { id: 'a', attributes: { count: actor.attributes.count + 1 } }); }); return { status: 'completed', value: null, evidence: { operationId }, requiredOperations: [operationId] }; } }); } });
        const result = await f.ctx.runs.run(new AbortController().signal);
        assert.equal(result.status, 'completed', result.reason);
        assert.equal(result.cut.frontier.completedCount, '4');
        assert.equal(result.cut.sequence, '2');
        assert.equal(result.cut.frontier.value, '4');
        const s = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
        assert.equal((await f.ctx.actors.get('a', s)).attributes.count, 4);
        await s.close();
        assert.equal((await f.ctx.history.status()).state, 'clean');
        console.log(JSON.stringify({ root: f.root.id, cut: result.cut, metrics: f.ctx.history.diagnostics() }));
    }
    finally {
        await f.host.close();
    }
});
test('PR06-T04 completion=true cannot cover a missing required SQL receipt; failed reason retained', async () => {
    const dishonest = { apply(ctx) { ctx.provide('completionPolicy', { evaluate() { return { complete: true, reason: 'override' }; } }); } };
    const f = await fixture({ sourceConfig: { proposals: [proposals[0]] }, completion: dishonest });
    try {
        await f.ctx.plugin({ inject: ['drivers'], apply(ctx) {
                ctx.drivers.register('rule', { async activate(intent, context) {
                        let error;
                        try {
                            await context.database.transaction(context.writeLease, req('required-but-failed'), tx => tx.execute({ sql: 'INSERT INTO missing_required_table VALUES(1)' }));
                        }
                        catch (e) {
                            error = String(e);
                        }
                        return { status: 'completed', requiredOperations: ['required-but-failed'], value: null, evidence: { error } };
                    } });
            } });
        const result = await f.ctx.runs.run(new AbortController().signal);
        assert.equal(result.status, 'failed');
        assert.match(result.reason, /RUN_REQUIRED_OPERATION/);
        assert.equal((await f.ctx.history.current()).id, f.root.id);
        assert.equal(f.ctx.executionFence.snapshot().state, 'halted');
        assert.match(f.ctx.executionFence.snapshot().reason, /RUN_REQUIRED_OPERATION/);
        assert.throws(() => f.ctx.runs.run(new AbortController().signal), /RUN_FAILED/);
    }
    finally {
        await f.host.close();
    }
});
test('PR06-T05 incomplete remains paused and final point does not advance', async () => {
    const f = await fixture({ sourceConfig: { proposals: [proposals[0]] } });
    try {
        await f.ctx.plugin({ inject: ['drivers'], apply(ctx) { ctx.drivers.register('rule', { async activate(intent, context) { await context.database.transaction(context.writeLease, req('partial'), tx => tx.execute({ sql: "UPDATE s0_actors SET attributes='{}' WHERE id='a'" })); return { status: 'incomplete', value: { remaining: ['required'] }, evidence: { reason: 'budget' } }; } }); } });
        const result = await f.ctx.runs.run(new AbortController().signal);
        assert.equal(result.status, 'paused');
        assert.equal(result.cut.id, f.root.id);
        assert.equal(result.recoveryRequired, true);
        assert.equal((await f.ctx.history.status()).state, 'dirty');
        await assert.rejects(f.ctx.runs.pauseAtBoundary(), /RUN_NO_BOUNDARY/);
    }
    finally {
        await f.host.close();
    }
});
test('PR06-T06 first failure revokes old lease before delayed driver writes and waits physical end', async () => {
    const p = { id: 'late', planKind: 'staged/1', payload: { phases: [{ id: 'parallel', activations: ['a', 'b'].map(actorId => ({ id: actorId, actorId, momentId: 'm', driver: 'rule', source: 'event', input: null })) }], nextCursor: '1' } };
    const f = await fixture({ sourceConfig: { proposals: [p] } });
    const other = Promise.withResolvers();
    const release = Promise.withResolvers();
    const cancelled = Promise.withResolvers();
    let lateError, finished = false;
    try {
        await f.ctx.plugin({ inject: ['drivers'], apply(ctx) {
                ctx.drivers.register('rule', { async activate(intent, context) {
                        if (intent.actorId === 'a') {
                            await other.promise;
                            throw Error('first required failure');
                        }
                        other.resolve();
                        context.signal.addEventListener('abort', () => cancelled.resolve(), { once: true });
                        await release.promise;
                        try {
                            await context.database.transaction(context.writeLease, req('late-write'), tx => tx.execute({ sql: 'UPDATE s0_actors SET attributes=attributes' }));
                        }
                        catch (error) {
                            lateError = String(error);
                        }
                        return { status: 'completed', value: null, evidence: { lateError } };
                    } });
            } });
        const running = f.ctx.runs.run(new AbortController().signal).then(result => { finished = true; return result; });
        await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(Error('cancel timeout ' + JSON.stringify({ tasks: f.ctx.tasks.snapshot(), runs: f.ctx.runs.diagnostics(), fence: f.ctx.executionFence.snapshot() }))), 45000); cancelled.promise.then(() => { clearTimeout(timer); resolve(); }); });
        assert.equal(finished, false);
        assert.equal(f.ctx.executionFence.snapshot().state, 'halted');
        release.resolve();
        const result = await running;
        assert.equal(result.status, 'failed');
        assert.match(result.reason, /first required failure/);
        assert.match(lateError, /FENCE_LEASE/);
        assert.equal((await f.ctx.history.current()).id, f.root.id);
        assert.equal((await f.ctx.database.probe('late-write')).status, 'not-committed');
    }
    finally {
        release.resolve();
        await f.host.close();
    }
});
const EventSource = await import(base + 'proposal-events/index.js');
const EventRuntime = await import(base + 'runtime-events/index.js');
const EventClock = await import(base + 'clock-event/index.js');
test('PR06-T02 event codec equal-time offsets retain precision and ack follows actual cut', async () => {
    const f = await fixture({ codec: 'events/1', time: { time: '0', offset: '0' }, source: EventSource.default, runtime: EventRuntime.default, clock: EventClock.default, migrations: [EventRuntime.eventsMigration] });
    try {
        let calls = [];
        await f.ctx.plugin({ inject: ['runtimeDriver', 'actors'], apply(ctx) { ctx.runtimeDriver.register({ id: 'inc/1', async execute(event, tx) { calls.push(event.id); const actor = await ctx.actors.get('a', tx); await ctx.actors.update(tx, { id: 'a', attributes: { count: actor.attributes.count + 1 } }); } }); } });
        for (const [id, offset] of [['e2', '9007199254740994'], ['e1', '9007199254740993']])
            await f.ctx.proposalSource.push({ id, offset, time: '9', payload: { handler: 'inc/1' } });
        f.ctx.proposalSource.end();
        const result = await f.ctx.runs.run(new AbortController().signal);
        assert.equal(result.status, 'completed', result.reason);
        assert.deepEqual(calls, ['e1', 'e2']);
        assert.equal(result.cut.frontier.value, '9007199254740994');
        assert.deepEqual(result.cut.frontier.logicalTime, { time: '9', offset: '9007199254740994' });
        assert.equal(result.cut.frontier.pendingAcknowledgements.length, 2);
        assert.equal((await f.ctx.proposalSource.waitProposal('0', new AbortController().signal)).kind, 'end');
        assert.equal((await f.ctx.history.current()).id, result.cut.id);
        await f.ctx.proposalSource.push({ id: 'e1', offset: '9007199254740993', time: '9', payload: { handler: 'inc/1' } });
        assert.equal(f.ctx.proposalSource.diagnostics().pending, 0);
    }
    finally {
        await f.host.close();
    }
});
test('PR06-T03 idle wait has no write lease and boundary pause cancels it', async () => {
    const f = await fixture({ codec: 'events/1', time: { time: '0', offset: '0' }, source: EventSource.default, runtime: EventRuntime.default, clock: EventClock.default, migrations: [EventRuntime.eventsMigration] });
    try {
        const waiting = Promise.withResolvers();
        const source = f.ctx.proposalSource;
        const wait = source.waitProposal.bind(source);
        source.waitProposal = (...args) => { const physical = wait(...args); waiting.resolve(); return physical; };
        const running = f.ctx.runs.run(new AbortController().signal);
        await waiting.promise;
        assert.equal(f.ctx.executionFence.snapshot().state, 'idle');
        const boundary = await f.ctx.runs.pauseAtBoundary();
        assert.equal((await running).status, 'paused');
        assert.equal(boundary.cut.id, f.root.id);
        assert.equal(f.ctx.executionFence.snapshot().state, 'sealed');
        await boundary.finalize({ kind: 'resume', generation: 'g', controlSequence: '0' });
        assert.equal(f.ctx.executionFence.snapshot().state, 'idle');
    }
    finally {
        await f.host.close();
    }
});
test('PR06-T07 one proposal activation budget spans all phases and preserves incomplete evidence', async () => {
    const p = { id: 'budget', planKind: 'staged/1', payload: { phases: [0, 1].map(i => ({ id: 'phase' + i, activations: [{ id: 'activation' + i, actorId: 'a', momentId: 'm' + i, driver: 'rule', source: 'agenda', input: i }] })), nextCursor: '1' } };
    const f = await fixture({ sourceConfig: { proposals: [p] }, activationConfig: { maxActivations: 1 } });
    try {
        let calls = 0;
        await f.ctx.plugin({ inject: ['drivers'], apply(ctx) { ctx.drivers.register('rule', { async activate() { calls++; return { status: 'completed', value: null, evidence: null }; } }); } });
        const result = await f.ctx.runs.run(new AbortController().signal);
        assert.equal(result.status, 'paused');
        assert.equal(calls, 1);
        assert.equal(result.cut.id, f.root.id);
        assert.equal(f.ctx.runs.diagnostics().advance.evidence.admissions[1].reason, 'ACTIVATION_BUDGET');
    }
    finally {
        await f.host.close();
    }
});
test('PR06 fixed generation view remains usable until holder release and rejects new acquire', async () => {
    const f = await fixture();
    let held;
    try {
        held = await f.ctx.generationViews.acquire('g');
        let retired = false;
        const retiring = f.clock.dispose().then(() => retired = true);
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(retired, false);
        await assert.rejects(f.ctx.generationViews.acquire('g'), /GENERATION_UNAVAILABLE/);
        assert.equal(held.value.clock.initial(), '0');
        assert.deepEqual(held.value.runtime.planKinds, ['staged/1']);
        await held.release();
        await retiring;
        assert.equal(retired, true);
    }
    finally {
        await held?.release();
        await f.host.close();
    }
});
test('PR06-T02 persisted event offset resumes from real independent history fork without replay', async () => {
    const options = { codec: 'events/1', time: { time: '0', offset: '0' }, source: EventSource.default, runtime: EventRuntime.default, clock: EventClock.default, migrations: [EventRuntime.eventsMigration] };
    const f = await fixture(options);
    let branch;
    async function register(ctx, calls) { await ctx.plugin({ inject: ['runtimeDriver', 'actors'], apply(c) { c.runtimeDriver.register({ id: 'inc/1', async execute(event, tx) { calls.push(event.id); const actor = await c.actors.get('a', tx); await c.actors.update(tx, { id: 'a', attributes: { count: actor.attributes.count + 1 } }); } }); } }); }
    const first = { id: 'e1', offset: '9007199254740993', time: '9', payload: { handler: 'inc/1' } };
    const second = { id: 'e2', offset: '9007199254740994', time: '9', payload: { handler: 'inc/1' } };
    try {
        await register(f.ctx, []);
        await f.ctx.proposalSource.push(first);
        f.ctx.proposalSource.end();
        const one = await f.ctx.runs.run(new AbortController().signal);
        assert.equal(one.status, 'completed', one.reason);
        const target = join(dirname(f.directory), 'pr06-fork-' + randomUUID());
        const fork = await f.ctx.history.fork({ operationId: 'fork', pointId: one.cut.id, targetRunId: 'branch', targetDirectory: target });
        assert.equal(fork.frontier.value, first.offset);
        branch = await fixture({ ...options, restore: true, directory: target, runId: 'branch' });
        const calls = [];
        await register(branch.ctx, calls);
        await branch.ctx.proposalSource.push(first);
        await branch.ctx.proposalSource.push(second);
        branch.ctx.proposalSource.end();
        const resumed = await branch.ctx.runs.run(new AbortController().signal);
        assert.equal(resumed.status, 'completed', resumed.reason);
        assert.deepEqual(calls, ['e2']);
        assert.equal(resumed.cut.frontier.completedCount, '2');
        const reader = await branch.ctx.database.snapshot({ maxAgeMs: 30000 });
        assert.equal((await branch.ctx.actors.get('a', reader)).attributes.count, 2);
        assert.equal((await reader.query({ sql: 'SELECT id FROM s0_runtime_events ORDER BY offset' }, { maxRows: 10, maxBytes: 4096 })).length, 2);
        await reader.close();
        console.log(JSON.stringify({ source: one.cut.id, fork: fork.id, resumed: resumed.cut.id, offset: resumed.cut.frontier.value }));
    }
    finally {
        await branch?.host.close();
        await f.host.close();
    }
});
