import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    parseTarget, targetText, offerKey, offerEntry, schemaCard, canonicalJson,
    foldOffers, diffOffers, applyOfferDelta, renderOfferDelta, truncateOffers,
    offerLine, schemaLine, emptyOfferFold, actionsDeliveryChannel,
} from '../.build/core/dist/contracts/affordance.js';
import {
    DELIVERY_CHANNEL_ORDER, orderDeliveryChannels, foldDelivery, renderDelivery,
} from '../.build/core/dist/contracts/delivery.js';

const code = (error) => error?.code;
const offer = (action, extra = {}) => ({ action, provider: 'test', ...extra });
const entry = (action, extra, card) => offerEntry(offer(action, extra), card);

test('A-T3 parseTarget 只在第一个冒号处切分，非法 kind 或空 id 报 ACTION_TARGET_INVALID', () => {
    assert.deepEqual(parseTarget('social/post:a:b'), { kind: 'social/post', id: 'a:b' });
    assert.equal(targetText(parseTarget('social/post:a:b')), 'social/post:a:b');
    assert.deepEqual(parseTarget('contract/offer:o_33'), { kind: 'contract/offer', id: 'o_33' });
    for (const bad of [':x', 'social/post', 'social/post:', '']) {
        assert.throws(() => parseTarget(bad), (error) => code(error) === 'ACTION_TARGET_INVALID', bad);
    }
});

test('A-T3 diffOffers 分出 added/changed/expired/folded，被预算收起但仍有效的 offer 不算失效', () => {
    const a = entry('social.publish');
    const b = entry('social.reply', { target: { kind: 'social/post', id: 'p_1' } });
    const c = entry('social.reply', { target: { kind: 'social/post', id: 'p_2' } });
    const oldE = entry('social.react', { target: { kind: 'social/post', id: 'p_9' }, prefill: { mood: 'old' } });
    const newE = entry('social.react', { target: { kind: 'social/post', id: 'p_9' }, prefill: { mood: 'new' } });
    const d = entry('social.reply', { target: { kind: 'social/post', id: 'p_3' } });
    const delivered = foldOffers([{ added: [a, b, c, oldE], schemas: [] }]);
    assert.equal(delivered.delivered.size, 4);
    const delta = diffOffers(delivered, [a, b, newE], [a, newE, d]);
    assert.deepEqual(delta.added.map((value) => offerKey(value)), [offerKey(newE.offer), offerKey(d.offer)]);
    assert.deepEqual(delta.changed.map((value) => offerKey(value)), [offerKey(newE.offer)]);
    assert.deepEqual(delta.expired, [offerKey(c.offer)]);
    assert.deepEqual(delta.folded, [offerKey(b.offer)]);
    assert.equal(delta.expired.includes(offerKey(b.offer)), false);
    assert.equal(delta.initial, false);
});

test('A-T3 renderOfferDelta 与 §4.4 样例一致，且同一输入两次逐字节相同', () => {
    const reply = schemaCard('social.reply', {
        description: '回复一条帖子',
        input: { type: 'object', properties: { content: { type: 'string' } }, required: ['content'] },
        targets: { kinds: ['social/post'], required: true },
    });
    const publish = schemaCard('social.publish', {
        description: '发布帖子',
        input: { type: 'object', properties: { content: { type: 'string' }, tags: { type: 'array', items: { type: 'string' } } }, required: ['content'] },
    });
    const publishEntry = entry('social.publish', {}, publish);
    const r812 = entry('social.reply', { target: { kind: 'social/post', id: 'p_812' }, prefill: { postId: 'p_812' }, needs: ['content'], reason: '新投递，来自 a_17' }, reply);
    const r790 = entry('social.reply', { target: { kind: 'social/post', id: 'p_790' }, prefill: { postId: 'p_790' }, needs: ['content'], reason: '被提及' }, reply);
    const delta = {
        initial: true,
        added: [publishEntry.offer, r812.offer, r790.offer],
        entries: [publishEntry, r812, r790],
        changed: [],
        expired: [],
        folded: [],
        schemas: [reply, publish],
        omitted: [{ action: 'social.react', count: 37 }],
    };
    const expected = [
        '可用行动',
        'schema social.reply {"description":"回复一条帖子","input":{"type":"object","properties":{"content":{"type":"string"}},"required":["content"]},"targets":["social/post"]}',
        'schema social.publish {"description":"发布帖子","input":{"type":"object","properties":{"content":{"type":"string"},"tags":{"type":"array","items":{"type":"string"}}},"required":["content"]}}',
        '+ social.publish',
        '+ social.reply social/post:p_812  预填{"postId":"p_812"} 需填[content]  ·新投递，来自 a_17',
        '+ social.reply social/post:p_790  预填{"postId":"p_790"} 需填[content]  ·被提及',
        '另有：social.react ×37（完整清单 /self/actions/offers.jsonl）',
    ].join('\n');
    assert.equal(renderOfferDelta(delta), expected);
    assert.equal(renderOfferDelta(delta), renderOfferDelta(delta));
    const changed = renderOfferDelta({ ...delta, initial: false, schemas: [], omitted: [], added: [r790.offer], entries: [r790], expired: [offerKey(r812.offer)] });
    assert.equal(changed, ['可用行动（变更）', '+ social.reply social/post:p_790  预填{"postId":"p_790"} 需填[content]  ·被提及', '- social.reply social/post:p_812'].join('\n'));
    assert.equal(renderOfferDelta({ ...emptyOfferDelta(), entries: [], added: [], expired: [] }), '');
});

function emptyOfferDelta() {
    return { initial: true, added: [], entries: [], changed: [], expired: [], folded: [], schemas: [], omitted: [] };
}

test('A-T3 foldOffers 按变更段顺序 fold，schema 摘要只记一次，结果等于直接集合', () => {
    const reply = schemaCard('social.reply', { input: { type: 'object', properties: { content: { type: 'string' } } } });
    const publish = schemaCard('social.publish', { input: { type: 'object', properties: { content: { type: 'string' }, tags: { type: 'array' } } } });
    const r812 = entry('social.reply', { target: { kind: 'social/post', id: 'p_812' } }, reply);
    const r790 = entry('social.reply', { target: { kind: 'social/post', id: 'p_790' } }, reply);
    const publishEntry = entry('social.publish', {}, publish);
    const records = [
        { added: [publishEntry, r812], schemas: [publish.digest, reply.digest] },
        { added: [r790], expired: [offerKey(publishEntry.offer)], schemas: [reply.digest] },
        { added: [r812], expired: [offerKey(r790.offer)] },
    ];
    const folded = foldOffers(records);
    assert.deepEqual([...folded.delivered.keys()].sort(), [offerKey(r812.offer)].sort());
    assert.deepEqual([...folded.schemas].sort(), [reply.digest, publish.digest].sort());
    const delta = diffOffers(folded, [r812], [r812]);
    assert.equal(delta.added.length, 0);
    assert.equal(delta.schemas.length, 0);
    const replay = applyOfferDelta(emptyOfferFold(), { ...emptyOfferDelta(), entries: records.flatMap((record) => record.added), schemas: [reply, publish] });
    assert.deepEqual([...replay.delivered.keys()].sort(), [offerKey(r812.offer), offerKey(r790.offer), offerKey(publishEntry.offer)].sort());
});

test('A-T3 预算截断按 maxOffers 与字节数整条取舍，omitted 计数正确', () => {
    const many = Array.from({ length: 65 }, (_, index) => entry('bulk.act', { target: { kind: 'bulk/item', id: `i_${index}` } }));
    const capped = truncateOffers(many, { maxOffers: 64, maxOfferBytes: 1048576, maxSchemaBytes: 4096 });
    assert.equal(capped.deliver.length, 64);
    assert.deepEqual(capped.omitted, [{ action: 'bulk.act', count: 1 }]);

    const long = Array.from({ length: 3 }, (_, index) => entry('bulk.act', { target: { kind: 'bulk/item', id: `j_${index}` }, reason: 'x'.repeat(64) }));
    const twoRecords = Buffer.byteLength(offerLine(long[0])) + 1 + Buffer.byteLength(offerLine(long[1]));
    const budgeted = truncateOffers(long, { maxOffers: 64, maxOfferBytes: twoRecords, maxSchemaBytes: 4096 });
    assert.equal(budgeted.deliver.length, 2);
    assert.deepEqual(budgeted.omitted, [{ action: 'bulk.act', count: 1 }]);
    assert.equal(offerLine(budgeted.deliver[1]), offerLine(long[1]));
    const tooSmall = truncateOffers(long, { maxOffers: 64, maxOfferBytes: Buffer.byteLength(offerLine(long[0])) - 1, maxSchemaBytes: 4096 });
    assert.equal(tooSmall.deliver.length, 0);
    assert.deepEqual(tooSmall.omitted, [{ action: 'bulk.act', count: 3 }]);
});

test('A-T3 schema 行保持定义中的键顺序，其余 JSON 规范化排序', () => {
    const card = schemaCard('x.y', { input: { type: 'object', properties: { z: { type: 'string' }, a: { type: 'number' } } } });
    assert.equal(schemaLine(card), 'schema x.y {"input":{"type":"object","properties":{"z":{"type":"string"},"a":{"type":"number"}}}}');
    assert.equal(canonicalJson({ b: 1, a: { d: 2, c: 3 } }), '{"a":{"c":3,"d":2},"b":1}');
});

test('A-T3 DeliveryChannel 契约：通道顺序固定 state→percepts→actions，fold 按 originalProviderFields 分桶', () => {
    assert.deepEqual([...DELIVERY_CHANNEL_ORDER], ['state', 'percepts', 'actions']);
    const stateChannel = {
        name: 'state', empty: 0,
        fold: (state, record) => state + record,
        diff: () => null,
        render: () => '状态变更',
    };
    const perceptsChannel = { name: 'percepts', empty: [], fold: (state, record) => [...state, record], diff: () => null, render: () => '感知' };
    const ordered = orderDeliveryChannels([actionsDeliveryChannel, perceptsChannel, stateChannel]);
    assert.deepEqual(ordered.map((channel) => channel.name), ['state', 'percepts', 'actions']);
    assert.throws(() => orderDeliveryChannels([{ ...stateChannel, name: 'unknown' }]), (error) => code(error) === 'DELIVERY_CHANNEL_UNKNOWN');

    const reply = schemaCard('social.reply', { input: { type: 'object' } });
    const r1 = entry('social.reply', { target: { kind: 'social/post', id: 'p_1' } }, reply);
    const messages = [
        { originalProviderFields: { state: 2, offers: { added: [r1], schemas: [reply.digest] } } },
        { originalProviderFields: { state: 3 }, details: { offers: { expired: [offerKey(r1.offer)] } } },
    ];
    const folded = foldDelivery(messages, [stateChannel, actionsDeliveryChannel]);
    assert.equal(folded.channels.get('state'), 5);
    const actions = folded.channels.get('actions');
    assert.deepEqual([...actions.delivered.keys()], []);
    assert.equal(actions.schemas.size, 1);

    const before = foldDelivery(messages.slice(0, 1), [stateChannel, actionsDeliveryChannel]);
    assert.deepEqual([...before.channels.get('actions').delivered.keys()], [offerKey(r1.offer)]);
    const empty = foldDelivery([], [stateChannel, actionsDeliveryChannel]);
    const text = renderDelivery(empty, [actionsDeliveryChannel, stateChannel], new Map([
        ['state', { valid: [5], deliver: [5] }],
        ['actions', { valid: [r1], deliver: [r1] }],
    ]));
    assert.equal(text, ['状态变更', '可用行动', 'schema social.reply {"input":{"type":"object"}}', '+ social.reply social/post:p_1'].join('\n'));
});
