import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, runtime } from './pr10-support.mjs';
import { perceptionMigration } from '../.build/core/dist/perception/index.js';
import { eventsMigration } from '../.build/core/dist/runtime-events/index.js';
import { actorToolsMigration } from '../.build/core/dist/actor-tools/index.js';
import Workspace, { workspaceMigration } from '../.build/extras/dist/workspace/index.js';
import { ReplayProvider } from '../.build/core/dist/model-replay/provider.js';
const core = async (name) => (await import('../.build/core/dist/' + name + '/index.js')).default;
const social = async (name) => (await import('../.build/social/dist/' + name + '/index.js')).default;
const usage = {
    input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: {
        input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0
    }
};
const assistant = (content, stopReason = 'stop') => ({
    role: 'assistant', content, api: 'openai-completions', provider: 'replay', model: 'social', usage, stopReason, timestamp: 1
});
test('PR10-T20 actual event and DriverLLM consume Social percepts and commit native actions', async () => {
    const source = {
        consumer: 'pr10-native-llm'
    };
    const f = await fixture({
        event: true, migrations: [eventsMigration, perceptionMigration(source), actorToolsMigration(source), workspaceMigration(source)]
    });
    const observed = [], original = '供真实pi主体读取的完整社会事件原文与标签。';
    class ObservedReplay extends ReplayProvider {
        async prepare(request, context) {
            const held = await super.prepare(request, context);
            return {
                value: {
                    ...held.value, streamFn: (model, input, options) => {
                        observed.push(JSON.stringify(input.messages));
                        return held.value.streamFn(model, input, options);
                    }
                }, release: () => held.release()
            };
        }
    }
    try {
        const c = f.ctx;
        await runtime(f, {
            event: true, sourceConfig: {
                source: 'llm-events'
            }
        });
        await c.plugin(Workspace);
        for (const name of ['perception', 'exploration', 'mount-workspace', 'mount-results', 'mount-world', 'plain', 'prompts', 'mount-context', 'exploration-shell', 'actor-tools', 'model-providers', 'agent-runtimes', 'agent-runtime-pi', 'inference-defaults'])
            await c.plugin(await core(name), name === 'prompts' ? {
                sections: [{
                        id: 'social', text: 'Read the original social material and execute the requested social action.'
                    }]
            } : {});
        for (const name of ['social-ranking', 'social-recommendations', 'social-perception'])
            await c.plugin(await social(name));
        await c.plugin({
            inject: ['modelProviders', 'eventHandlers', 'socialRepository'], apply(ctx) {
                ctx.modelProviders.register('replay/v1', new ObservedReplay({
                    social: [
                        assistant([{
                                type: 'toolCall', id: 'publish-social', name: 'act', arguments: {
                                    action: 'social.publish', input: {
                                        id: 'llm-social-post', content: '实际pi行动保存的完整回复', tags: ['native-llm'], metadata: {
                                            original: true
                                        }
                                    }
                                }
                            }], 'toolUse'),
                        assistant([{
                                type: 'text', text: '已保留原文并完成社会行动。'
                            }])
                    ]
                }));
                ctx.eventHandlers.register({
                    id: 'social-llm-event',
                    execute: (_event, tx) => ctx.socialRepository.publish(tx, {
                        id: 'llm-seed', authorId: 'a', content: original, tags: ['original'], createdTime: '42'
                    }, ['b']).then(() => {
                    }),
                    activations: () => [{
                            id: 'social-llm', actorId: 'b', momentId: 'llm-social', driver: 'llm/v1', source: 'actual-event', input: 'Read the social event and publish the requested reply.'
                        }]
                });
            }
        });
        await c.plugin(await core('driver-llm'), {
            provider: 'replay/v1', model: 'social', perceptions: ['social/profile', 'social/feed', 'social/notifications', 'social/recommendations'], requiredActions: [{
                    name: 'social.publish', minimumCompleted: 1
                }], maxRequests: 3, maxToolCalls: 1
        });
        c.proposalSource.push({
            id: 'llm-event', offset: '1', time: '43', payload: {
                handler: 'social-llm-event'
            }
        });
        c.proposalSource.end();
        const result = await c.runs.run(new AbortController().signal);
        assert.equal(result.status, 'completed', JSON.stringify(c.runs.diagnostics()));
        assert.equal(observed.length, 2);
        assert.ok(observed[0].includes(original));
        assert.ok(observed[0].includes('social.recommendation'));
        const view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        const post = await c.socialRepository.get('llm-social-post', view);
        assert.equal(post.authorId, 'b');
        assert.equal(post.createdTime, '43');
        assert.deepEqual(post.metadata, {
            original: true
        });
        const thread = await c.threads.open('b', 'llm-social', view);
        assert.ok(Number(thread.messageCount) > 3);
        const [row] = await view.query({
            sql: "SELECT material FROM perception_material WHERE source_id='social/recommendations'"
        }, {
            maxRows: 1, maxBytes: 65536
        });
        assert.equal(JSON.parse(row.material).value.ranking.components.timeScore, Math.exp(-1 / 12));
        await view.close();
        assert.ok(result.cut.requiredOperations.length > 3);
        for (const operation of result.cut.requiredOperations)
            assert.equal((await c.database.probe(operation)).status, 'committed');
    }
    finally {
        await f.host.close();
    }
});
