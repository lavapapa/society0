import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, activation, request } from './pr10-support.mjs';
import { targetText } from '../.build/core/dist/contracts/affordance.js';

const ids = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j'];
const actors = ids.map((id, index) => ({ id, attributes: { index } }));
const edges = ids.filter(id => id !== 'a').map(id => ({ from: id, to: 'a' }));
const scopeFor = actorId => ({ actorId, momentId: 'moment', query: null });
const limits = { maxOffers: 64, maxOfferBytes: 1048576, maxSchemaBytes: 4096 };
const bounds = { maxItems: 100, maxResponseBytes: 1048576 };

async function read(ctx, body) {
    const view = await ctx.database.snapshot({ maxAgeMs: 30000 });
    try {
        return await body(view);
    }
    finally {
        await view.close();
    }
}

async function present(ctx, reservation, actorId, momentId, items, label) {
    await ctx.database.transaction(reservation.lease, request('present-' + label), async (tx) => {
        const versions = await ctx.socialRepository.versions(tx, ['social/content', 'social/relations', 'social/exposure', 'social/notifications']);
        await ctx.socialRepository.present(tx, { presentationId: label, actorId, momentId, inputId: label, versions, itemIds: items });
    });
}

async function seed(f) {
    const r = await f.ctx.executionFence.reserve({ id: 'a6-seed' });
    const posts = [];
    try {
        for (let index = 0; index < 40; index++) {
            const author = ids[1 + (index % (ids.length - 1))];
            const id = 'p' + String(index).padStart(2, '0');
            await f.ctx.database.transaction(r.lease, request('publish-' + id), tx => f.ctx.socialRepository.publish(tx, {
                id, authorId: author, content: '帖子 ' + id, createdTime: String(index + 1)
            }, ids.filter(item => item !== author)));
            posts.push(id);
        }
    }
    finally {
        await r.release();
    }
    return posts;
}

test('A-T6 social 目标行动、内部 present 与 offer provider', async () => {
    const f = await fixture({ actors, edges, social: { offerPosts: 16 } });
    try {
        const c = f.ctx;
        const posts = await seed(f);
        const r = await c.executionFence.reserve({ id: 'a6-actions' });
        try {
            await present(c, r, 'a', 'moment', ['p00', 'p01'], 'present-two');
            await c.database.transaction(r.lease, request('follow'), tx => c.socialRepository.follow(tx, 'a', 'b', '1', 'follow-b'));

            const set = await read(c, view => c.actions.offers(scopeFor('a'), view, limits));
            const postTargets = new Set(set.offers.filter(offer => offer.target?.kind === 'social/post').map(offer => targetText(offer.target)));
            assert.deepEqual([...postTargets].sort(), ['social/post:p00', 'social/post:p01']);
            for (const action of ['social.reply', 'social.react', 'social.repost']) {
                for (const post of ['p00', 'p01']) {
                    const offer = set.offers.find(item => item.action === action && item.target?.id === post);
                    assert.ok(offer, action + ' ' + post);
                    assert.deepEqual(offer.prefill, { postId: post });
                    assert.deepEqual(offer.locked, ['postId']);
                    assert.equal(offer.provider, 'social');
                }
            }
            const follow = set.offers.find(item => item.action === 'social.follow' && item.target?.id === 'c');
            assert.ok(follow);
            assert.deepEqual(follow.prefill, { actorId: 'c' });
            const unfollow = set.offers.find(item => item.action === 'social.unfollow' && item.target?.id === 'b');
            assert.ok(unfollow);

            await read(c, async view => {
                const discovered = await c.actions.discover(scopeFor('a'), view, bounds);
                assert.equal(discovered.items.some(item => item.name === 'social.present'), false);
                assert.equal((await c.actions.offers(scopeFor('a'), view, limits)).offers.some(offer => offer.action === 'social.present'), false);
            });

            const receipt = await c.actions.execute('social.reply', { content: '回复 p00' }, activation(f, r, 'a6-reply', 'a'), { kind: 'social/post', id: 'p00' });
            assert.equal(receipt.status, 'completed');
            const locked = await c.actions.execute('social.reply', { postId: 'p01', content: '锁定冲突' }, activation(f, r, 'a6-locked', 'a'), { kind: 'social/post', id: 'p00' });
            assert.equal(locked.status, 'rejected');
            assert.equal(locked.value.code, 'ACTION_INPUT_LOCKED');

            await present(c, r, 'a', 'moment', [], 'internal-only');

            await present(c, r, 'a', 'moment', posts, 'present-forty');
            const many = await read(c, view => c.actions.offers(scopeFor('a'), view, limits));
            const offered = new Set(many.offers.filter(offer => offer.action === 'social.reply').map(offer => offer.target.id));
            assert.equal(offered.size, 16);
            for (const id of posts.slice(-16))
                assert.ok(offered.has(id), id);
            assert.equal(offered.has(posts[posts.length - 17]), false);

            const folded = posts[posts.length - 30];
            const late = await c.actions.execute('social.reply', { content: '有效但已收起' }, activation(f, r, 'a6-folded', 'a'), { kind: 'social/post', id: folded });
            assert.equal(late.status, 'completed');

            const missing = await c.actions.execute('social.reply', { postId: 'p_deleted', content: '已删除' }, activation(f, r, 'a6-missing', 'a'), { kind: 'social/post', id: 'p_deleted' });
            assert.equal(missing.status, 'rejected');
            assert.equal(missing.value.code, 'ACTION_NOT_OFFERED');

            const outsider = await read(c, async view => ({
                offers: await c.actions.offers(scopeFor('z'), view, limits),
                discovered: await c.actions.discover(scopeFor('z'), view, bounds)
            }));
            assert.equal(outsider.offers.offers.some(offer => offer.action.startsWith('social.')), false);
            assert.equal(outsider.discovered.items.some(item => item.name.startsWith('social.')), false);
        }
        finally {
            await r.release();
        }
    }
    finally {
        await f.host.close();
    }
});
