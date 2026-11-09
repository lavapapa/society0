import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, assistant} from './c1-support.mjs';
import {contextBudget, DEFAULT_CONTEXT_BUDGET} from '../.build/core/dist/contracts/context.js';

const budget = contextBudget();

function contentText(message) {
    if (typeof message.content === 'string') return message.content;
    if (Array.isArray(message.content)) return message.content.map(part => part?.type === 'text' ? part.text ?? '' : '').join('');
    return '';
}

function isPercept(message) {
    return message.role === 'user' && contentText(message).trimStart().startsWith('{"source"');
}

function estimate(messages, filter) {
    return messages.filter(filter).reduce((sum, message) => sum + budget.estimate(contentText(message)), 0);
}

function historyEstimate(messages) {
    return estimate(messages, message => message.role !== 'system' && !isPercept(message));
}

function prefix(left, right) {
    return left.length <= right.length && left.every((message, index) => JSON.stringify(message) === JSON.stringify(right[index]));
}

async function activateAt(ctx, run, id) {
    const context = run.context(id, 'a', 'm');
    const driver = await ctx.drivers.acquire('llm/c1', context);
    try {
        return await driver.value.activate({id, actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: id}, context);
    } finally {
        await driver.release();
    }
}

test('D4 history slot stays within budget and folds at stable boundaries', async t => {
    const requests = [];
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'h'.repeat(1200)}])],
        driver: {threadLifetime: 'actor'},
        observe: messages => requests.push(messages),
    });
    t.after(() => run.close());

    for (let index = 0; index < 10; index++) {
        const result = await activateAt(ctx, run, 'd4-history-' + index);
        assert.equal(result.status, 'completed', JSON.stringify(result));
    }

    assert.equal(requests.length, 10);
    const history = requests.map(historyEstimate);
    assert.ok(Math.max(...history) <= budget.slot('history'), 'history slot exceeded budget: ' + JSON.stringify(history));

    let folds = 0;
    for (let index = 1; index < requests.length; index++) {
        if (prefix(requests[index - 1], requests[index])) continue;
        folds++;
        assert.ok(requests[index].some(message => String(message.content).includes('历史已折叠')), 'a changed prefix must be an explicit fold');
    }
    assert.ok(folds >= 1, 'the run must fold at least once');
    assert.ok(folds <= 4, 'folding must not happen on every request: ' + folds);
});

test('D4 folding never separates a tool call from its tool result', async t => {
    const requests = [];
    const {run, ctx} = await driverFixture({
        messages: [
            assistant([{type: 'toolCall', id: 'd4-fold-call', name: 'read', arguments: {path: '/workspace'}}], 'toolUse'),
            assistant([{type: 'text', text: 't'.repeat(1000)}]),
        ],
        driver: {threadLifetime: 'actor', maxRequests: 4, maxToolCalls: 2},
        observe: messages => requests.push(messages),
    });
    t.after(() => run.close());

    for (let index = 0; index < 8; index++) await activateAt(ctx, run, 'd4-tool-' + index);

    assert.equal(requests.length, 16);
    for (const messages of requests) {
        for (let index = 0; index < messages.length; index++) {
            const message = messages[index];
            if (message.role === 'assistant' && Array.isArray(message.content)) {
                for (const part of message.content) {
                    if (part.type !== 'toolCall') continue;
                    const result = messages[index + 1];
                    assert.equal(result?.role, 'toolResult', 'tool call must be followed by its result');
                    assert.equal(result.toolCallId, part.id);
                }
            }
            if (message.role !== 'toolResult') continue;
            const call = messages[index - 1];
            assert.equal(call?.role, 'assistant', 'tool result must be preceded by its call');
            assert.ok(call.content.some(part => part.type === 'toolCall' && part.id === message.toolCallId));
        }
    }
    assert.ok(requests.some(messages => messages.some(message => String(message.content).includes('历史已折叠'))), 'the tool thread must fold at least once');
});

test('D4 percepts over budget are truncated with a continuation hint', async t => {
    const requests = [];
    const source = {
        id: 'd4/percepts',
        schema: {},
        cursorScope: 'actor',
        available: async () => true,
        versions: async () => [{owner: 'd4/percepts', version: '1'}],
        collect: async (scope, before) => {
            const cursor = before ?? 0;
            if (cursor >= 3) return {items: [], nextCursor: cursor, through: cursor, hasMore: false};
            return {
                items: [0, 1].map(offset => ({
                    id: 'd4-percept-' + cursor + '-' + offset,
                    kind: 'notice',
                    material: {kind: 'inline', value: {text: 'p'.repeat(400)}},
                    provenance: {source: 'd4/percepts'},
                })),
                nextCursor: cursor + 1,
                through: cursor + 1,
                hasMore: cursor + 1 < 3,
            };
        },
    };
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'ok'}])],
        driver: {threadLifetime: 'actor', perceptions: ['d4/percepts'], budget: {...DEFAULT_CONTEXT_BUDGET, percepts: 200}},
        register(c) { c.perceptions.register(source); },
        observe: messages => requests.push(messages),
    });
    t.after(() => run.close());

    assert.equal((await activateAt(ctx, run, 'd4-percept-0')).status, 'completed');

    assert.equal(requests.length, 1);
    const percepts = requests[0].filter(isPercept);
    const used = estimate(percepts, () => true);
    assert.ok(used <= 200, 'percept slot exceeded budget: ' + used);
    assert.ok(percepts.length >= 1, 'some percepts must remain in the prompt');
    assert.ok(percepts.some(message => /续读|截断/.test(contentText(message))), 'a truncated percept must carry a continuation hint');
});
