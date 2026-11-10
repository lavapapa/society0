// cases: D20 check then run one real tick (social replay) without slot/setup failures
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { call, cli, fixture, json, skip as buildSkip, socialReplay, waitFor, worldFile } from './d7-support.mjs';

const requireE2E = process.env.SOCIETY0_REQUIRE_E2E === '1';
const skip = requireE2E ? false : buildSkip;
const FORBIDDEN = /setup_error|SLOT_[A-Z_]*|CHECK_FAILED/;

test('D20 social check then one tick completes without slot or setup failures', { skip, timeout: 300_000 }, async () => {
    assert.ok(existsSync(cli), 'BUILD_REQUIRED: 先运行 python3 -B scripts/build.py 生成 .build/cli');
    const { directory, profile } = await fixture('social-sqlite', socialReplay);
    const world = await worldFile(directory);

    const checked = call(['check', '--profile', profile, '--world', world, '--json']);
    assert.equal(checked.status, 0, checked.stderr);
    const report = JSON.parse(checked.stdout);
    assert.equal(report.ok, true, JSON.stringify(report.missing));
    assert.deepEqual(report.missing, []);

    const runDir = join(directory, 'run-d20');
    const started = json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--world', world, '--ticks', '1', '--detach', '--json']));
    assert.equal(started.schema, 'society0.run/1');

    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));
    assert.equal(status.completedTicks, 1, JSON.stringify(status));

    const result = json(call(['run', 'result', '--dir', runDir, '--json']));
    assert.equal(result.result.status, 'completed', JSON.stringify(result.result));
    assert.equal(result.result.cut.frontier.completedCount, '1', JSON.stringify(result.result.cut));
    assert.equal(result.activations.total > 0, true, JSON.stringify(result.activations));
    assert.equal(result.activations.failed, 0, JSON.stringify(result.activations));

    const text = JSON.stringify({ status, result });
    assert.doesNotMatch(text, FORBIDDEN);
});
