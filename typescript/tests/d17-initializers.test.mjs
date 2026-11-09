// cases: D17 required initializers derive from the plan; actors.seed-only profiles run without plain.seed
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { call, fixture, flatten, json, localExperiment, skip, waitFor, worldFile } from './d7-support.mjs';

const enginePackages = ['@society0/host', '@society0/core', '@society0/extras', '@society0/social'];

const initializerSource = `export default {
    inject: ['initializers'],
    apply(ctx) {
        ctx.initializers.register({
            id: 'd17.seed',
            after: ['actors.seed'],
            async initialize() { return { operationIds: [] }; }
        });
    }
};
`;

function actorsSeedOnly(profile, pluginName) {
    const entries = flatten(profile.patches[0].insert);
    const actors = entries.find(entry => entry.name === '@society0/core/actors');
    actors.config = { seed: true };
    const bootstrap = entries.find(entry => entry.id === 'bootstrap');
    bootstrap.config = bootstrap.config.filter(entry => entry.name !== '@society0/core/plain-init');
    bootstrap.config.push({ id: 'd17-init', name: pluginName, config: {} });
}

test('D17 check --world and run start accept an actors.seed-only profile with a later initializer', { skip, timeout: 300_000 }, async () => {
    const { directory } = await fixture('rule-sqlite');
    const local = await localExperiment(directory, '@fixture/local-plugin', '1.0.0', { scope: 'bootstrap', source: initializerSource });
    const built = await fixture('rule-sqlite', profile => actorsSeedOnly(profile, local.name));
    const profile = JSON.parse(await readFile(built.profile, 'utf8'));
    profile.lockfile = pathToFileURL(local.lock).href;
    await writeFile(built.profile, JSON.stringify(profile));

    const locked = call(['lock', '--base', local.exp, '--out', local.lock, local.name, ...enginePackages, '--json']);
    assert.equal(locked.status, 0, locked.stderr);

    const world = await worldFile(built.directory, [{ id: 'alice', attributes: { name: 'Alice' } }]);
    const checked = call(['check', '--profile', built.profile, '--base', local.exp, '--world', world, '--json']);
    assert.equal(checked.status, 0, checked.stderr);
    const report = JSON.parse(checked.stdout);
    assert.equal(report.ok, true, JSON.stringify(report.missing));

    const runDir = join(built.directory, 'run-d17');
    json(call(['run', 'start', '--profile', built.profile, '--dir', runDir, '--base', local.exp, '--world', world, '--ticks', '1', '--detach', '--json']));
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));
    assert.equal(status.completedTicks, 1);
});
