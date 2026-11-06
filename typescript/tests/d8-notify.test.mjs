import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './pr07-support.mjs';

test('D8 notify 交给策略钩子，未接策略时 wake 为 none，回执确定', async () => {
    const env = await setup();
    const { ctx } = env;
    try {
        ctx.actions.register({
            name: 'd8.send', inputSchema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] },
            outputSchema: {}, effect: 'read', available: async () => true,
            async execute(input, context) {
                return {
                    operationId: context.operationId, status: 'completed', value: { sent: true }, original: null, artifacts: [],
                    notify: [{ actorId: 'b', reason: 'message', ref: { messageId: 'm1', text: input.text } }],
                };
            },
        });
        const plain = await ctx.actions.execute('d8.send', { text: 'hi' }, env.context('d8-no-policy'));
        assert.equal(plain.status, 'completed');
        assert.deepEqual(plain.delivery, { recipients: [{ actorId: 'b', visibleRevision: '1' }], wake: 'none' });
        assert.equal(plain.notify.length, 1);

        const seen = [];
        const dispose = ctx.actions.provideNotifier({
            id: 'test-policy',
            async deliver(intent) {
                seen.push(intent);
                return 'scheduled';
            },
        });
        try {
            const woke = await ctx.actions.execute('d8.send', { text: 'hi' }, env.context('d8-policy'));
            assert.equal(woke.delivery.wake, 'scheduled');
            assert.deepEqual(woke.delivery.recipients, [{ actorId: 'b', visibleRevision: '1' }]);
            assert.equal(seen.length, 1);
            assert.equal(seen[0].cause, 'notify');
            assert.equal(seen[0].actorId, 'b');
            assert.equal(seen[0].momentId, 'm');
            assert.deepEqual(seen[0].input, { messageId: 'm1', text: 'hi' });

            const again = await ctx.actions.execute('d8.send', { text: 'hi' }, env.context('d8-policy-2'));
            assert.deepEqual(again.delivery, woke.delivery);
            assert.equal(JSON.stringify(again).includes('undefined'), false);
        }
        finally {
            await dispose();
        }
    }
    finally {
        await env.close();
    }
});
