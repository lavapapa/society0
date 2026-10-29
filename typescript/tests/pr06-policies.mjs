import { test } from 'node:test';
import assert from 'node:assert/strict';
import Ticks from '../../../evidence/pr06/stage/dist/clock-ticks/index.js';
import EventClock from '../../../evidence/pr06/stage/dist/clock-event/index.js';
import Sequence from '../../../evidence/pr06/stage/dist/proposal-sequence/index.js';
import Events from '../../../evidence/pr06/stage/dist/proposal-events/index.js';
import { createHost, OwnedResources } from '../../../evidence/pr10/stage/host/dist/index.js';
test('PR06 integer clocks preserve values beyond safe number and stable offsets', async () => {
    const h = await createHost({ boot: [] });
    try {
        await h.ctx.plugin(Ticks);
        assert.equal(h.ctx.logicalClock.advance('9007199254740993', null), '9007199254740994');
        assert.throws(() => h.ctx.logicalClock.compare(9007199254740992, '0'), /CLOCK/);
    }
    finally {
        await h.close();
    }
    const e = await createHost({ boot: [] });
    try {
        await e.ctx.plugin(EventClock);
        assert.equal(e.ctx.logicalClock.compare({ time: '4', offset: '9007199254740993' }, { time: '4', offset: '9007199254740994' }), -1);
    }
    finally {
        await e.close();
    }
});
test('PR06 proposal read preserves cursor; event ack requires covering persisted cut', async () => {
    const h = await createHost({ boot: [] });
    try {
        await h.ctx.plugin(OwnedResources);
        await h.ctx.plugin(Events, { maxPending: 2, maxBytes: 1024 });
        const source = h.ctx.proposalSource;
        await source.push({ id: 'e1', offset: '1', time: '9', payload: { x: 1 } });
        const first = await source.waitProposal('0', new AbortController().signal);
        assert.equal(first.proposal.cursor, '0');
        await assert.rejects(source.acknowledge(first.proposal, { frontier: { pendingAcknowledgements: [] } }), /ACK/);
        await source.release(first.proposal);
        assert.equal((await source.waitProposal('0', new AbortController().signal)).proposal.id, 'e1');
    }
    finally {
        await h.close();
    }
});
import CompositionStrategies from '../../../evidence/pr10/stage/host/dist/composition-strategies/index.js';
import SlotStrategies from '../../../evidence/pr10/stage/host/dist/slot-strategies/index.js';
import Slots from '../../../evidence/pr10/stage/host/dist/slots/index.js';
import Drivers from '../../../evidence/pr06/stage/dist/drivers/index.js';
test('PR06 named driver holds only selected provider; unrelated provider retires independently', async () => {
    const h = await createHost({ boot: [] });
    let lease;
    try {
        for (const plugin of [OwnedResources, CompositionStrategies, SlotStrategies, Slots, Drivers])
            await h.ctx.plugin(plugin);
        const driver = { async activate() { return { status: 'completed', value: null, evidence: null }; } };
        await h.ctx.plugin({ inject: ['drivers'], apply(ctx) { ctx.drivers.register('one', driver); } });
        const other = await h.ctx.plugin({ inject: ['drivers'], apply(ctx) { ctx.drivers.register('two', driver); } });
        lease = await h.ctx.drivers.acquire('one', { runId: 'r', generation: 'g', operationId: 'op', signal: new AbortController().signal });
        let timer;
        try {
            await Promise.race([other.dispose(), new Promise((_, reject) => timer = setTimeout(() => reject(Error('unrelated driver retained')), 1000))]);
        }
        finally {
            clearTimeout(timer);
        }
        assert.equal(lease.value, driver);
    }
    finally {
        await lease?.release();
        await h.close();
    }
});
test('PR06 event delivery behind an outstanding reservation has an explicit late policy', async () => {
    const h = await createHost({ boot: [] });
    try {
        await h.ctx.plugin(OwnedResources);
        await h.ctx.plugin(Events);
        await h.ctx.proposalSource.push({ id: 'three', offset: '3', time: '9', payload: null });
        await h.ctx.proposalSource.waitProposal('0', new AbortController().signal);
        await assert.rejects(h.ctx.proposalSource.push({ id: 'two', offset: '2', time: '9', payload: null }), /EVENT_LATE_OFFSET/);
    }
    finally {
        await h.close();
    }
});
