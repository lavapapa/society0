import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, runtime, activation, request } from './pr10-support.mjs';
import Rankings from '../.build/social/dist/social-ranking/index.js';
import Recommendations from '../.build/social/dist/social-recommendations/index.js';
import Comparison from '../.build/social/dist/social-measurement/index.js';
import { propagationSlot } from '../.build/social/dist/social/index.js';
import Rule, { ruleHandlerSlot } from '../.build/core/dist/driver-rule/index.js';
import Perceptions, { perceptionMigration } from '../.build/core/dist/perception/index.js';
test('PR10-T10 expired comparison lease leaves SQL revision unchanged', async () => {
    const f = await fixture({
        social: false
    });
    let r;
    try {
        const c = f.ctx;
        for (const plugin of [Rankings, Recommendations, Comparison])
            await c.plugin(plugin);
        r = await c.executionFence.reserve({
            id: 'comparison-expired'
        });
        const context = activation(f, r, 'expired-comparison');
        const input = {
            id: 'bounded-input', runId: f.runId, scope: {
                actorId: 'a', momentId: 'm', query: {
                    time: '0'
                }
            }, versions: [], items: [], maxBytes: 4096
        };
        const revision = await c.database.readRevision();
        r.cancel(Error('expired comparison lease'));
        await assert.rejects(c.recommendationMeasurement.compare(input, ['chronological/v1', 'weighted/v1'], r.lease, context), /expired comparison lease|FENCE|lease|cancel/i);
        assert.equal(await c.database.readRevision(), revision);
    }
    finally {
        await r?.release();
        await f.host.close();
    }
});
test('PR10-T02/PR07-T03 actual staged rule propagation failure leaves complete point unchanged', async () => {
    const f = await fixture({
        propagation: 'fixture/fail', migrations: [perceptionMigration({
                package: '@society0/core', entry: 'perception'
            })]
    });
    try {
        const c = f.ctx;
        await runtime(f, {
            sourceConfig: {
                proposals: [{
                        id: 'fail-publish', planKind: 'staged/1', payload: {
                            phases: [{
                                    id: 'one', activations: [{
                                            id: 'fail', actorId: 'a', momentId: 'fail', driver: 'rule', source: 'propagation-failure', input: null
                                        }]
                                }], nextCursor: '1'
                        }
                    }]
            }
        });
        await c.plugin(Perceptions);
        await c.plugin(Rule, {
            handler: 'failure-rule', sources: []
        });
        await c.plugin({
            inject: ['slots', 'actions'], apply(ctx) {
                ctx.slots.contribute(propagationSlot, {
                    id: 'fixture/fail', value: {
                        async recipients() {
                            throw Error('third-party propagation failure');
                        }
                    }
                });
                ctx.slots.contribute(ruleHandlerSlot, {
                    id: 'failure-rule', value: {
                        async activate(_input, context) {
                            await ctx.actions.execute('social.publish', {
                                id: 'must-not-exist', content: '完整但未成功投递的原文'
                            }, context);
                            return {
                                status: 'completed', value: null, evidence: {}
                            };
                        }
                    }
                });
            }
        });
        const result = await c.runs.run(new AbortController().signal);
        assert.equal(result.status, 'failed');
        assert.match(result.reason, /third-party propagation failure/);
        assert.equal((await c.history.current()).id, f.root.id);
        const view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal(await c.socialRepository.get('must-not-exist', view), undefined);
        await view.close();
    }
    finally {
        await f.host.close();
    }
});
test('PR06 actual receipt count determines frontier transaction budget', async () => {
    const f = await fixture({
        social: false
    });
    try {
        const c = f.ctx;
        await runtime(f, {
            sourceConfig: {
                proposals: [{
                        id: 'many-receipts', planKind: 'staged/1', payload: {
                            phases: [{
                                    id: 'one', activations: [{
                                            id: 'write-many', actorId: 'a', momentId: 'many', driver: 'many-writes', source: 'receipt-budget', input: null
                                        }]
                                }], nextCursor: '1'
                        }
                    }]
            }
        });
        await c.plugin({
            inject: ['drivers', 'actors'],
            apply(ctx) {
                ctx.drivers.register('many-writes', {
                    async activate(_intent, context) {
                        const requiredOperations = [];
                        for (let i = 0; i < 65; i++) {
                            const id = context.operationId + '/write/' + i;
                            await context.database.transaction(context.writeLease, request(id), tx => ctx.actors.update(tx, {
                                id: 'a', attributes: {
                                    written: i + 1
                                }
                            }));
                            requiredOperations.push(id);
                        }
                        return {
                            status: 'completed', value: null, evidence: {}, requiredOperations
                        };
                    }
                });
            }
        });
        const result = await c.runs.run(new AbortController().signal);
        assert.equal(result.status, 'completed', JSON.stringify(c.runs.diagnostics()));
        assert.equal(result.cut.requiredOperations.length, 66);
    }
    finally {
        await f.host.close();
    }
});
