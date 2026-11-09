// cases: D5 platform entry
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { mkdtemp, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const cli = resolve('.build/cli/dist/main.js');
const skip = existsSync(cli) ? false : 'BUILD_REQUIRED: 先运行 python3 -B scripts/build.py 生成 .build/cli';
const profileDir = resolve('profiles');
const snapshotDir = resolve('tests/d5-snapshots');

function moduleLock(directory) {
    const packages = {};
    for (const name of ['host', 'core', 'extras', 'social']) {
        const meta = JSON.parse(readFileSync(join('.build', name, 'generated/plugin.meta.json'), 'utf8'));
        packages['@society0/' + name] = meta.source;
    }
    const path = join(directory, 'module-lock.json');
    return { path, value: { lockfile: pathToFileURL(path).href, packages } };
}

async function fixture(base, mutate = () => {}) {
    const directory = await mkdtemp(join(tmpdir(), 'd5-check-'));
    const profile = JSON.parse(await readFile(join(profileDir, base + '.json'), 'utf8'));
    profile.lockfile = './module-lock.json';
    mutate(profile);
    await writeFile(join(directory, 'profile.json'), JSON.stringify(profile));
    const lock = moduleLock(directory);
    await writeFile(lock.path, JSON.stringify(lock.value));
    return { directory, profile: join(directory, 'profile.json') };
}
function nested(entries, name) {
    for (const entry of entries) {
        if (entry.group) {
            const found = nested(entry.config ?? [], name);
            if (found) return found;
        }
        else if (entry.name === name) return entry;
    }
}
function repeatProposals(profile, count) {
    const sequence = nested(profile.patches[0].insert, '@society0/core/proposal-sequence');
    const template = sequence.config.proposals[0];
    sequence.config.proposals = Array.from({ length: count }, (_, index) => {
        const proposal = structuredClone(template);
        proposal.id = 'p' + (index + 1);
        proposal.payload.nextCursor = String(index + 1);
        for (const [phaseIndex, phase] of (proposal.payload.phases ?? []).entries()) {
            phase.id = 'act-' + (index + 1) + '-' + (phaseIndex + 1);
            for (const [activationIndex, activation] of phase.activations.entries()) {
                activation.id = 'p' + (index + 1) + '-' + (activationIndex + 1);
                activation.momentId = 'm' + (index + 1);
            }
        }
        return proposal;
    });
}
function call(args, options = {}) {
    return spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8', timeout: 120_000, ...options });
}
function json(result) {
    assert.equal(result.error, undefined, String(result.error));
    assert.equal(result.stderr, '', result.stderr);
    return JSON.parse(result.stdout);
}
function check(profile, extra = [], status = 0) {
    const result = call(['check', '--profile', profile, ...extra, '--json']);
    assert.equal(result.status, status, result.stderr);
    return { result, report: JSON.parse(result.stdout) };
}

test('D5 check reports slot, service, migration, plugin, config and credential problems with hints', { skip, timeout: 240_000 }, async () => {
    const complete = await fixture('rule-sqlite');
    const full = check(complete.profile).report;
    assert.equal(full.schema, 'society0.check/1');
    assert.equal(full.ok, true);
    assert.deepEqual(full.missing, []);
    await assert.rejects(stat(join(complete.directory, 'run')), { code: 'ENOENT' });
    assert.equal((await readdir(complete.directory)).sort().join(','), ['module-lock.json', 'profile.json'].sort().join(','));

    const broken = await fixture('rule-sqlite', profile => {
        profile.patches[0].insert.push({ id: 'ghost', name: '@society0/ghost', config: {} });
        const run = profile.patches[0].insert[0];
        run.config = run.config.filter(entry => entry.id !== 'thread' && entry.id !== 'artifacts-local');
        nested(profile.patches[0].insert, '@society0/core/proposal-sequence').config.bogus = 1;
        const proposal = nested(profile.patches[0].insert, '@society0/core/proposal-sequence').config.proposals[0];
        proposal.payload.phases[0].activations[0].driver = 'llm/v1';
    });
    const report = check(broken.profile, [], 2).report;
    assert.equal(report.ok, false);
    for (const [kind, id] of [['plugin', '@society0/ghost'], ['service', 'threads'], ['config', 'proposal-sequence/bogus'], ['slot', 'core/drivers/llm/v1'], ['migration', 'artifacts-local']]) {
        const item = report.missing.find(entry => entry.kind === kind && entry.id === id);
        assert.ok(item, `${kind}:${id}: ${JSON.stringify(report.missing)}`);
        assert.equal(typeof item.hint, 'string');
        assert.ok(item.hint.length > 0, kind);
    }

    const credential = await fixture('social-sqlite');
    const social = check(credential.profile, [], 2).report;
    assert.equal(social.ok, false);
    const missing = social.missing.find(entry => entry.kind === 'credential' && entry.id === 'authorized-local');
    assert.ok(missing, JSON.stringify(social.missing));
    assert.ok(missing.hint.length > 0);
});

test('D5 check output snapshot is stable', { skip, timeout: 120_000 }, async () => {
    const { report } = check((await fixture('rule-sqlite')).profile);
    const snapshot = JSON.parse(await readFile(join(snapshotDir, 'check-rule-sqlite.json'), 'utf8'));
    assert.deepEqual(report, snapshot);
});

test('D5 run start/status/result completes two ticks through versioned JSON', { skip, timeout: 180_000 }, async () => {
    const { directory, profile } = await fixture('rule-sqlite', value => repeatProposals(value, 2));
    const runDir = join(directory, 'run2');
    const started = json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--ticks', '2', '--detach', '--json']));
    assert.equal(started.schema, 'society0.run/1');
    assert.equal(started.dir, runDir);
    assert.equal(typeof started.pid, 'number');
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));
    assert.equal(status.completedTicks, 2);
    assert.equal(status.frontier.completedCount, '2');
    const result = json(call(['run', 'result', '--dir', runDir, '--json']));
    assert.equal(result.schema, 'society0.run-result/1');
    assert.equal(result.state, 'completed');
    assert.equal(result.result.status, 'completed');
    assert.equal(result.result.cut.frontier.completedCount, '2');
});

test('D5 run kill leaves paused status and resume finishes from the frontier', { skip, timeout: 300_000 }, async () => {
    const { directory, profile } = await fixture('rule-sqlite', value => repeatProposals(value, 200));
    const runDir = join(directory, 'run3');
    const started = json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--ticks', '200', '--detach', '--json']));
    const points = join(runDir, 'history', 'points');
    const deadline = Date.now() + 60_000;
    let published = false;
    while (Date.now() < deadline) {
        try { if ((await readdir(points)).some(name => name.endsWith('.json'))) { published = true; break; } }
        catch { /* not created yet */ }
        await delay(5);
    }
    assert.equal(published, true, 'root complete point was not published');
    try { process.kill(started.pid, 'SIGTERM'); }
    catch { /* process may have exited between the poll and the signal */ }
    const paused = await waitFor(runDir, value => value.state === 'paused' || value.state === 'failed' || value.state === 'completed');
    assert.ok(['paused', 'failed'].includes(paused.state), JSON.stringify(paused));
    const resumed = json(call(['run', 'resume', '--dir', runDir, '--detach', '--json']));
    assert.equal(resumed.schema, 'society0.run/1');
    const completed = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(completed.state, 'completed', JSON.stringify(completed));
    assert.equal(completed.completedTicks, 200);
    const result = json(call(['run', 'result', '--dir', runDir, '--json']));
    assert.equal(result.result.status, 'completed');
});

test('D5 platform JSON envelopes keep their keys and errors use the error schema', { skip, timeout: 120_000 }, async () => {
    const missing = call(['run', 'status', '--dir', join(tmpdir(), 'd5-does-not-exist'), '--json']);
    assert.equal(missing.status, 1);
    const error = JSON.parse(missing.stderr);
    assert.deepEqual(Object.keys(error).sort(), ['code', 'hint', 'message', 'schema']);
    assert.equal(error.schema, 'society0.error/1');
    assert.equal(error.code, 'RUN_NOT_FOUND');

    const { directory, profile } = await fixture('rule-sqlite');
    const runDir = join(directory, 'run4');
    json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--ticks', '1', '--detach', '--json']));
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.deepEqual(Object.keys(status).sort(), ['completedTicks', 'dir', 'frontier', 'lastReason', 'pid', 'runId', 'schema', 'state', 'updatedAt']);
    assert.equal(status.schema, 'society0.run-status/1');
});

async function waitFor(runDir, predicate, timeout = 120_000) {
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
        const result = call(['run', 'status', '--dir', runDir, '--json']);
        if (result.status === 0) {
            last = JSON.parse(result.stdout);
            if (predicate(last)) return last;
        }
        await delay(100);
    }
    throw Error('status timeout: ' + JSON.stringify(last));
}
function delay(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
