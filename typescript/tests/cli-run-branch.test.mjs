// CLI run branch: fork a completed run twice and resume both parallel worlds.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const cli = resolve('.build/cli/dist/main.js');
const skip = existsSync(cli) ? false : 'BUILD_REQUIRED: 先运行 python3 -B scripts/build.py 生成 .build/cli';
const profileDir = resolve('profiles');

function moduleLock(directory) {
    const packages = {};
    for (const name of ['host', 'core', 'extras', 'social']) {
        const meta = JSON.parse(readFileSync(join('.build', name, 'generated/plugin.meta.json'), 'utf8'));
        packages['@society0/' + name] = meta.source;
    }
    const path = join(directory, 'module-lock.json');
    return { path, value: { lockfile: pathToFileURL(path).href, packages } };
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

async function fixture() {
    const directory = await mkdtemp(join(tmpdir(), 'cli-run-branch-'));
    const profile = JSON.parse(await readFile(join(profileDir, 'rule-sqlite.json'), 'utf8'));
    profile.lockfile = './module-lock.json';
    repeatProposals(profile, 6);
    await writeFile(join(directory, 'profile.json'), JSON.stringify(profile));
    const lock = moduleLock(directory);
    await writeFile(lock.path, JSON.stringify(lock.value));
    return { directory, profile: join(directory, 'profile.json'), document: profile };
}

function call(args) {
    return spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8', timeout: 240_000 });
}

function json(result) {
    assert.equal(result.error, undefined, String(result.error));
    assert.equal(result.stderr, '', result.stderr);
    return JSON.parse(result.stdout);
}

async function waitFor(runDir, predicate, timeout = 180_000) {
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
        const result = call(['run', 'status', '--dir', runDir, '--json']);
        if (result.status === 0) {
            last = JSON.parse(result.stdout);
            if (predicate(last)) return last;
        }
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw Error('status timeout: ' + JSON.stringify(last));
}

test('run branch forks a complete point into resumable paused runs', { skip, timeout: 600_000 }, async () => {
    const { directory, profile, document } = await fixture();
    const source = join(directory, 'source');
    json(call(['run', 'start', '--profile', profile, '--dir', source, '--ticks', '2', '--detach', '--json']));
    const sourceStatus = await waitFor(source, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(sourceStatus.state, 'completed', JSON.stringify(sourceStatus));
    assert.equal(sourceStatus.completedTicks, 2);
    const sourceDocument = JSON.parse(await readFile(join(source, 'entry-status.json'), 'utf8'));

    const targets = [];
    const first = join(directory, 'branch-a');
    const firstOutput = json(call(['run', 'branch', '--dir', source, '--target-dir', first, '--json']));
    assert.equal(firstOutput.schema, 'society0.run-branch/1');
    assert.equal(firstOutput.dir, first);
    assert.equal(firstOutput.sourceDir, source);
    assert.equal(firstOutput.runId, 'branch-a');
    assert.equal(firstOutput.state, 'paused');
    assert.equal(typeof firstOutput.operationId, 'string');
    assert.equal(typeof firstOutput.sourcePointId, 'string');
    assert.equal(typeof firstOutput.targetPointId, 'string');
    assert.equal(firstOutput.generation, 'g2');
    assert.equal(typeof firstOutput.controlSequence, 'string');
    assert.equal(typeof firstOutput.actorCount, 'number');
    const firstStatus = JSON.parse(await readFile(join(first, 'entry-status.json'), 'utf8'));
    assert.equal(firstStatus.state, 'paused');
    assert.equal(firstStatus.completedTicks, 2);
    assert.equal(firstStatus.lineage, sourceDocument.lineage);
    targets.push({ dir: first, output: firstOutput });

    const sqlite = nested(document.patches[0].insert, '@society0/core/sqlite');
    const patches = join(directory, 'branch-patches.json');
    await writeFile(patches, JSON.stringify([{ id: sqlite.id, config: { ...sqlite.config, maxTransactionMs: 31000 } }]));
    const second = join(directory, 'branch-b');
    const secondOutput = json(call(['run', 'branch', '--dir', source, '--target-dir', second, '--patches', patches, '--json']));
    assert.equal(secondOutput.schema, 'society0.run-branch/1');
    assert.equal(secondOutput.dir, second);
    assert.equal(secondOutput.state, 'paused');
    const secondStatus = JSON.parse(await readFile(join(second, 'entry-status.json'), 'utf8'));
    assert.equal(secondStatus.state, 'paused');
    assert.equal(secondStatus.completedTicks, 2);
    assert.equal(secondStatus.lineage, sourceDocument.lineage);
    targets.push({ dir: second, output: secondOutput });

    for (const target of targets) {
        json(call(['run', 'resume', '--dir', target.dir, '--ticks', '4', '--detach', '--json']));
        const completed = await waitFor(target.dir, value => value.state === 'completed' || value.state === 'failed');
        assert.equal(completed.state, 'completed', JSON.stringify(completed));
        assert.equal(completed.completedTicks, 4);
        const result = json(call(['run', 'result', '--dir', target.dir, '--json']));
        assert.equal(result.schema, 'society0.run-result/1');
        assert.equal(result.state, 'completed');
        const status = JSON.parse(await readFile(join(target.dir, 'entry-status.json'), 'utf8'));
        assert.equal(status.lineage, sourceDocument.lineage);
    }
    assert.notEqual(targets[0].output.runId, targets[1].output.runId);
    assert.notEqual(targets[0].output.targetPointId, targets[1].output.targetPointId);
});
