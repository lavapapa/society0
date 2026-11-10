// cases: D20 budget_exhausted settles an activation when required work is empty, publishes a complete point, and resume never reports HISTORY_DIRTY
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { call, cli, fixture, flatten, json, skip as buildSkip, socialReplay, waitFor, worldFile } from './d7-support.mjs';

const requireE2E = process.env.SOCIETY0_REQUIRE_E2E === '1';
const skip = requireE2E ? false : buildSkip;

test('D20 budget-exhausted tick settles, completes, and resumes without HISTORY_DIRTY', { skip, timeout: 300_000 }, async () => {
    assert.ok(existsSync(cli), 'BUILD_REQUIRED: 先运行 python3 -B scripts/build.py 生成 .build/cli');
    const { directory, profile } = await fixture('social-sqlite', value => {
        socialReplay(value);
        const entries = flatten(value.patches[0].insert);
        const driver = entries.find(entry => entry.name === '@society0/core/driver-llm');
        driver.config = { ...driver.config, requiredActions: [], maxRequests: 1 };
        const round = entries.find(entry => entry.name === '@society0/social/round-robin');
        round.config = { ...round.config, rounds: 2 };
    });
    const world = await worldFile(directory);

    const runDir = join(directory, 'run-d20-budget');
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
    assert.equal(result.activations.limits.requests, result.activations.total, JSON.stringify(result.activations));
    const entries = result.result.activations.entries;
    assert.equal(entries.length, result.activations.total, JSON.stringify(entries));
    for (const entry of entries) {
        assert.equal(entry.reason, 'success', JSON.stringify(entry));
        assert.equal(entry.requiredSatisfied, true, JSON.stringify(entry));
        assert.equal(entry.limit, 'requests', JSON.stringify(entry));
        assert.ok(entry.warnings.includes('BUDGET_EXHAUSTED'), JSON.stringify(entry));
    }

    const resumed = call(['run', 'resume', '--dir', runDir, '--detach', '--json']);
    assert.equal(resumed.status, 1, resumed.stdout);
    assert.doesNotMatch(resumed.stderr, /HISTORY_DIRTY/);
    const error = JSON.parse(resumed.stderr);
    assert.equal(error.code, 'RUN_COMPLETED', resumed.stderr);
});
