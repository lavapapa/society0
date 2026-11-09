// cases: D7-2 check exit codes and D7-5 actor creator rules
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { call, fixture, flatten, skip } from './d7-support.mjs';

function actorEntry(profile) {
    return flatten(profile.patches[0].insert).find(entry => entry.name === '@society0/core/actors');
}

test('D7-2 check exits 2 when ok=false, 0 when runnable, 1 when the check cannot finish', { skip, timeout: 240_000 }, async () => {
    const complete = await fixture('rule-sqlite');
    const ok = call(['check', '--profile', complete.profile, '--json']);
    assert.equal(ok.status, 0, ok.stderr);
    assert.equal(JSON.parse(ok.stdout).ok, true);

    const broken = await fixture('rule-sqlite', profile => {
        const run = profile.patches[0].insert[0];
        run.config = run.config.filter(entry => entry.id !== 'thread');
    });
    const failed = call(['check', '--profile', broken.profile, '--json']);
    assert.equal(failed.status, 2, failed.stderr);
    assert.equal(JSON.parse(failed.stdout).ok, false);

    const missing = join(await mkdtemp(join(tmpdir(), 'd7-check-')), 'absent.json');
    const internal = call(['check', '--profile', missing, '--json']);
    assert.equal(internal.status, 1);
    const error = JSON.parse(internal.stderr);
    assert.equal(error.schema, 'society0.error/1');
    assert.equal(error.code, 'FILE_NOT_FOUND');
});

test('D7-5 check reports two actor creators', { skip, timeout: 240_000 }, async () => {
    const { profile } = await fixture('rule-sqlite', value => {
        actorEntry(value).config = { seed: true };
    });
    const result = call(['check', '--profile', profile, '--json']);
    assert.equal(result.status, 2, result.stderr);
    const report = JSON.parse(result.stdout);
    const problem = report.missing.find(item => item.kind === 'actor');
    assert.ok(problem, JSON.stringify(report.missing));
    assert.match(problem.hint, /actors\.seed/);
});

test('D7-5 check reports a domain initializer without actors.seed', { skip, timeout: 240_000 }, async () => {
    const { profile } = await fixture('social-sqlite', value => {
        actorEntry(value).config = { seed: false };
    });
    const result = call(['check', '--profile', profile, '--json']);
    assert.equal(result.status, 2, result.stderr);
    const report = JSON.parse(result.stdout);
    const problem = report.missing.find(item => item.kind === 'actor');
    assert.ok(problem, JSON.stringify(report.missing));
    assert.match(problem.hint, /social\.seed|actors\.seed/);
});

test('D7-5 a social profile with actors.seed passes check', { skip, timeout: 240_000 }, async () => {
    const { profile } = await fixture('social-sqlite', value => {
        actorEntry(value).config = { seed: true };
    });
    process.env.D7_CHECK_CREDENTIALS = '{}';
    let result;
    try { result = call(['check', '--profile', profile, '--credentials-env', 'D7_CHECK_CREDENTIALS', '--json']); }
    finally { delete process.env.D7_CHECK_CREDENTIALS; }
    assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(result.stdout);
    assert.equal(report.ok, true, JSON.stringify(report.missing));
});
