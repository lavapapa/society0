// cases: D10-1 run start compiles a model profile with --credentials-env; D10-2 lock reads SOURCE.json without .git
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cp, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { call, fixture, flatten, json, localExperiment, skip, socialReplay, worldFile } from './d7-support.mjs';

const ENV = 'D10_SEEDS_CREDENTIALS';

async function waitSlow(runDir, predicate, timeout = 540_000) {
    const path = join(runDir, 'entry-status.json');
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
        try {
            last = JSON.parse(await readFile(path, 'utf8'));
            if (predicate(last)) return last;
        }
        catch { /* status file not written yet or mid-replace */ }
        await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw Error('status timeout: ' + JSON.stringify(last));
}

function replayWithModelProvider(profile) {
    socialReplay(profile);
    const pi = flatten(profile.patches[0].insert).find(entry => entry.name === '@society0/core/model-pi');
    pi.disabled = false;
}

test('D10-1 run start --ticks 1 completes a profile with an LLM driver and --credentials-env', { skip, timeout: 600_000 }, async () => {
    const { directory, profile } = await fixture('social-sqlite', replayWithModelProvider);
    const world = await worldFile(directory);
    const runDir = join(directory, 'run-model');
    process.env[ENV] = JSON.stringify({ 'authorized-local': { type: 'api_key', key: 'sk-d10-local-only' } });
    try {
        const started = json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--world', world, '--ticks', '1', '--credentials-env', ENV, '--detach', '--json']));
        assert.equal(started.schema, 'society0.run/1');
        const status = await waitSlow(runDir, value => value.state === 'completed' || value.state === 'failed');
        assert.equal(status.state, 'completed', JSON.stringify(status));
        assert.equal(status.completedTicks, 1);
    }
    finally {
        delete process.env[ENV];
    }
});

test('D10-2 lock records the SOURCE.json implementation for an installed package without .git', { skip, timeout: 300_000 }, async () => {
    const root = await mkdtemp(join(tmpdir(), 'd10-seeds-'));
    const { exp } = await localExperiment(root, '@fixture/installed-plugin', '2.0.0');
    const installed = join(root, 'installed', 'installed-plugin');
    await mkdir(join(root, 'installed'), { recursive: true });
    await cp(join(exp, 'plugin'), installed, { recursive: true });
    await rm(join(installed, '.git'), { recursive: true, force: true });
    assert.equal(existsSync(join(installed, '.git')), false);

    const recorded = 'a'.repeat(40);
    await writeFile(join(installed, 'SOURCE.json'), JSON.stringify({
        package: '@fixture/installed-plugin', version: '2.0.0',
        implementation: { package: '@fixture/installed-plugin', version: '2.0.0', gitCommit: recorded, dirty: false, release: false, lockfile: 'BUILD-LOCK.json', patches: [] }
    }));
    const link = join(exp, 'node_modules', '@fixture', 'installed-plugin');
    await symlink(installed, link, 'dir');

    const lockPath = join(root, 'installed-lock.json');
    const first = call(['lock', '--base', exp, '--out', lockPath, '@fixture/installed-plugin', '--json']);
    assert.equal(first.status, 0, first.stderr);
    const written = JSON.parse(await readFile(lockPath, 'utf8'));
    assert.equal(written.packages['@fixture/installed-plugin'].gitCommit, recorded);
    assert.equal(written.packages['@fixture/installed-plugin'].dirty, false);

    await rm(join(installed, 'SOURCE.json'));
    const second = call(['lock', '--base', exp, '--out', join(root, 'installed-lock-2.json'), '@fixture/installed-plugin', '--json']);
    assert.equal(second.status, 1);
    assert.equal(JSON.parse(second.stderr).code, 'LOCK_GIT');
});
