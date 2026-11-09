// cases: D15 check --world dry-runs run-start root creation; terminal status is not overwritten
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { call, fixture, flatten, json, skip, socialReplay, waitFor, worldFile } from './d7-support.mjs';

function checkWithCredentials(args) {
    process.env.D15_CHECK_CREDENTIALS = '{}';
    try { return call([...args, '--credentials-env', 'D15_CHECK_CREDENTIALS', '--json']); }
    finally { delete process.env.D15_CHECK_CREDENTIALS; }
}

function socialEntry(profile, name) {
    const entry = flatten(profile.patches[0].insert).find(item => item.name === name);
    assert.ok(entry, 'profile must include ' + name);
    return entry;
}

test('D15 check --world fails when an actor is listed in two pairing groups', { skip, timeout: 240_000 }, async () => {
    const { directory, profile } = await fixture('social-sqlite', value => {
        socialEntry(value, '@society0/social/social-init').config = {
            startRound: 1,
            pairingGroups: [['alice'], ['alice']]
        };
    });
    const world = await worldFile(directory);
    const result = checkWithCredentials(['check', '--profile', profile, '--world', world]);
    assert.equal(result.status, 2, result.stderr);
    const report = JSON.parse(result.stdout);
    assert.equal(report.ok, false);
    const problem = report.missing.find(item => item.kind === 'world' && item.id === 'alice');
    assert.ok(problem, JSON.stringify(report.missing));
    assert.match(problem.hint, /alice/);
    assert.match(problem.hint, /多组配对|一组/);
});

test('D15 check --world passes when the world can create the run root', { skip, timeout: 240_000 }, async () => {
    const { directory, profile } = await fixture('social-sqlite');
    const world = await worldFile(directory);
    const result = checkWithCredentials(['check', '--profile', profile, '--world', world]);
    assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(result.stdout);
    assert.equal(report.ok, true, JSON.stringify(report.missing));
    assert.equal(report.missing.some(item => item.kind === 'world'), false);
});

test('D15 terminal status stays completed after the worker exits', { skip, timeout: 600_000 }, async () => {
    for (let attempt = 0; attempt < 3; attempt++) {
        const { directory, profile } = await fixture('social-sqlite', socialReplay);
        const world = await worldFile(directory);
        const runDir = join(directory, 'run-status-' + attempt);
        json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--world', world, '--ticks', '1', '--detach', '--json']));
        const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed' || value.state === 'paused');
        assert.equal(status.state, 'completed', JSON.stringify(status));
        assert.ok(status.lastReason, JSON.stringify(status));
        // 定时器的 running 回调若晚于终态落盘，会在这段时间内把状态改回 running。
        const deadline = Date.now() + 1000;
        while (Date.now() < deadline) {
            const current = json(call(['run', 'status', '--dir', runDir, '--json']));
            assert.equal(current.state, 'completed', JSON.stringify(current));
            assert.equal(current.lastReason, status.lastReason);
            await new Promise(resolve => setTimeout(resolve, 50));
        }
        const result = json(call(['run', 'result', '--dir', runDir, '--json']));
        assert.equal(result.result.status, 'completed');
    }
});

test('D15 a source that runs out before --ticks completes early with round-robin exhausted', { skip, timeout: 300_000 }, async () => {
    const { directory, profile } = await fixture('social-sqlite', value => {
        socialReplay(value);
        socialEntry(value, '@society0/social/round-robin').config = {
            ...socialEntry(value, '@society0/social/round-robin').config,
            rounds: 1
        };
    });
    const world = await worldFile(directory);
    const runDir = join(directory, 'run-exhausted');
    json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--world', world, '--ticks', '10', '--detach', '--json']));
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));
    assert.equal(status.lastReason, 'round-robin exhausted');
    assert.ok(status.completedTicks < 10, JSON.stringify(status));
});
