import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, activation, request } from './pr10-support.mjs';
import { schedule } from '../.build/social/dist/social/index.js';

const actors = ['a', 'b', 'c', 'd'].map((id, index) => ({ id, attributes: { index } }));
const pages = { maxItems: 10, maxResponseBytes: 65536 };

async function pairingFixture() {
    const f = await fixture({ actors, edges: [], social: { pairingGroups: [['a', 'b', 'c', 'd']], pairingSessionMinutes: 10 } });
    const r = await f.ctx.executionFence.reserve({ id: 'b02' });
    return { f, r };
}
let operations = 0;
const exchange = (f, r, actor, name, input) => f.ctx.actions.execute('social.' + name, input, activation(f, r, 'b02-' + name + '-' + (++operations), actor));

test('B02 圆桌配对覆盖每对一次、同轮重入保留配对并拒绝回退', async () => {
    const { f, r } = await pairingFixture();
    try {
        const first = (await f.ctx.database.transaction(r.lease, request('round-1'), tx => f.ctx.social.startRound(tx, 1))).value;
        assert.deepEqual(first, { round: 1, pairs: [['a', 'd'], ['b', 'c']], successfulPairs: 2 });
        let view = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            const a = await f.ctx.social.pairing(view, 'a');
            assert.equal(a.currentPartner, 'd');
            assert.equal(a.canConverse, true);
            assert.deepEqual(a.partnerHistory, ['d']);
            const group = await f.ctx.social.groupView(view, 'a');
            assert.deepEqual(group.members, ['a', 'b', 'c', 'd']);
            assert.deepEqual(group.upcoming, [{ round: 1, partner: 'd' }, { round: 2, partner: 'c' }, { round: 3, partner: 'b' }]);
        }
        finally { await view.close(); }
        const repeat = (await f.ctx.database.transaction(r.lease, request('round-1-again'), tx => f.ctx.social.startRound(tx, 1))).value;
        assert.deepEqual(repeat, { round: 1, pairs: [], successfulPairs: 0 });
        await assert.rejects(f.ctx.database.transaction(r.lease, request('round-out'), tx => f.ctx.social.startRound(tx, 4)), /SOCIAL_PAIRING_ROUND/);
    }
    finally { await r.release(); await f.host.close(); }
});

test('B02 伙伴消息与组广播按收件人 FoV 可见、原文保留且重开轮次清空当前收件箱', async () => {
    const { f, r } = await pairingFixture();
    try {
        await f.ctx.database.transaction(r.lease, request('round-1'), tx => f.ctx.social.startRound(tx, 1));
        const direct = await exchange(f, r, 'a', 'sendPartnerMessage', { content: '完整伙伴原文🙂' });
        assert.equal(direct.status, 'completed');
        assert.equal(direct.value.sentTo, 'd');
        const broadcast = await exchange(f, r, 'a', 'broadcastToGroup', { content: '组内广播原文' });
        assert.equal(broadcast.status, 'completed');
        assert.deepEqual(broadcast.value.delivered.map(x => x.receiver), ['b', 'c', 'd']);
        await assert.rejects(exchange(f, r, 'a', 'sendPartnerMessage', { content: '   ' }), /SOCIAL_MESSAGE_EMPTY/);
        let view = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            const forD = await f.ctx.social.conversation(view, 'd', pages);
            assert.equal(forD.total, '2');
            assert.deepEqual(forD.items.map(x => x.content), ['完整伙伴原文🙂', '组内广播原文']);
            assert.equal((await f.ctx.social.conversation(view, 'a', pages)).total, '0');
            assert.equal((await f.ctx.social.conversation(view, 'b', pages)).total, '1');
            const history = await f.ctx.social.conversation(view, 'd', pages, true);
            assert.equal(history.total, '2');
        }
        finally { await view.close(); }
        (await f.ctx.database.transaction(r.lease, request('reset'), tx => f.ctx.social.resetRoundMessages(tx, 1)));
        view = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            assert.equal((await f.ctx.social.conversation(view, 'd', pages)).total, '0');
            assert.equal((await f.ctx.social.conversation(view, 'd', pages, true)).total, '2');
        }
        finally { await view.close(); }
        const second = (await f.ctx.database.transaction(r.lease, request('round-2'), tx => f.ctx.social.startRound(tx, 2))).value;
        assert.deepEqual(second.pairs, [['a', 'c'], ['d', 'b']]);
        await assert.rejects(f.ctx.database.transaction(r.lease, request('round-back'), tx => f.ctx.social.startRound(tx, 1)), /SOCIAL_PAIRING_ROUND/);
        view = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            assert.equal((await f.ctx.social.pairing(view, 'a')).currentPartner, 'c');
            assert.deepEqual((await f.ctx.social.pairing(view, 'a')).partnerHistory, ['d', 'c']);
            assert.equal((await f.ctx.social.conversation(view, 'd', pages)).total, '0');
            assert.equal((await f.ctx.social.conversation(view, 'd', pages, true)).total, '2');
        }
        finally { await view.close(); }
    }
    finally { await r.release(); await f.host.close(); }
});

test('B02 圆桌调度对每个偶数规模恰好覆盖所有组合', () => {
    for (const size of [2, 4, 6, 20]) {
        const members = Array.from({ length: size }, (_, i) => 'm' + i), rounds = schedule(members);
        assert.equal(rounds.length, size - 1);
        const pairs = new Set();
        for (const round of rounds) {
            assert.equal(round.length, size / 2);
            const seen = new Set();
            for (const [a, b] of round) {
                assert.notEqual(a, b);
                assert.ok(!seen.has(a) && !seen.has(b));
                seen.add(a); seen.add(b);
                pairs.add([a, b].sort().join(':'));
            }
            assert.equal(seen.size, size);
        }
        assert.equal(pairs.size, size * (size - 1) / 2);
    }
});
