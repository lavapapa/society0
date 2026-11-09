// cases: D16 plugin-provided migrations are collected from copied and symlinked experiment packages
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cp, mkdir, readFile, symlink, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { call, fixture, flatten, json, skip, waitFor } from './d7-support.mjs';

const enginePackages = ['@society0/host', '@society0/core', '@society0/extras', '@society0/social'];

function git(directory, args) {
    const result = spawnSync('git', ['-C', directory, ...args], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    return result.stdout.trim();
}

function packageJson(name) {
    return JSON.stringify({
        name,
        version: '1.0.0',
        type: 'module',
        exports: {
            '.': './index.js',
            './package.json': './package.json',
            './plugin.meta.json': './plugin.meta.json',
            './plugin.schema.json': './plugin.schema.json',
            './plugin.api.json': './plugin.api.json'
        },
        society: { plugin: { metadata: './plugin.meta.json', configSchema: './plugin.schema.json', api: './plugin.api.json' } },
        peerDependencies: { '@society0/host': '0.1.0' }
    });
}

async function writePackage(directory, name, owner, table, handler) {
    await mkdir(directory, { recursive: true });
    await writeFile(join(directory, 'package.json'), packageJson(name));
    const migrations = `
export const migrations = [{
    owner: ${JSON.stringify(owner)},
    from: 0,
    to: 1,
    after: [],
    source: { package: ${JSON.stringify(name)}, entry: '.', version: 1 },
    statements: [{ sql: 'CREATE TABLE ${table} (id TEXT PRIMARY KEY NOT NULL)' }]
}];
`;
    const plugin = handler ? `
import { ruleHandlerSlot } from '@society0/core/driver-rule';

export default {
    inject: ['slots', 'database'],
    apply(ctx) {
        ctx.slots.contribute(ruleHandlerSlot, {
            id: ${JSON.stringify(owner + '/v1')},
            value: {
                async activate(_input, context) {
                    await context.database.transaction(context.writeLease, {
                        operationId: context.operationId + ':d16-write',
                        maxDurationMs: 15000,
                        maxStatements: 8,
                        maxInputBytes: 1048576,
                        maxChangedRows: 8,
                        maxCapturedBytes: 1048576
                    }, async tx => {
                        await tx.execute({ sql: 'INSERT INTO ${table}(id) VALUES (?)', parameters: [${JSON.stringify(owner)} + ':' + context.operationId] });
                    });
                    return { status: 'completed', value: { tag: ${JSON.stringify(owner)} }, evidence: { policy: ${JSON.stringify(owner + '/v1')} }, requiredOperations: [] };
                }
            }
        });
    }
};
` : `
export default { apply() {} };
`;
    await writeFile(join(directory, 'index.js'), migrations + plugin);
    git(directory, ['init', '-q']);
    git(directory, ['-c', 'user.email=d16@example.invalid', '-c', 'user.name=d16', 'add', 'index.js', 'package.json']);
    git(directory, ['-c', 'user.email=d16@example.invalid', '-c', 'user.name=d16', 'commit', '-q', '-m', 'd16 plugin']);
    const commit = git(directory, ['rev-parse', 'HEAD']);
    const source = { package: name, version: '1.0.0', gitCommit: commit, dirty: true, lockfile: 'BUILD-LOCK.json', patches: [], hostAbi: 'society0/host/1' };
    const metadata = {
        title: owner, summary: 'D16 migration fixture', configVersion: 1, configPolicy: [], scope: 'run', hostAbi: 'society0/host/1'
    };
    await writeFile(join(directory, 'plugin.meta.json'), JSON.stringify({
        format: 'society-plugin-metadata/v1', package: { name, version: '1.0.0' }, source,
        entries: [{ exportPath: '.', metadata, configSchemaPointer: '#/schema/entries/0/schema', apiPointer: '#/entries/0' }]
    }));
    await writeFile(join(directory, 'plugin.schema.json'), JSON.stringify({
        format: 'society-config-schema/v1',
        schema: { dialect: 'schemastery', entries: [{ exportPath: '.', schema: { uid: 0, refs: { 0: { type: 'object', meta: { default: {} }, dict: {} } } } }] },
        limitations: []
    }));
    await writeFile(join(directory, 'plugin.api.json'), JSON.stringify({
        format: 'society-plugin-api/v1', entries: [{ exportPath: '.', provides: [], requires: [] }]
    }));
}

async function experiment(directory, specs) {
    const exp = join(directory, 'experiment');
    await mkdir(join(exp, 'node_modules', '@society0'), { recursive: true });
    await mkdir(join(exp, 'node_modules', '@fixture'), { recursive: true });
    for (const engine of ['host', 'core', 'extras', 'social'])
        await symlink(resolve('.build', engine), join(exp, 'node_modules', '@society0', engine), 'dir');
    const installed = [];
    for (const spec of specs) {
        const source = join(exp, 'packages', spec.directory);
        await writePackage(source, spec.name, spec.owner, spec.table, spec.handler);
        const target = join(exp, 'node_modules', '@fixture', spec.directory);
        if (spec.install === 'copy')
            await cp(source, target, { recursive: true });
        else
            await symlink(source, target, 'dir');
        installed.push(spec.name);
    }
    return { exp, lock: join(directory, 'd16-lock.json'), installed };
}

function addPlugins(profile, specs) {
    const entries = flatten(profile.patches[0].insert);
    const run = entries.find(entry => entry.id === 'run');
    assert.ok(run, 'rule-sqlite profile must contain the run group');
    for (const [index, spec] of specs.entries())
        run.config.push({ id: 'd16-plugin-' + index, name: spec.name, config: {} });
    const handler = specs.find(spec => spec.handler);
    if (handler) {
        const driver = entries.find(entry => entry.name === '@society0/core/driver-rule');
        assert.ok(driver, 'rule-sqlite profile must contain driver-rule');
        driver.config = { ...(driver.config ?? {}), handler: handler.owner + '/v1' };
    }
}

async function prepare(specs) {
    const { directory } = await fixture('rule-sqlite');
    const { exp, lock, installed } = await experiment(directory, specs);
    const built = await fixture('rule-sqlite', profile => addPlugins(profile, specs));
    const profile = JSON.parse(await readFile(built.profile, 'utf8'));
    profile.lockfile = pathToFileURL(lock).href;
    await writeFile(built.profile, JSON.stringify(profile));
    const locked = call(['lock', '--base', exp, '--out', lock, ...installed, ...enginePackages, '--json']);
    assert.equal(locked.status, 0, locked.stderr);
    return { directory, exp, lock, profile: built.profile, installed };
}

test('D16 check and run collect plugin migrations from copied and symlinked packages', { skip, timeout: 300_000 }, async () => {
    const { directory, exp, profile, installed } = await prepare([
        { directory: 'd16-copy', name: '@fixture/d16-copy', owner: 'd16-copy', table: 'd16_copy_rows', install: 'copy', handler: true },
        { directory: 'd16-link', name: '@fixture/d16-link', owner: 'd16-link', table: 'd16_link_rows', install: 'link' }
    ]);

    const checked = call(['check', '--profile', profile, '--base', exp, '--json']);
    assert.equal(checked.status, 0, checked.stderr);
    const report = JSON.parse(checked.stdout);
    assert.equal(report.ok, true, JSON.stringify(report.missing));
    assert.ok(report.resolved.plugins.includes(installed[0]), JSON.stringify(report.resolved.plugins));
    assert.ok(report.resolved.plugins.includes(installed[1]), JSON.stringify(report.resolved.plugins));
    assert.ok(report.migrations.includes('d16-copy'), JSON.stringify(report.migrations));
    assert.ok(report.migrations.includes('d16-link'), JSON.stringify(report.migrations));

    const runDir = join(directory, 'run-d16');
    json(call(['run', 'start', '--profile', profile, '--dir', runDir, '--base', exp, '--ticks', '1', '--detach', '--json']));
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));
    assert.equal(status.completedTicks, 1);
});

test('D16 check reports conflicting plugin migrations for one owner', { skip, timeout: 300_000 }, async () => {
    const { exp, profile } = await prepare([
        { directory: 'd16-first', name: '@fixture/d16-first', owner: 'd16-conflict', table: 'd16_first_rows', install: 'copy' },
        { directory: 'd16-second', name: '@fixture/d16-second', owner: 'd16-conflict', table: 'd16_second_rows', install: 'link' }
    ]);

    const checked = call(['check', '--profile', profile, '--base', exp, '--json']);
    assert.equal(checked.status, 2, checked.stderr);
    const report = JSON.parse(checked.stdout);
    assert.equal(report.ok, false);
    const problem = report.missing.find(item => item.kind === 'migration' && item.id === 'd16-conflict');
    assert.ok(problem, JSON.stringify(report.missing));
    assert.match(problem.hint, /@fixture\/d16-first/);
    assert.match(problem.hint, /@fixture\/d16-second/);
});
