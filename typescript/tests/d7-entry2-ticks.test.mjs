// cases: D7-1 run start --ticks applies to every profile (social replay)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { call, fixture, json, skip, socialReplay, waitFor, worldFile } from './d7-support.mjs';

test('D7-1 social replay stops after --ticks 2 at a complete point', { skip, timeout: 300_000 }, async () => {
    const { directory, profile } = await fixture('social-sqlite', socialReplay);
    const world = await worldFile(directory);
    const runDir = join(directory, 'run-ticks');
    const started = json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--world', world, '--ticks', '2', '--detach', '--json']));
    assert.equal(started.schema, 'society0.run/1');
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));
    assert.equal(status.completedTicks, 2);
    assert.equal(status.frontier.completedCount, '2');
    assert.match(String(status.lastReason), /max ticks/i);
    const result = json(call(['run', 'result', '--dir', runDir, '--json']));
    assert.equal(result.result.status, 'completed');
    assert.equal(result.result.cut.frontier.completedCount, '2');
});
