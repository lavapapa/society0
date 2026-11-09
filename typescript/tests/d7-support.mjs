// Shared fixtures for the D7 platform-entry round.
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { mkdtemp, readFile, writeFile, mkdir, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

export const cli = resolve('.build/cli/dist/main.js');
export const skip = existsSync(cli) ? false : 'BUILD_REQUIRED: 先运行 python3 -B scripts/build.py 生成 .build/cli';
export const profileDir = resolve('profiles');
export const usage = { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

export function assistant(content, stopReason = 'stop') {
    return { role: 'assistant', api: 'openai-completions', provider: 'replay', model: 'social-replay', content, stopReason, usage, timestamp: 0 };
}

export function flatten(entries) {
    return entries.flatMap(entry => [entry, ...(entry.group ? flatten(entry.config ?? []) : [])]);
}

export function moduleLock(directory) {
    const packages = {};
    for (const name of ['host', 'core', 'extras', 'social']) {
        const meta = JSON.parse(readFileSync(join('.build', name, 'generated/plugin.meta.json'), 'utf8'));
        packages['@society0/' + name] = meta.source;
    }
    const path = join(directory, 'module-lock.json');
    return { path, value: { lockfile: pathToFileURL(path).href, packages } };
}

export async function fixture(base, mutate = () => {}) {
    const directory = await mkdtemp(join(tmpdir(), 'd7-entry2-'));
    const profile = JSON.parse(await readFile(join(profileDir, base + '.json'), 'utf8'));
    profile.lockfile = './module-lock.json';
    mutate(profile);
    await writeFile(join(directory, 'profile.json'), JSON.stringify(profile));
    const lock = moduleLock(directory);
    await writeFile(lock.path, JSON.stringify(lock.value));
    return { directory, profile: join(directory, 'profile.json'), lock };
}

/** social-sqlite switched to the explicit replay provider; round-robin keeps producing past the tick cap. */
export function socialReplay(profile) {
    const entries = flatten(profile.patches[0].insert);
    const run = entries.find(entry => entry.id === 'run');
    const pi = entries.find(entry => entry.name === '@society0/core/model-pi');
    pi.disabled = true;
    const driver = entries.find(entry => entry.name === '@society0/core/driver-llm');
    driver.config = { ...driver.config, provider: 'replay/v1', model: 'social-replay', maxRequests: 4, maxToolCalls: 2 };
    const round = entries.find(entry => entry.name === '@society0/social/round-robin');
    round.config = { ...round.config, rounds: 5 };
    const actors = entries.find(entry => entry.name === '@society0/core/actors');
    actors.config = { ...(actors.config ?? {}), seed: true };
    const publish = index => assistant([{ type: 'toolCall', id: 'publish-' + index, name: 'act', arguments: { action: 'social.publish', input: { content: 'entry2 replay post ' + index } } }], 'toolUse');
    run.config.push({
        id: 'replay', name: '@society0/core/model-replay',
        config: { name: 'replay/v1', fixtures: { 'social-replay': [publish(1), assistant([{ type: 'text', text: 'posted 1' }]), publish(2), assistant([{ type: 'text', text: 'posted 2' }]), publish(3), assistant([{ type: 'text', text: 'posted 3' }])] } }
    });
}

export async function worldFile(directory, actors = [{ id: 'alice', attributes: { name: 'Alice' } }, { id: 'bob', attributes: { name: 'Bob' } }]) {
    const path = join(directory, 'world.json');
    await writeFile(path, JSON.stringify({ actors: actors.map(actor => ({ ...actor, source: actor.source ?? { kind: 'fixture', ref: actor.id } })) }));
    return path;
}

export function call(args, options = {}) {    return spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8', timeout: 240_000, ...options });
}
export function json(result) {
    assert.equal(result.error, undefined, String(result.error));
    assert.equal(result.stderr, '', result.stderr);
    return JSON.parse(result.stdout);
}

export async function waitFor(runDir, predicate, timeout = 180_000) {
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

export async function localExperiment(directory, name = '@fixture/local-plugin', version = '1.2.3', options = {}) {
    const exp = join(directory, 'experiment');
    const plugin = join(exp, 'plugin');
    await mkdir(join(plugin), { recursive: true });
    await writeFile(join(plugin, 'package.json'), JSON.stringify({
        name, version, type: 'module', exports: { '.': './index.js', './package.json': './package.json', './plugin.meta.json': './plugin.meta.json', './plugin.schema.json': './plugin.schema.json', './plugin.api.json': './plugin.api.json' },
        society: { plugin: { metadata: './plugin.meta.json', configSchema: './plugin.schema.json', api: './plugin.api.json' } },
        peerDependencies: { '@society0/host': '0.1.0' }
    }));
    await writeFile(join(plugin, 'index.js'), options.source ?? 'export default { apply() {} };\n');
    const git = args => spawnSync('git', ['-C', plugin, ...args], { encoding: 'utf8' });
    git(['init', '-q']);
    git(['-c', 'user.email=entry2@example.invalid', '-c', 'user.name=entry2', 'add', 'index.js', 'package.json']);
    git(['-c', 'user.email=entry2@example.invalid', '-c', 'user.name=entry2', 'commit', '-q', '-m', 'local plugin']);
    const commit = git(['rev-parse', 'HEAD']).stdout.trim();
    const source = { package: name, version, gitCommit: commit, dirty: true, lockfile: 'BUILD-LOCK.json', patches: [], hostAbi: 'society0/host/1' };
    const metadata = {
        title: 'local-plugin', summary: 'local experiment plugin', configVersion: 1, configPolicy: [], scope: options.scope ?? 'run', hostAbi: 'society0/host/1'
    };
    await writeFile(join(plugin, 'plugin.meta.json'), JSON.stringify({
        format: 'society-plugin-metadata/v1', package: { name, version }, source,
        entries: [{ exportPath: '.', metadata, configSchemaPointer: '#/schema/entries/0/schema', apiPointer: '#/entries/0' }]
    }));
    await writeFile(join(plugin, 'plugin.schema.json'), JSON.stringify({
        format: 'society-config-schema/v1',
        schema: { dialect: 'schemastery', entries: [{ exportPath: '.', schema: { uid: 0, refs: { 0: { type: 'object', meta: { default: {} }, dict: {} } } } }] },
        limitations: []
    }));
    await writeFile(join(plugin, 'plugin.api.json'), JSON.stringify({
        format: 'society-plugin-api/v1', entries: [{ exportPath: '.', provides: [], requires: [] }]
    }));
    await mkdir(join(exp, 'node_modules', '@society0'), { recursive: true });
    await mkdir(join(exp, 'node_modules', '@fixture'), { recursive: true });
    for (const engine of ['host', 'core', 'extras', 'social']) {
        await symlink(resolve('.build', engine), join(exp, 'node_modules', '@society0', engine), 'dir');
    }
    await symlink(plugin, join(exp, 'node_modules', '@fixture', 'local-plugin'), 'dir');
    return { exp, plugin, lock: join(directory, 'd7-lock.json'), name, version };
}

export function hasSecret(root, secret) {
    const stack = [root];
    while (stack.length) {
        const path = stack.pop();
        let info;
        try { info = statSync(path); } catch { continue; }
        if (info.isDirectory()) { for (const name of readdirSync(path)) stack.push(join(path, name)); continue; }
        try { if (readFileSync(path, 'utf8').includes(secret)) return path; } catch { /* binary */ }
    }
    return undefined;
}
