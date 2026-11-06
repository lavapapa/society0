import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './pr07-support.mjs';

const definition = (name, inputSchema) => ({
    name,
    inputSchema,
    outputSchema: {},
    effect: 'read',
    available: async () => true,
    execute: async (input, context) => ({ operationId: context.operationId, status: 'completed', value: input, original: null, artifacts: [] }),
});

test('D5 ACTION_INPUT 回执带字段路径与期望摘要，超过 5 条截断并计数', async () => {
    const env = await setup();
    try {
        const { ctx } = env;
        ctx.actions.register(definition('d5.quantity', {
            type: 'object',
            properties: { quantity: { type: 'integer' }, label: { type: 'string' } },
            required: ['quantity'],
            additionalProperties: false,
        }));
        const properties = {};
        for (let index = 0; index < 7; index++)
            properties[`field${index}`] = { type: 'string' };
        ctx.actions.register(definition('d5.many', { type: 'object', properties, required: Object.keys(properties) }));

        const wrong = await ctx.actions.execute('d5.quantity', { quantity: '3' }, env.context('d5-wrong'));
        assert.equal(wrong.status, 'rejected');
        assert.equal(wrong.value.code, 'ACTION_INPUT');
        assert.equal(wrong.value.errors.length, 1);
        assert.equal(wrong.value.errors[0].path, '/quantity');
        assert.match(wrong.value.errors[0].message, /integer/);
        assert.equal(wrong.value.errorCount, 1);
        assert.match(wrong.value.expected, /quantity: 整数/);
        assert.ok(Buffer.byteLength(wrong.value.expected) <= 1024);

        const many = await ctx.actions.execute('d5.many', {}, env.context('d5-many'));
        assert.equal(many.status, 'rejected');
        assert.equal(many.value.errors.length, 5);
        assert.equal(many.value.errorCount, 7);
        assert.match(many.value.expected, /field0: 字符串/);
        assert.ok(Buffer.byteLength(JSON.stringify(many.value)) < 65536);

        const valid = await ctx.actions.execute('d5.quantity', { quantity: 3 }, env.context('d5-valid'));
        assert.equal(valid.status, 'completed');
        assert.deepEqual(valid.value, { quantity: 3 });
    }
    finally {
        await env.close();
    }
});
