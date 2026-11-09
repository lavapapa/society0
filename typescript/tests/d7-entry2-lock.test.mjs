// cases: D7-4 local plugin packages resolved by package name from an experiment directory
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { call, fixture, flatten, json, localExperiment, skip, waitFor } from './d7-support.mjs';

const enginePackages = ['@society0/host', '@society0/core', '@society0/extras', '@society0/social'];

function addLocal(profile, name) {
    const run = profile.patches[0].insert[0];
    run.config.push({ id: 'local-plugin', name, config: {} });
}

test('D7-4 lock from an experiment directory; check and run a local plugin', { skip, timeout: 300_000 }, async () => {
    const { directory } = await fixture('rule-sqlite');
    const local = await localExperiment(directory);
    const built = await fixture('rule-sqlite', profile => addLocal(profile, local.name));
    const profile = JSON.parse(await readFile(built.profile, 'utf8'));
    profile.lockfile = pathToFileURL(local.lock).href;
    await writeFile(built.profile, JSON.stringify(profile));

    const lock = call(['lock', '--base', local.exp, '--out', local.lock, local.name, ...enginePackages, '--json']);
    assert.equal(lock.status, 0, lock.stderr);
    const written = JSON.parse(await readFile(local.lock, 'utf8'));
    assert.equal(written.packages[local.name].version, local.version);
    assert.equal(written.packages[local.name].hostAbi, 'society0/host/1');
    assert.match(written.packages[local.name].gitCommit, /^[0-9a-f]{40}$/);
    assert.equal(written.packages[local.name].dirty, true);
    assert.deepEqual(written.packages[local.name].patches, []);

    const checked = call(['check', '--profile', built.profile, '--base', local.exp, '--json']);
    assert.equal(checked.status, 0, checked.stderr);
    const report = JSON.parse(checked.stdout);
    assert.equal(report.ok, true, JSON.stringify(report.missing));
    assert.ok(report.resolved.plugins.includes(local.name), JSON.stringify(report.resolved.plugins));

    const runDir = join(directory, 'run-local');
    json(call(['run', 'start', '--profile', built.profile, '--dir', runDir, '--base', local.exp, '--ticks', '1', '--detach', '--json']));
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));
    assert.equal(status.completedTicks, 1);
});
