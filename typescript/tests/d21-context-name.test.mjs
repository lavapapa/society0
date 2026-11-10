import test from 'node:test';
import assert from 'node:assert/strict';
import {setup, request} from './pr07-support.mjs';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ContextMount} from '../.build/core/dist/mount-context/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import {ActorTools, actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';

async function harness(t) {
    const run = await setup({migrations: [actorToolsMigration({test: 'd21-context-name'})]});
    t.after(() => run.close());
    for (const plugin of [Prompts, ContextMount, ExplorationShell, ActorTools]) await run.host.ctx.plugin(plugin, {});
    let ctx;
    await run.host.ctx.plugin({inject: ['contextMount', 'actorTools'], apply(c) { ctx = c; }});
    return {run, ctx};
}

test('D21 /context names are safe, short and readable exactly as prompted', async t => {
    const {run, ctx} = await harness(t);
    const actorId = 'alice name/中文', momentId = 'day 1/午间';
    await run.ctx.database.transaction(run.reservation.lease, request('d21-context-actor'), async tx => {
        await run.ctx.actors.create(tx, {id: actorId, attributes: {}});
        await run.ctx.workspace.initialize(tx, actorId);
    });
    const context = run.context('d21-context-name', actorId, momentId);
    const lease = ctx.contextMount.freeze(['d21 context body'], context);
    try {
        const path = lease.value[0];
        assert.match(path, /^\/context\/[A-Za-z0-9._-]+-[A-Za-z0-9._-]+-\d+-\d+\.txt$/);
        assert.doesNotMatch(path, /[% ]/);
        assert.ok(path.length < 160, path);
        const [segment] = path.slice('/context/'.length).split('-');
        assert.match(segment, /\.[0-9a-f]{8}$/);
        const binding = await ctx.actorTools.bind(context);
        try {
            const read = binding.value.tools.find(item => item.name === 'read');
            assert.ok(read);
            const output = await read.execute('d21-context-read', {path}, context.signal);
            assert.equal(output.details.status, 'completed');
            assert.match(output.content[0].text, /d21 context body/);
        } finally {
            await binding.release();
        }
    } finally {
        await lease.release();
    }
});

test('D21 concurrent freezes for one actor and moment stay distinct and deterministic', async t => {
    const {run, ctx} = await harness(t);
    const actorId = 'same actor', momentId = 'same moment';
    const first = ctx.contextMount.freeze(['one'], run.context('d21-first', actorId, momentId));
    const second = ctx.contextMount.freeze(['two'], run.context('d21-second', actorId, momentId));
    try {
        assert.notEqual(first.value[0], second.value[0]);
        assert.match(first.value[0], /-0-0\.txt$/);
        assert.match(second.value[0], /-0-1\.txt$/);
    } finally {
        await first.release();
        await second.release();
    }
});
