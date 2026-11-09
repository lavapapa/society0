import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fixture, request} from './pr10-support.mjs';

const actors = ['a', 'b', 'c', 'd'].map(id => ({id, attributes: {}}));

test('L2 pairing groups with the same size keep globally unique member ordinals', async () => {
  const f = await fixture({actors, edges: [], social: {pairingGroups: [['a', 'b'], ['c', 'd']], pairingSessionMinutes: 10}});
  const r = await f.ctx.executionFence.reserve({id: 'l2-pairing-groups'});
  try {
    const round = (await f.ctx.database.transaction(r.lease, request('round-1'), tx => f.ctx.social.startRound(tx, 1))).value;
    assert.deepEqual(round, {round: 1, pairs: [['a', 'b'], ['c', 'd']], successfulPairs: 2});
    const view = await f.ctx.database.snapshot({maxAgeMs: 30000});
    try {
      assert.equal((await f.ctx.social.pairing(view, 'a')).currentPartner, 'b');
      assert.equal((await f.ctx.social.pairing(view, 'c')).currentPartner, 'd');
    } finally { await view.close(); }
  } finally {
    await r.release();
    await f.host.close();
  }
});
