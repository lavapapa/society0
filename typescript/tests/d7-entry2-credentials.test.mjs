// cases: D7-3 --credentials-env registers credentials in memory only
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { call, fixture, hasSecret, json, skip, socialReplay, waitFor, worldFile } from './d7-support.mjs';

const ENV = 'D7_ENTRY2_CREDENTIALS';
const SECRET = 'sk-entry2-do-not-persist-7f3c';

test('D7-3 credentials from an environment variable stay out of the run directory and log', { skip, timeout: 300_000 }, async () => {
    const { directory, profile } = await fixture('social-sqlite', socialReplay);
    const world = await worldFile(directory);
    const runDir = join(directory, 'run-credentials');
    process.env[ENV] = JSON.stringify({ 'authorized-local': { type: 'api_key', key: SECRET } });
    try {
        const started = json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--world', world, '--ticks', '1', '--credentials-env', ENV, '--detach', '--json']));
        assert.equal(started.schema, 'society0.run/1');
        const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
        assert.equal(status.state, 'completed', JSON.stringify(status));
        const stored = JSON.parse(await readFile(join(runDir, 'entry-status.json'), 'utf8'));
        assert.equal(stored.credentialsEnv, ENV);
        assert.equal(hasSecret(runDir, SECRET), undefined);
        assert.equal(hasSecret(runDir + '.log', SECRET), undefined);
        assert.ok(!(await readFile(join(runDir, 'entry-status.json'), 'utf8')).includes(SECRET));
    }
    finally {
        delete process.env[ENV];
    }
});

test('D7-3 malformed credentials JSON fails before a run directory is created', { skip, timeout: 120_000 }, async () => {
    const { directory, profile } = await fixture('rule-sqlite');
    const runDir = join(directory, 'run-bad-credentials');
    process.env[ENV] = 'not-json';
    try {
        const failed = call(['run', 'start', '--profile', profile, '--dir', runDir, '--ticks', '1', '--credentials-env', ENV, '--detach', '--json']);
        assert.equal(failed.status, 1);
        assert.equal(JSON.parse(failed.stderr).code, 'CREDENTIALS_ENV_INVALID');
    }
    finally {
        delete process.env[ENV];
    }
});
