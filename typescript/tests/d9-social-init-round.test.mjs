import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture } from './pr10-support.mjs';

const pages = { maxItems: 10, maxResponseBytes: 65536 };

test('D9 social-init starts a pairing round through the bootstrap initializer', async () => {
    const f = await fixture({
        actors: [{ id: 'a', attributes: {} }, { id: 'b', attributes: {} }],
        edges: [],
        social: { pairingGroups: [['a', 'b']], pairingSessionMinutes: 10 },
        init: { startRound: 1, pairingGroups: [['a', 'b']], pairingSessionMinutes: 10 },
    });
    try {
        const view = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            const a = await f.ctx.social.pairing(view, 'a');
            const b = await f.ctx.social.pairing(view, 'b');
            assert.equal(a.canConverse, true);
            assert.equal(a.currentPartner, 'b');
            assert.equal(a.currentRound, 1);
            assert.equal(b.canConverse, true);
            assert.equal(b.currentPartner, 'a');
            assert.equal((await f.ctx.social.conversation(view, 'b', pages)).total, '0');
        } finally {
            await view.close();
        }
        const operation = await f.ctx.history.current();
        assert.ok(operation.requiredOperations.includes('social.seed/round/1'), JSON.stringify(operation.requiredOperations));
    } finally {
        await f.host.close();
    }
});
