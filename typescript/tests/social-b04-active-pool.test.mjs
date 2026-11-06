import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, activation, request } from './pr10-support.mjs';
import Rankings from '../.build/social/dist/social-ranking/index.js';
import Recommendations from '../.build/social/dist/social-recommendations/index.js';

const poolRequest = { maxItems: 10, maxResponseBytes: 131072 };

async function seed(f) {
    const r = await f.ctx.executionFence.reserve({ id: 'b04' });
    await f.ctx.database.transaction(r.lease, request('b04-seed'), async tx => {
        for (const [id, time] of [['p1', '1'], ['p2', '2'], ['p3', '3'], ['p4', '4'], ['p5', '5']])
            await f.ctx.socialRepository.publish(tx, { id, authorId: 'a', content: id, tags: [], createdTime: time }, []);
        for (const actor of ['b', 'c'])
            await f.ctx.socialRepository.react(tx, { id: 'like-p2-' + actor, actorId: actor, postId: 'p2', kind: 'like', time: '5' });
        await f.ctx.socialRepository.react(tx, { id: 'like-p4', actorId: 'b', postId: 'p4', kind: 'like', time: '5' });
    });
    return r;
}

test('B04 活跃池在阈值内全量、超过后取最新与高互动并集并稳定分页', async () => {
    const f = await fixture();
    let r;
    try {
        r = await seed(f);
        const view = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            const full = await f.ctx.socialRepository.activePool(view, poolRequest, '6', {
                fullScanUntil: 5, recentKeepCount: 2, topEngagementKeepCount: 2, minLifetimeTicks: 2
            });
            assert.equal(full.total, '5');
            assert.deepEqual(full.items.map(x => x.id), ['p5', 'p4', 'p3', 'p2', 'p1']);
            const pool = await f.ctx.socialRepository.activePool(view, poolRequest, '6', {
                fullScanUntil: 4, recentKeepCount: 2, topEngagementKeepCount: 2, minLifetimeTicks: 2
            });
            assert.equal(pool.total, '3');
            assert.deepEqual(pool.items.map(x => x.id), ['p5', 'p4', 'p2']);
            const first = await f.ctx.socialRepository.activePool(view, { maxItems: 2, maxResponseBytes: 65536 }, '6', {
                fullScanUntil: 4, recentKeepCount: 2, topEngagementKeepCount: 2, minLifetimeTicks: 2
            });
            assert.deepEqual(first.items.map(x => x.id), ['p5', 'p4']);
            assert.ok(first.next);
            const second = await f.ctx.socialRepository.activePool(view, { maxItems: 2, maxResponseBytes: 65536, cursor: first.next }, '6', {
                fullScanUntil: 4, recentKeepCount: 2, topEngagementKeepCount: 2, minLifetimeTicks: 2
            });
            assert.deepEqual(second.items.map(x => x.id), ['p2']);
            assert.equal(second.next, undefined);
        }
        finally { await view.close(); }
    }
    finally {
        await r?.release();
        await f.host.close();
    }
});

test('B04 活跃池作为推荐来源进入真实 prepare 与纯排名', async () => {
    const f = await fixture();
    let r;
    try {
        r = await seed(f);
        await f.ctx.plugin(Rankings, { timeDecay: 2000000 });
        await f.ctx.plugin(Recommendations, {
            sources: ['social/active-pool/v1'], fullScanUntil: 4, recentKeepCount: 2, topEngagementKeepCount: 2, minLifetimeTicks: 2
        });
        const prepared = await f.ctx.recommendations.prepare({ actorId: 'b', momentId: 'm', query: { time: '6' } }, { maxItems: 10, maxBytes: 131072 }, activation(f, r, 'b04-prepare', 'b'));
        assert.equal(prepared.status, 'ready');
        assert.deepEqual(prepared.input.items.map(x => x.id).sort(), ['p2', 'p4', 'p5']);
        assert.equal(prepared.input.items.find(x => x.id === 'p2').features.likes, '2');
        const preview = await f.ctx.recommendations.preview(prepared.input, 'chronological/v1', poolRequest);
        assert.deepEqual(preview.items.map(x => x.id), ['p5', 'p4', 'p2']);
    }
    finally {
        await r?.release();
        await f.host.close();
    }
});

test('B04 afterTick 等待已登记批处理真实结束且可重复调用', async () => {
    const f = await fixture();
    try {
        let release;
        const gate = new Promise(resolve => release = resolve);
        const seen = [];
        const dispose = f.ctx.socialRepository.onAfterTick(async () => {
            seen.push('start');
            await gate;
            seen.push('end');
        });
        let settled = false;
        const pending = f.ctx.social.afterTick().then(() => settled = true);
        await new Promise(resolve => setTimeout(resolve, 20));
        assert.equal(settled, false);
        release();
        await pending;
        assert.equal(settled, true);
        assert.deepEqual(seen, ['start', 'end']);
        await dispose();
        await f.ctx.social.afterTick();
    }
    finally { await f.host.close(); }
});
