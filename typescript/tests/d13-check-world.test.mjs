// cases: D13 check reads --world and validates actors referenced by initialization plugins
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { call, fixture, skip, worldFile } from './d7-support.mjs';

function checkWithCredentials(args) {
    process.env.D13_CHECK_CREDENTIALS = '{}';
    try { return call([...args, '--credentials-env', 'D13_CHECK_CREDENTIALS', '--json']); }
    finally { delete process.env.D13_CHECK_CREDENTIALS; }
}

test('D13 check warns that run start needs a world when the profile references actors', { skip, timeout: 240_000 }, async () => {
    const { profile } = await fixture('social-sqlite');
    const result = checkWithCredentials(['check', '--profile', profile]);
    assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(result.stdout);
    assert.equal(report.ok, true, JSON.stringify(report.missing));
    const warning = report.warnings.find(item => item.includes('world'));
    assert.ok(warning, JSON.stringify(report.warnings));
    assert.match(warning, /run start/);
    assert.match(warning, /alice/);
    assert.match(warning, /bob/);
});

test('D13 check reports a referenced actor missing from the world', { skip, timeout: 240_000 }, async () => {
    const { directory, profile } = await fixture('social-sqlite');
    const world = await worldFile(directory, [{ id: 'alice', attributes: { name: 'Alice' } }]);
    const result = checkWithCredentials(['check', '--profile', profile, '--world', world]);
    assert.equal(result.status, 2, result.stderr);
    const report = JSON.parse(result.stdout);
    assert.equal(report.ok, false);
    const problem = report.missing.find(item => item.kind === 'actor' && item.id === 'bob');
    assert.ok(problem, JSON.stringify(report.missing));
    assert.match(problem.hint, /world/);
});

test('D13 check accepts a world containing every referenced actor', { skip, timeout: 240_000 }, async () => {
    const { directory, profile } = await fixture('social-sqlite');
    const world = await worldFile(directory);
    const result = checkWithCredentials(['check', '--profile', profile, '--world', world]);
    assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(result.stdout);
    assert.equal(report.ok, true, JSON.stringify(report.missing));
    assert.equal(report.missing.some(item => item.kind === 'actor' && item.id === 'bob'), false);
});
