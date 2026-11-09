// cases: D12-1 run start uses the same migration list as check for @society0/extras/workspace
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { call, fixture, flatten, json, skip, socialReplay, waitFor, worldFile } from './d7-support.mjs';

/** Keep the package name as the entry id, the shape that exposed the check/run split. */
function workspaceWithPackageId(profile) {
    socialReplay(profile);
    const workspace = flatten(profile.patches[0].insert).find(entry => entry.name === '@society0/extras/workspace');
    assert.ok(workspace, 'social-sqlite profile must include @society0/extras/workspace');
    workspace.id = workspace.name;
}

test('D12-1 run start completes when the workspace entry uses its package name as id', { skip, timeout: 300_000 }, async () => {
    const { directory, profile } = await fixture('social-sqlite', workspaceWithPackageId);
    const checked = json(call(['check', '--profile', profile, '--json']));
    assert.equal(checked.ok, true, JSON.stringify(checked.missing));
    const world = await worldFile(directory);
    const runDir = join(directory, 'run-workspace');
    const started = json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--world', world, '--ticks', '1', '--detach', '--json']));
    assert.equal(started.schema, 'society0.run/1');
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));
    assert.equal(status.completedTicks, 1);
});
