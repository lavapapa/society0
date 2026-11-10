import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, assistant} from './c1-support.mjs';

const perceptMessage = message => typeof message.content === 'string' && message.content.startsWith('{"source":"d21/default"');

function source(defaultDrivers) {
    return {
        id: 'd21/default',
        schema: {},
        ...(defaultDrivers ? {defaultDrivers} : {}),
        cursorScope: 'thread',
        available: async () => true,
        versions: async () => [{owner: 'd21/default', version: '1'}],
        collect: async (scope, before) => before === null || before === undefined
            ? {items: [{id: 'd21-default-1', kind: 'notice', material: {kind: 'inline', value: {text: 'default percept'}}, provenance: {source: 'd21/default'}}], nextCursor: 1, through: 1, hasMore: false}
            : {items: [], nextCursor: before, through: before, hasMore: false},
    };
}

async function observed(t, {driver, defaultDrivers}) {
    const seen = [];
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'done'}])],
        prompts: {sections: [{id: 'role', order: 0, text: 'D21-PERCEPTION-HEADER'}]},
        driver,
        register(c) { c.perceptions.register(source(defaultDrivers)); },
        observe(messages) { seen.push(...messages); },
    });
    t.after(() => run.close());
    const context = run.context('d21-perception', 'a', 'm1');
    const driverLease = await ctx.drivers.acquire('llm/c1', context);
    try {
        const result = await driverLease.value.activate({id: 'd21-perception', actorId: 'a', momentId: 'm1', source: 'test', driver: 'llm/c1', input: {task: 'observe'}}, context);
        assert.equal(result.status, 'completed', JSON.stringify(result));
    } finally {
        await driverLease.release();
    }
    return seen.filter(perceptMessage).length;
}

test('D21 a source declaring defaultDrivers is mounted when perceptions is empty', async t => {
    assert.equal(await observed(t, {driver: {}, defaultDrivers: ['llm/c1']}), 1);
});

test('D21 explicit perceptions are not duplicated by default mounting', async t => {
    assert.equal(await observed(t, {driver: {perceptions: ['d21/default']}, defaultDrivers: ['llm/c1']}), 1);
});

test('D21 defaultPerceptions false keeps the explicit configuration only', async t => {
    assert.equal(await observed(t, {driver: {defaultPerceptions: false}, defaultDrivers: ['llm/c1']}), 0);
});

test('D21 defaultDrivers for another driver name is not mounted', async t => {
    assert.equal(await observed(t, {driver: {}, defaultDrivers: ['llm/other']}), 0);
});
