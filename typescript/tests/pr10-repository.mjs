import { test } from 'node:test';
import assert from 'node:assert/strict';
import Repository from '../.build/social/dist/social-repository/index.js';
test('PR10 production SQL repository entry', () => assert.equal(typeof Repository, 'function'));
import { fixture, activation, request, bounds } from './pr10-support.mjs';
test('PR10-T01 real three-member root and atomic publish/reply/react/follow/unfollow', async () => {
    const f = await fixture();
    let r;
    try {
        r = await f.ctx.executionFence.reserve({
            id: 'actions'
        });
        const run = (name, input, id, actor = 'a') => f.ctx.actions.execute('social.' + name, input, activation(f, r, id, actor));
        const published = await run('publish', {
            id: 'post', content: '完整原文', tags: ['science'], metadata: {
                original: 'retained'
            }
        }, 'publish');
        assert.equal(published.status, 'completed');
        await run('reply', {
            id: 'reply', postId: 'post', content: '完整回复'
        }, 'reply', 'b');
        await run('react', {
            postId: 'post', kind: 'like'
        }, 'like', 'c');
        await run('react', {
            postId: 'post', kind: 'like'
        }, 'like-again', 'c');
        await run('repost', {
            id: 'repost', postId: 'post', content: '转发评论'
        }, 'repost', 'c');
        await run('follow', {
            actorId: 'b'
        }, 'follow', 'c');
        await run('unfollow', {
            actorId: 'b'
        }, 'unfollow', 'c');
        const view = await f.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        const post = await f.ctx.socialRepository.get('post', view);
        assert.equal(post.likes, '1');
        assert.equal(post.replies, '1');
        assert.equal(post.reposts, '1');
        assert.equal(post.views, '0');
        assert.equal(post.metadata.original, 'retained');
        const inbox = await f.ctx.socialRepository.feed('b', view, bounds);
        assert.equal(inbox.total, '2');
        assert.equal(inbox.items[0].content, '完整原文');
        const counts = await view.query({
            sql: 'SELECT count(*) AS n FROM action_receipts'
        }, {
            maxRows: 1, maxBytes: 4096
        });
        assert.equal(String(counts[0].n), '7');
        await view.close();
    }
    finally {
        await r?.release();
        await f.host.close();
    }
});
test('PR10-T09 notification uniqueness fault rolls back content delivery and action receipt', async () => {
    const f = await fixture();
    let r;
    try {
        r = await f.ctx.executionFence.reserve({
            id: 'rollback'
        });
        await f.ctx.database.transaction(r.lease, request('conflict'), tx => f.ctx.socialRepository.notify(tx, {
            id: JSON.stringify(['rollback-post', 'b']), actorId: 'b', kind: 'fault', time: '0', data: {}
        }));
        await assert.rejects(f.ctx.actions.execute('social.publish', {
            id: 'rollback-post', content: 'must rollback'
        }, activation(f, r, 'publish')), /UNIQUE/);
        const view = await f.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal(await f.ctx.socialRepository.get('rollback-post', view), undefined);
        assert.equal(String((await view.query({
            sql: 'SELECT count(*) AS n FROM social_deliveries WHERE post=?', parameters: ['rollback-post']
        }, {
            maxRows: 1, maxBytes: 4096
        }))[0].n), '0');
        assert.equal((await f.ctx.database.probe('publish/social/publish')).status, 'not-committed');
        await view.close();
    }
    finally {
        await r?.release();
        await f.host.close();
    }
});
