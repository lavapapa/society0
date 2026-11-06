import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir, chmod } from 'node:fs/promises';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { fixture, activation, request, runtime } from './pr10-support.mjs';
import { perceptionMigration } from '../.build/core/dist/perception/index.js';
import { eventsMigration } from '../.build/core/dist/runtime-events/index.js';
import { actorToolsMigration } from '../.build/core/dist/actor-tools/index.js';
import Workspace, { workspaceMigration } from '../.build/extras/dist/workspace/index.js';

const arg = name => { const index = process.argv.indexOf(name); return index > 0 ? process.argv[index + 1] : undefined; };
const evidenceDir = arg('--evidence-dir'), gatewayEnv = arg('--gateway-env');
if (!process.argv.includes('--allow-local-model') || !evidenceDir || !gatewayEnv) {
    console.error('Usage: node tests/redirect-social-live.mjs --allow-local-model --gateway-env <env> --evidence-dir <dir>');
    process.exit(2);
}
const core = async name => (await import('../.build/core/dist/' + name + '/index.js')).default;
const social = async name => (await import('../.build/social/dist/' + name + '/index.js')).default;
const Random = await core('random');
const base = { name: 'Authorized local Qwen', api: 'openai-completions', provider: 'local-gateway', baseUrl: 'http://127.0.0.1:4000/v1', reasoning: true, input: ['text'], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 8192, maxTokens: 256, compat: { supportsStore: false, supportsDeveloperRole: false, supportsReasoningEffort: false, maxTokensField: 'max_tokens', thinkingFormat: 'qwen-chat-template' } };
const model = { ...base, id: 'Qwen3.6-35B-A3B-FP8' };
const line = (await readFile(gatewayEnv, 'utf8')).split(/\r?\n/).find(item => item.startsWith('LITELLM_MASTER_KEY='));
assert.ok(line, 'LITELLM_MASTER_KEY missing');
const secret = line.slice('LITELLM_MASTER_KEY='.length).trim();
assert.ok(process.env.TMPDIR, 'TMPDIR required');
const credentials = join(process.env.TMPDIR, 'redirect-social-gateway-credentials.json');
await writeFile(credentials, JSON.stringify({ 'local-gateway': { type: 'api_key', key: secret } }), { mode: 0o600 });
await chmod(credentials, 0o600);
await mkdir(evidenceDir, { recursive: true });

let requests = 0;
const originalFetch = globalThis.fetch;
globalThis.fetch = (input, init) => {
    if (String(input).includes('127.0.0.1:4000')) requests++;
    return originalFetch(input, init);
};

async function boot(task) {
    const f = await fixture({
        actors: [{ id: 'a', attributes: { name: '甲' } }, { id: 'b', attributes: { name: '乙' } }],
        edges: [{ from: 'b', to: 'a' }],
        social: { pairingGroups: [['a', 'b']], pairingSessionMinutes: 10 },
        event: true,
        migrations: [eventsMigration, perceptionMigration({ consumer: 'redirect-social-live' }), actorToolsMigration({ consumer: 'redirect-social-live' }), workspaceMigration({ consumer: 'redirect-social-live' })]
    });
    const c = f.ctx;
    await c.plugin(Random, { seed: 'redirect-social-live' });
    await runtime(f, { event: true, sourceConfig: { source: 'llm-events' } });
    await c.plugin(Workspace);
    for (const name of ['perception', 'exploration', 'mount-workspace', 'mount-results', 'mount-world', 'plain', 'prompts', 'mount-context', 'exploration-shell', 'actor-tools', 'model-providers', 'model-credentials', 'model-pi', 'agent-runtimes', 'agent-runtime-pi', 'inference-defaults'])
        await c.plugin(await core(name), name === 'prompts' ? {
            sections: [{ id: 'social-live', text: 'You are a social actor. You must invoke the named tool with a JSON object argument. Never satisfy the instruction by printing JSON text.' }],
            tools: task === 'publish' ? [{ id: 'publish', name: 'publish', action: 'social.publish' }] : [{ id: 'message', name: 'message', action: 'social.sendPartnerMessage' }]
        } : name === 'model-credentials' ? {
            stores: [{ reference: 'authorized-local', path: credentials }]
        } : name === 'model-pi' ? {
            name: 'pi/v1', providerId: 'local-gateway', credential: 'authorized-local', models: [model], maxTokens: 256, timeoutMs: 120000
        } : {});
    for (const name of ['social-ranking', 'social-recommendations', 'social-perception'])
        await c.plugin(await social(name), name === 'social-recommendations' ? {
            sources: ['social/active-pool/v1'], fullScanUntil: 3, recentKeepCount: 2, topEngagementKeepCount: 2, minLifetimeTicks: 10
        } : {});
    await c.plugin({
        inject: ['eventHandlers'], apply(ctx) {
            ctx.eventHandlers.register({
                id: 'redirect-social-live',
                execute: async (_event, tx) => {
                    await c.social.startRound(tx, 1);
                    for (const [id, time] of [['seed-1', '1'], ['seed-2', '2'], ['seed-3', '3']])
                        await c.socialRepository.publish(tx, { id, authorId: 'b', content: id, tags: [], createdTime: time }, []);
                },
                activations: () => [task === 'publish' ? {
                    id: 'live-publish', actorId: 'a', momentId: 'live-publish', driver: 'llm/v1', source: 'redirect-social-live',
                    input: 'Invoke the publish tool exactly once with the JSON object {"content":"真实模型发布 #topic 原文"}. The action must be executed; printing the object is not enough.'
                } : {
                    id: 'live-message', actorId: 'a', momentId: 'live-message', driver: 'llm/v1', source: 'redirect-social-live',
                    input: 'Invoke the message tool exactly once with the JSON object {"content":"真实模型伙伴消息 #live"}. The action must be executed; printing the object is not enough.'
                }]
            });
        }
    });
    await c.plugin(await core('driver-llm'), {
        provider: 'pi/v1', model: model.id, reasoning: 'off', perceptions: ['social/profile', 'social/notifications', 'social/feed'],
        maxRequests: 4, maxToolCalls: 1, maxPageBytes: 65536, maxPageItems: 16
    });
    return f;
}

async function execute(task) {
    const f = await boot(task);
    let lease;
    try {
        const c = f.ctx, started = performance.now();
        c.proposalSource.push({ id: 'live-1', offset: '1', time: '5', payload: { handler: 'redirect-social-live' } });
        c.proposalSource.end();
        const result = await c.runs.run(new AbortController().signal);
        const durationMs = Math.round(performance.now() - started);
        const view = await c.database.snapshot({ maxAgeMs: 30000 });
        let posts, receipts;
        try {
            posts = (await view.query({ sql: 'SELECT id,author_id,content,created_time FROM social_posts ORDER BY ordinal' }, { maxRows: 100, maxBytes: 65536 })).map(row => ({ id: String(row.id), authorId: String(row.author_id), content: String(row.content), createdTime: String(row.created_time) }));
            receipts = (await view.query({ sql: 'SELECT action,actor_id FROM action_receipts ORDER BY operation_id' }, { maxRows: 100, maxBytes: 65536 })).map(row => ({ action: String(row.action), actorId: String(row.actor_id) }));
        }
        finally { await view.close(); }
        const report = { task, directory: f.directory, status: result.status, durationMs, requests, posts, receipts };
        if (result.status !== 'completed') {
            report.diagnostics = c.runs.diagnostics();
            await writeFile(join(evidenceDir, task + '-failure.json'), JSON.stringify(report, null, 2));
            assert.fail(JSON.stringify(report));
        }
        lease = await c.executionFence.reserve({ id: 'live-verify-' + task });
        if (task === 'publish') {
            const published = posts.find(post => post.authorId === 'a' && post.content.includes('#topic'));
            assert.ok(published, JSON.stringify(posts));
            const intervention = (await c.database.transaction(lease.lease, request('live-intervene'), tx => c.social.intervene(tx, {
                time: '6', targetHashtag: '#topic', interventionRate: 1, tagToApply: 'flagged'
            }))).value;
            assert.ok(intervention.flaggedPostIds.includes(published.id));
            const prepared = await c.recommendations.prepare({ actorId: 'b', momentId: 'live-prepare', query: { time: '6' } }, { maxItems: 32, maxBytes: 262144 }, activation(f, lease, 'live-prepare', 'b'));
            assert.equal(prepared.status, 'ready');
            const poolIds = prepared.input.items.map(item => item.id);
            assert.ok(poolIds.includes(published.id), JSON.stringify(poolIds));
            const ranked = await c.recommendations.preview(prepared.input, 'chronological/v1', { maxItems: 16, maxResponseBytes: 131072 });
            await c.social.afterTick();
            report.b03 = { postId: published.id, intervention: intervention.flaggedPostIds, totalPosts: intervention.totalPosts };
            report.b04 = { poolIds, ranked: ranked.items.map(item => item.id), afterTick: true };
        }
        else {
            const conversationView = await c.database.snapshot({ maxAgeMs: 30000 });
            let conversation;
            try { conversation = await c.social.conversation(conversationView, 'b', { maxItems: 10, maxResponseBytes: 65536 }); }
            finally { await conversationView.close(); }
            assert.equal(conversation.total, '1');
            assert.equal(conversation.items[0].sender, 'a');
            assert.ok(conversation.items[0].content.includes('#live'));
            report.b02 = { partner: conversation.items[0].sender, content: conversation.items[0].content, messageId: conversation.items[0].id };
        }
        await writeFile(join(evidenceDir, task + '.json'), JSON.stringify(report, null, 2));
        console.log(JSON.stringify({ task, status: report.status, durationMs, requests, b03: report.b03, b04: report.b04, b02: report.b02 }, null, 2));
    }
    finally {
        await lease?.release().catch(() => {});
        await f.host.close();
    }
}

try {
    await execute('publish');
    await execute('message');
    await writeFile(join(evidenceDir, 'summary.json'), JSON.stringify({ model: model.id, requests, at: new Date().toISOString() }, null, 2));
}
finally { globalThis.fetch = originalFetch; }
