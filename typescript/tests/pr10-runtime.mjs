import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join, dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fixture, runtime, request } from './pr10-support.mjs';
import Repository, { measurementMigration } from '../.build/social/dist/measurement-repository/index.js';
import Measurements from '../.build/social/dist/measurements/index.js';
import Every from '../.build/social/dist/measurement-every/index.js';
import RoundRobin from '../.build/social/dist/round-robin/index.js';
test('PR10-T04 required final interview failure preserves settled observations and blocks final cut', async () => {
    const f = await fixture({
        migrations: [measurementMigration]
    });
    try {
        const c = f.ctx;
        await runtime(f, {
            sourceConfig: {
                proposals: [{
                        id: 'measure', planKind: 'staged/1', payload: {
                            phases: [{
                                    id: 'final', activations: [{
                                            id: 'final', actorId: 'a', momentId: 'end', driver: 'measurements', source: 'final', input: null
                                        }]
                                }], nextCursor: '1'
                        }
                    }]
            }
        });
        await c.plugin(Repository);
        await c.plugin(Measurements);
        await c.plugin(Every);
        let identity;
        await c.plugin({
            inject: ['measurements'], apply(ctx) {
                ctx.measurements.register({
                    id: 'interview-observation', kind: 'interview', required: true, async observe(context) {
                        identity = {
                            lineage: c.attempts.authority().lineage, operationId: context.operationId + '/measurement/interview-observation', attempt: 0
                        };
                        return {
                            value: {
                                opinion: '完整回答'
                            }, original: {
                                question: 'How?', response: '完整回答', trace: ['raw', 'retained']
                            }, source: {
                                provider: 'third-party-interview'
                            }
                        };
                    }
                });
                ctx.measurements.register({
                    id: 'required-final', kind: 'measurement', required: true, async observe() {
                        throw Error('required final measurement failed');
                    }
                });
            }
        });
        const result = await c.runs.run(new AbortController().signal);
        assert.equal(result.status, 'paused');
        assert.equal(result.reason, 'incomplete');
        assert.equal((await c.history.current()).id, f.root.id);
        assert.equal(result.recoveryRequired, true);
        assert.equal((await c.attempts.probe(identity)).record.result.observation.original.response, '完整回答');
        const view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        const records = await c.measurementRepository.page(view, {
            maxItems: 5, maxResponseBytes: 65536
        });
        assert.equal(records.total, '1');
        assert.deepEqual(records.items[0].original.trace, ['raw', 'retained']);
        await view.close();
    }
    finally {
        await f.host.close();
    }
});
test('PR10-T06/T05 real fork resumes persisted round-robin actor without reseeding', async () => {
    const f = await fixture();
    let branch;
    const calls = [];
    const install = async (c, observed) => c.plugin({
        inject: ['drivers'], apply(ctx) {
            ctx.drivers.register('audit-turn', {
                async activate(intent, context) {
                    observed.push(intent.actorId);
                    const operationId = context.operationId + '/turn';
                    await context.database.transaction(context.writeLease, request(operationId), tx => c.actors.update(tx, {
                        id: intent.actorId, attributes: {
                            visited: true
                        }
                    }));
                    return {
                        status: 'completed', value: {
                            actorId: intent.actorId
                        }, evidence: {
                            operationId
                        }, requiredOperations: [operationId]
                    };
                }
            });
        }
    });
    try {
        await runtime(f, {
            source: RoundRobin, sourceConfig: {
                actors: ['a', 'b', 'c'], rounds: 1, driver: 'audit-turn'
            }, termination: {
                apply(ctx) {
                    ctx.provide('terminationPolicy', {
                        evaluate() {
                            return {
                                action: 'stop', reason: 'one turn requested'
                            };
                        }
                    });
                }
            }
        });
        await install(f.ctx, calls);
        const first = await f.ctx.runs.run(new AbortController().signal);
        assert.equal(first.status, 'completed');
        assert.deepEqual(calls, ['a']);
        assert.equal(first.cut.frontier.value, '1');
        const target = join(dirname(f.directory), 'pr10-fork-' + randomUUID());
        await f.ctx.history.fork({
            operationId: 'fork', pointId: first.cut.id, targetRunId: 'branch', targetDirectory: target
        });
        branch = await fixture({
            restore: true, directory: target, runId: 'branch', attemptDirectory: f.attemptDirectory
        });
        await runtime(branch, {
            source: RoundRobin, sourceConfig: {
                actors: ['a', 'b', 'c'], rounds: 1, driver: 'audit-turn'
            }
        });
        const resumed = [];
        await install(branch.ctx, resumed);
        const result = await branch.ctx.runs.run(new AbortController().signal);
        assert.equal(result.status, 'completed', result.reason);
        assert.deepEqual(resumed, ['b', 'c']);
        assert.equal(result.cut.frontier.value, '3');
        const view = await branch.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal(String((await view.query({
            sql: 'SELECT count(*) AS n FROM social_members'
        }, {
            maxRows: 1, maxBytes: 1024
        }))[0].n), '3');
        await view.close();
    }
    finally {
        await branch?.host.close();
        await f.host.close();
    }
});
test('PR10-T04 oversized interview response remains original evidence while final measurement is incomplete', async () => {
    const f = await fixture({
        migrations: [measurementMigration]
    });
    let identity;
    const original = '完整外部响应'.repeat(4000);
    try {
        const c = f.ctx;
        await runtime(f, {
            sourceConfig: {
                proposals: [{
                        id: 'oversized', planKind: 'staged/1', payload: {
                            phases: [{
                                    id: 'final', activations: [{
                                            id: 'final', actorId: 'a', momentId: 'end', driver: 'measurements', source: 'final', input: null
                                        }]
                                }], nextCursor: '1'
                        }
                    }]
            }
        });
        await c.plugin(Repository);
        await c.plugin(Measurements, {
            maxResultBytes: 1024
        });
        await c.plugin(Every);
        await c.plugin({
            inject: ['measurements'], apply(ctx) {
                ctx.measurements.register({
                    id: 'large-interview', kind: 'interview', required: true, async observe(context) {
                        identity = {
                            lineage: c.attempts.authority().lineage, operationId: context.operationId + '/measurement/large-interview', attempt: 0
                        };
                        return {
                            value: {
                                answer: '完整答案'
                            }, original: {
                                response: original
                            }, source: {
                                provider: 'third-party-interview'
                            }
                        };
                    }
                });
            }
        });
        const result = await c.runs.run(new AbortController().signal);
        assert.equal(result.status, 'paused');
        assert.equal(result.reason, 'incomplete');
        assert.equal((await c.history.current()).id, f.root.id);
        const record = (await c.attempts.probe(identity)).record;
        assert.equal(record.status, 'settled', JSON.stringify(c.runs.diagnostics()));
        const chunks = [];
        for await (const chunk of c.attempts.readArtifact(record.result.evidence))
            chunks.push(Buffer.from(chunk));
        const saved = JSON.parse(Buffer.concat(chunks));
        assert.equal(saved.observation.original.response, original);
    }
    finally {
        await f.host.close();
    }
});
