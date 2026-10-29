import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHost, OwnedResources } from '../../../evidence/pr10/stage/host/dist/index.js';
import Composition from '../../../evidence/pr10/stage/host/dist/composition-strategies/index.js';
import Strategies from '../../../evidence/pr10/stage/host/dist/slot-strategies/index.js';
import Slots from '../../../evidence/pr10/stage/host/dist/slots/index.js';
import Random from '../../../evidence/pr10/stage/core/dist/random/index.js';
import Topology from '../../../evidence/pr10/stage/social/dist/topology-edges/index.js';
import { topologySlot } from '../../../evidence/pr10/stage/social/dist/social-init/index.js';
import { fixture } from './pr10-support.mjs';
test('PR10-T01 all public topology families use deterministic actual Philox addresses', async () => {
    const host = await createHost({
        boot: []
    });
    const c = host.ctx;
    try {
        for (const plugin of [OwnedResources, Composition, Strategies, Slots])
            await c.plugin(plugin);
        await c.plugin(Random, {
            seed: 'graph-seed'
        });
        c.slots.declare({
            key: topologySlot, strategy: 'keyed', required: true, validate: value => value
        });
        const actors = Array.from({
            length: 6
        }, (_, i) => ({
            id: 'actor-' + i, attributes: {
                raw: i
            }
        }));
        const context = {
            runId: 'topology', generation: 'g', operationId: 'init', signal: new AbortController().signal
        };
        async function build(algorithm, extra = {}) {
            const owner = await c.plugin(Topology, {
                actors, algorithm, directed: false, probability: 0.3, kNeighbors: 2, mEdges: 2, maxIterations: 20, targetCV: 0.8, ...extra
            });
            const selected = await c.slots.acquire(topologySlot, context);
            try {
                return await selected.items[0].value.build({}, context);
            }
            finally {
                await selected.release();
                await owner.dispose();
            }
        }
        for(const [probability,targetCV] of [[0,1],[1,0]]) {
            const options={baseAlgorithm:'random',directed:true,probability,targetCV,maxIterations:40};
            const first=await build('cv-targeted',options),second=await build('cv-targeted',options);
            assert.deepEqual(first,second);assert.ok(first.edges.length>0);
            const edges=new Set(first.edges.map(edge=>JSON.stringify([edge.from,edge.to])));
            assert.ok(first.edges.every(edge=>edge.from!==edge.to));
            assert.ok(first.edges.every(edge=>edges.has(JSON.stringify([edge.to,edge.from]))===(targetCV===1)));
        }
        for (const name of ['random', 'small-world', 'scale-free', 'complete', 'cv-targeted']) {
            const first = await build(name), second = await build(name);
            assert.deepEqual(first, second);
            assert.equal(first.actors.length, 6);
            assert.equal(new Set(first.edges.map(edge => JSON.stringify(edge))).size, first.edges.length);
            assert.ok(first.edges.every(edge => edge.from !== edge.to));
            if (name === 'complete')
                assert.equal(first.edges.length, 30);
            if (name === 'small-world')
                assert.equal(first.edges.length, 12);
            if (name === 'scale-free')
                assert.equal(first.edges.length, 16);
        }
    }
    finally {
        await host.close();
    }
});
test('PR10-T01 unknown topology actor leaves no published root', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'pr10-unpublished-'));
    await assert.rejects(fixture({
        directory, edges: [{
                from: 'missing', to: 'a'
            }]
    }), /SOCIAL_TOPOLOGY_UNKNOWN_ACTOR/);
    assert.deepEqual(await readdir(join(directory, 'history', 'points')), []);
});
