// cases: D18 slot dependencies derive from static api.slots declarations and config placeholders
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { call, fixture, flatten, json, localExperiment, skip, waitFor } from './d7-support.mjs';

const enginePackages = ['@society0/host', '@society0/core', '@society0/extras', '@society0/social'];

function withoutEntry(entries, name) {
    for (let index = entries.length - 1; index >= 0; index--) {
        const entry = entries[index];
        if (entry.name === name) entries.splice(index, 1);
        else if (entry.group) withoutEntry(entry.config ?? [], name);
    }
}

function generationGroup(profile) {
    const found = flatten(profile.patches[0].insert).find(entry => entry.group && entry.id === 'generation');
    assert.ok(found, 'profile must contain a generation group');
    return found;
}

function setProposalDriver(profile, driver) {
    const sequence = flatten(profile.patches[0].insert).find(entry => entry.name === '@society0/core/proposal-sequence');
    assert.ok(sequence, 'profile must contain proposal-sequence');
    for (const proposal of sequence.config.proposals)
        for (const phase of proposal.payload?.phases ?? [])
            for (const activation of phase.activations ?? [])
                activation.driver = driver;
}

const customDriverSource = `export default {
    inject: ['drivers'],
    apply(ctx) {
        ctx.drivers.register('x', {
            async activate() {
                return {
                    status: 'incomplete',
                    value: { reason: 'setup_error' },
                    evidence: { error: 'Error: SLOT_PENDING core/agent-runtimes/pi/v1', code: 'LLM_SETUP_ERROR', stage: 'runtime.acquire' },
                    requiredOperations: []
                };
            }
        });
    }
};
`;

async function customDriverProfile() {
    const { directory } = await fixture('rule-sqlite');
    const local = await localExperiment(directory, '@fixture/local-plugin', '1.0.0', { scope: 'generation', source: customDriverSource });
    await writeFile(join(local.plugin, 'plugin.api.json'), JSON.stringify({
        format: 'society-plugin-api/v1',
        entries: [{ exportPath: '.', provides: [], requires: [], slots: { provides: ['core/drivers/x'] } }]
    }));
    const built = await fixture('rule-sqlite', profile => {
        generationGroup(profile).config.push({ id: 'custom-driver', name: local.name, config: {} });
        setProposalDriver(profile, 'x');
    });
    const profile = JSON.parse(await readFile(built.profile, 'utf8'));
    profile.lockfile = pathToFileURL(local.lock).href;
    await writeFile(built.profile, JSON.stringify(profile));
    const locked = call(['lock', '--base', local.exp, '--out', local.lock, local.name, ...enginePackages, '--json']);
    assert.equal(locked.status, 0, locked.stderr);
    return { built, local };
}

test('D18 check rejects driver-llm without the configured agent runtime provider', { skip, timeout: 240_000 }, async () => {
    const missing = await fixture('social-sqlite', profile => withoutEntry(profile.patches[0].insert, '@society0/core/agent-runtime-pi'));
    const result = call(['check', '--profile', missing.profile, '--credentials-env', 'D18_CREDENTIALS', '--json'], { env: { ...process.env, D18_CREDENTIALS: '{}' } });
    assert.equal(result.status, 2, result.stderr);
    const report = JSON.parse(result.stdout);
    const problem = report.missing.find(item => item.kind === 'slot' && item.id === 'core/agent-runtimes/pi/v1');
    assert.ok(problem, JSON.stringify(report.missing));
    assert.equal(problem.code, 'SLOT_PROVIDER_MISSING');
    assert.equal(problem.requiredBy, 'driver-llm');
    assert.match(problem.hint, /@society0\/core\/agent-runtime-pi/);

    const complete = await fixture('social-sqlite');
    const ok = call(['check', '--profile', complete.profile, '--credentials-env', 'D18_CREDENTIALS', '--json'], { env: { ...process.env, D18_CREDENTIALS: '{}' } });
    assert.equal(ok.status, 0, ok.stderr);
    assert.equal(JSON.parse(ok.stdout).ok, true);
});

test('D18 check accepts a third-party driver declared with api.slots', { skip, timeout: 240_000 }, async () => {
    const { built, local } = await customDriverProfile();
    const result = call(['check', '--profile', built.profile, '--base', local.exp, '--json']);
    assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(result.stdout);
    assert.equal(report.ok, true, JSON.stringify(report.missing));
    assert.deepEqual(report.resolved.slots['core/drivers/x'], ['custom-driver']);
    assert.equal(report.missing.some(item => item.kind === 'slot' && item.id === 'core/drivers/x'), false);
});

test('D18 run result reports activation failures with code and stage', { skip, timeout: 300_000 }, async () => {
    const { built, local } = await customDriverProfile();
    const runDir = join(built.directory, 'run-d18');
    const started = call(['run', 'start', '--profile', built.profile, '--dir', runDir, '--base', local.exp, '--ticks', '1', '--detach', '--json']);
    assert.equal(started.status, 0, started.stderr);
    json(started);
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed' || value.state === 'paused');
    assert.ok(['completed', 'failed', 'paused'].includes(status.state), JSON.stringify(status));
    const result = json(call(['run', 'result', '--dir', runDir, '--json']));
    assert.equal(result.activationsNote, undefined);
    assert.equal(result.activations.total, 1);
    assert.equal(result.activations.failed, 1);
    assert.equal(result.activations.byReason.setup_error, 1);
    assert.equal(result.activations.failures.length, 1);
    assert.equal(result.activations.failures[0].actorId, 'alice');
    assert.equal(result.activations.failures[0].momentId, 'm1');
    assert.equal(result.activations.failures[0].reason, 'setup_error');
    assert.equal(result.activations.failures[0].code, 'SLOT_PENDING');
    assert.equal(result.activations.failures[0].stage, 'runtime.acquire');
    assert.equal(result.activations.failures[0].slot, 'core/agent-runtimes/pi/v1');
});
