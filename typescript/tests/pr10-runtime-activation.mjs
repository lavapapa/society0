import { createServer } from 'node:http';
import { once } from 'node:events';
import { join, dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import MeasurementRepository, { measurementMigration } from '../.build/social/dist/measurement-repository/index.js';
import Measurements from '../.build/social/dist/measurements/index.js';
import Every from '../.build/social/dist/measurement-every/index.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, runtime } from './pr10-support.mjs';
import Rule, { ruleHandlerSlot } from '../.build/core/dist/driver-rule/index.js';
import Perceptions, { perceptionMigration } from '../.build/core/dist/perception/index.js';
import { eventsMigration } from '../.build/core/dist/runtime-events/index.js';
import Rankings from '../.build/social/dist/social-ranking/index.js';
import Recommendations from '../.build/social/dist/social-recommendations/index.js';
import SocialPerception from '../.build/social/dist/social-perception/index.js';
const perception = perceptionMigration({
    package: '@society0/core', entry: 'perception'
});
async function rule(c, handler, sources = [], inject = []) {
    for (const plugin of [Perceptions, Rankings, Recommendations, SocialPerception])
        await c.plugin(plugin);
    await c.plugin(Rule, {
        handler: 'actual-social-rule', sources
    });
    await c.plugin({
        inject: ['slots', 'actions', ...inject], apply(ctx) {
            ctx.slots.contribute(ruleHandlerSlot, {
                id: 'actual-social-rule', value: {
                    activate: (input, context) => handler(ctx, input, context)
                }
            });
        }
    });
}
test('PR06/PR07-T03 two actors have independent actions interviews and stable replay addresses', async () => {
    const physical = [];
    const server = createServer(async (req, res) => {
        let raw = '';
        for await (const chunk of req)
            raw += chunk;
        const body = JSON.parse(raw);
        physical.push(body);
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify({
            answer: body.actorId, originalRequest: body
        }));
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    let f, branch;
    const proposals = [{
            id: 'two-actors', planKind: 'staged/1', payload: {
                phases: [{
                        id: 'together', activations: ['a', 'b'].map(actorId => ({
                            id: 'publish-' + actorId, actorId, momentId: 'shared', driver: 'rule', source: 'independent-identities', input: null
                        }))
                    }], nextCursor: '1'
            }
        }];
    async function install(environment, operations, receipts) {
        const c = environment.ctx;
        await runtime(environment, {
            sourceConfig: {
                proposals
            }
        });
        for (const plugin of [MeasurementRepository, Measurements, Every])
            await c.plugin(plugin);
        await c.plugin({
            inject: ['measurements'], apply(ctx) {
                ctx.measurements.register({
                    id: 'same-interview', kind: 'interview', required: true, async observe(context) {
                        const original = await (await fetch('http://127.0.0.1:' + server.address().port, {
                            method: 'POST', body: JSON.stringify({
                                actorId: context.actorId, momentId: context.momentId, operationId: context.operationId
                            }), signal: context.signal
                        })).json();
                        return {
                            value: original.answer, original, source: {
                                provider: 'actual-local-http-interview'
                            }
                        };
                    }
                });
            }
        });
        await rule(c, async (ctx, _input, context) => {
            operations.push(context.operationId);
            const receipt = await ctx.actions.execute('social.publish', {
                id: 'post-' + context.actorId, content: '完整主体原文 ' + context.actorId
            }, context);
            receipts.push(receipt.operationId);
            const measurement = await ctx.measurements.collect(context);
            return {
                ...measurement, requiredOperations: [receipt.operationId, ...measurement.requiredOperations]
            };
        }, [], ['measurements']);
    }
    try {
        f = await fixture({
            migrations: [perception, measurementMigration]
        });
        const c = f.ctx, operations = [], receipts = [];
        await install(f, operations, receipts);
        const result = await c.runs.run(new AbortController().signal);
        assert.equal(result.status, 'completed', JSON.stringify(c.runs.diagnostics()));
        assert.equal(new Set(operations).size, 2);
        assert.equal(new Set(receipts).size, 2);
        assert.equal(physical.length, 2);
        for (const id of receipts)
            assert.ok(result.cut.requiredOperations.includes(id));
        let view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        for (const id of ['a', 'b'])
            assert.equal((await c.socialRepository.get('post-' + id, view)).authorId, id);
        await view.close();
        const directory = join(dirname(f.directory), 'pr10-identities-' + randomUUID());
        await c.history.fork({
            operationId: 'identity-replay', pointId: f.root.id, targetRunId: 'identity-branch', targetDirectory: directory
        });
        branch = await fixture({
            restore: true, directory, runId: 'identity-branch', attemptDirectory: f.attemptDirectory
        });
        const repeated = [], repeatedReceipts = [];
        await install(branch, repeated, repeatedReceipts);
        const replay = await branch.ctx.runs.run(new AbortController().signal);
        assert.equal(replay.status, 'completed', JSON.stringify(branch.ctx.runs.diagnostics()));
        assert.deepEqual(repeated.sort(), operations.sort());
        assert.equal(physical.length, 2);
        view = await branch.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        const measurements = await branch.ctx.measurementRepository.page(view, {
            maxItems: 10, maxResponseBytes: 65536
        });
        assert.deepEqual(measurements.items.map(item => [item.actorId, item.value.value]).sort(), [['a', 'a'], ['b', 'b']]);
        await view.close();
    }
    finally {
        await branch?.host.close();
        await f?.host.close();
        server.close();
        await once(server, 'close');
    }
});
async function eventFixture(handler, sources = ['social/feed']) {
    const f = await fixture({
        event: true, migrations: [perception, eventsMigration]
    });
    const c = f.ctx;
    await runtime(f, {
        event: true
    });
    await rule(c, handler, sources);
    f.eventOwner = await c.plugin({
        inject: ['eventHandlers', 'socialRepository'], apply(ctx) {
            f.retireEvent = ctx.eventHandlers.register({
                id: 'event-to-rule', async execute(event, tx) {
                    await ctx.socialRepository.publish(tx, {
                        id: 'event-seed', authorId: 'a', content: '事件事务中的完整原文', createdTime: String(BigInt(event.time) - 1n)
                    }, ['c']);
                }, activations(event) {
                    return [{
                            id: event.id, actorId: 'c', momentId: 'event-conversation', driver: 'rule', source: 'event:' + event.id, input: event.payload
                        }];
                }
            });
        }
    });
    await c.proposalSource.push({
        id: 'event-one', offset: '1', time: '9007199254740993', payload: {
            handler: 'event-to-rule'
        }
    });
    c.proposalSource.end();
    return f;
}
test('PR07-T03/PR10-T20 actual event SQL then Social rule perception and action completes', async () => {
    const f = await eventFixture(async (ctx, input, context) => {
        assert.ok(input.threadId);
        for (const [index, logicalTime] of [9007199254740992, {
                calendar: 'unsupported'
            }].entries()) {
            await assert.rejects(ctx.actions.execute('social.publish', {
                id: 'invalid-clock-' + index, content: 'invalid clock'
            }, {
                ...context, operationId: context.operationId + '/invalid-clock/' + index, logicalTime
            }), /SOCIAL_TIME/);
        }
        const receipt = await ctx.actions.execute('social.publish', {
            id: 'event-rule-post', content: '由真实规则主体响应事件'
        }, context);
        return {
            status: 'completed', value: null, evidence: {}, requiredOperations: [receipt.operationId]
        };
    }, ['social/feed', 'social/recommendations']);
    try {
        const c = f.ctx, result = await c.runs.run(new AbortController().signal);
        assert.equal(result.status, 'completed', JSON.stringify(c.runs.diagnostics()));
        assert.equal(result.cut.frontier.codec, 'events/1');
        assert.equal(result.cut.frontier.value, '1');
        const view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal((await c.socialRepository.get('event-rule-post', view)).createdTime, '9007199254740993');
        assert.equal((await view.query({
            sql: "SELECT count(*) AS n FROM perception_material WHERE source_id='social/feed'"
        }, {
            maxRows: 1, maxBytes: 1024
        }))[0].n, 1n);
        const [recommended] = await view.query({
            sql: "SELECT material FROM perception_material WHERE source_id='social/recommendations'"
        }, {
            maxRows: 1, maxBytes: 65536
        });
        assert.equal(JSON.parse(recommended.material).value.ranking.components.timeScore, Math.exp(-1 / 12));
        await view.close();
        for (const id of result.cut.requiredOperations)
            assert.equal((await c.database.probe(id)).status, 'committed');
    }
    finally {
        await f.host.close();
    }
});
test('PR06 events commit followed by incomplete rule leaves complete point unchanged', async () => {
    const f = await eventFixture(async () => ({
        status: 'incomplete', value: null, evidence: {
            reason: 'required decision failed'
        }
    }));
    try {
        const c = f.ctx, result = await c.runs.run(new AbortController().signal);
        assert.equal(result.status, 'paused');
        assert.equal(result.reason, 'incomplete');
        assert.equal(result.recoveryRequired, true);
        assert.equal((await c.history.current()).id, f.root.id);
        const view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal((await c.socialRepository.get('event-seed', view)).content, '事件事务中的完整原文');
        await view.close();
    }
    finally {
        await f.host.close();
    }
});
test('PR06 event cancellation joins the actual rule physical completion', async () => {
    const entered = Promise.withResolvers(), finish = Promise.withResolvers();
    const f = await eventFixture(async () => {
        entered.resolve();
        await finish.promise;
        return {
            status: 'completed', value: null, evidence: {}
        };
    });
    try {
        const cancel = new AbortController();
        let settled = false;
        const running = f.ctx.runs.run(cancel.signal).then(result => {
            settled = true;
            return result;
        });
        await entered.promise;
        let retired = false;
        const retiring = f.eventOwner.dispose().then(() => {
            retired = true;
        });
        cancel.abort(Error('event cancellation'));
        await new Promise(setImmediate);
        assert.equal(settled, false);
        assert.equal(retired, false);
        finish.resolve();
        const result = await running;
        await retiring;
        assert.equal(retired, true);
        assert.equal(result.status, 'failed');
        assert.equal((await f.ctx.history.current()).id, f.root.id);
    }
    finally {
        finish.resolve();
        await f.host.close();
    }
});
