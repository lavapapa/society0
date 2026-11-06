import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, request } from './pr10-support.mjs';
import Random from '../.build/core/dist/random/index.js';

async function seed(f, lease) {
    return f.ctx.database.transaction(lease, request('seed'), async tx => {
        await f.ctx.socialRepository.publish(tx, { id: 'p1', authorId: 'a', content: '关于 #topic 的完整原文', tags: ['news'], createdTime: '1' }, []);
        await f.ctx.socialRepository.publish(tx, { id: 'p2', authorId: 'b', content: '#topic 第二条', tags: [], createdTime: '2' }, []);
        await f.ctx.socialRepository.publish(tx, { id: 'p3', authorId: 'c', content: '无关正文', tags: [], createdTime: '3' }, []);
    });
}
async function reserve(f) {
    return (await f.ctx.executionFence.reserve({ id: 'intervention' })).lease;
}
async function tags(f, tag) {
    const view = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
    try {
        return (await view.query({ sql: 'SELECT post FROM social_tags WHERE tag=? ORDER BY post', parameters: [tag] }, { maxRows: 100, maxBytes: 8192 })).map(row => String(row.post));
    }
    finally { await view.close(); }
}
test('B03 规则干预按研究随机流抽样打标、保留完整原文并可重复调用', async () => {
    const f = await fixture();
    const lease = await reserve(f);
    try {
        await f.ctx.plugin(Random, { seed: 'intervention-test' });
        await seed(f, lease);
        const all = (await f.ctx.database.transaction(lease, request('intervene-all'), tx => f.ctx.social.intervene(tx, {
            time: '7', targetHashtag: '#topic', interventionRate: 1, tagToApply: 'flagged'
        }))).value;
        assert.deepEqual(all, {
            ruleType: 'intervention', targetHashtag: '#topic', interventionRate: 1, tagApplied: 'flagged',
            totalPosts: '3', postsFlagged: 2, flaggedPostIds: ['p1', 'p2'], timestamp: '7'
        });
        assert.deepEqual(await tags(f, 'flagged'), ['p1', 'p2']);
        const original = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
        assert.equal((await f.ctx.socialRepository.get('p1', original)).content, '关于 #topic 的完整原文');
        await original.close();
        const again = (await f.ctx.database.transaction(lease, request('intervene-again'), tx => f.ctx.social.intervene(tx, {
            time: '8', targetHashtag: '#topic', interventionRate: 1, tagToApply: 'flagged'
        }))).value;
        assert.equal(again.postsFlagged, 0);
        const none = (await f.ctx.database.transaction(lease, request('intervene-none'), tx => f.ctx.social.intervene(tx, {
            time: '9', targetHashtag: '#topic', interventionRate: 0, tagToApply: 'checked'
        }))).value;
        assert.deepEqual(none.flaggedPostIds, []);
        assert.deepEqual(await tags(f, 'checked'), []);
        await assert.rejects(f.ctx.database.transaction(lease, request('intervene-bad'), tx => f.ctx.social.intervene(tx, {
            time: '10', targetHashtag: '', interventionRate: 1, tagToApply: 'x'
        })), /SOCIAL_INTERVENTION_HASHTAG/);
        await assert.rejects(f.ctx.database.transaction(lease, request('intervene-rate'), tx => f.ctx.social.intervene(tx, {
            time: '10', targetHashtag: '#topic', interventionRate: 2, tagToApply: 'x'
        })), /SOCIAL_INTERVENTION_RATE/);
    }
    finally {
        await f.host.close();
    }
});
test('B03 规则干预的抽样地址随种子稳定且只覆盖命中帖子', async () => {
    const runs = [];
    for (const seedValue of ['same-seed', 'same-seed', 'other-seed']) {
        const f = await fixture(), lease = await reserve(f);
        try {
            await f.ctx.plugin(Random, { seed: seedValue });
            await seed(f, lease);
            const report = (await f.ctx.database.transaction(lease, request('intervene'), tx => f.ctx.social.intervene(tx, {
                time: '1', targetHashtag: '#topic', interventionRate: 0.5, tagToApply: 'sampled'
            }))).value;
            runs.push(report.flaggedPostIds);
            assert.ok(report.flaggedPostIds.every(id => id === 'p1' || id === 'p2'));
        }
        finally { await f.host.close(); }
    }
    assert.deepEqual(runs[0], runs[1]);
});
