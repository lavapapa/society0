// cases: D19 every runtime slot declaration/contribution/acquire is covered by static api.slots
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { assistant, call, fixture, flatten, json, localExperiment, skip, waitFor, worldFile } from './d7-support.mjs';

const auditPathFor = directory => join(directory, 'slot-audit.jsonl');
const enginePackages = ['core', 'extras', 'social'];
const hostServices = new Set(['slots', 'resources', 'tasks', 'diagnostics', 'moduleResolver', 'configResolvers', 'profileLoader', 'commands', 'runDirectory']);
const optionalService = item => item.optional === true;
const alternatives = {
    clock: ['@society0/core/clock-ticks', '@society0/core/clock-event'],
    proposal: ['@society0/core/proposal-sequence', '@society0/core/proposal-events', '@society0/social/round-robin'],
    runtime: ['@society0/core/runtime-staged', '@society0/core/runtime-events'],
};
const alternativeNames = new Set(Object.values(alternatives).flat());
// Entries outside this audit because they need study-specific configuration or host wiring.
const researchConfig = new Map([
    ['@society0/core/model-credentials', '宿主作用域，由宿主启动时装配；自己声明并贡献凭据槽位'],
    ['@society0/core/plain-init', '需要改用独立的 initialActors/world 种子，当前 profile 使用 actors.seed'],
    ['@society0/core/deferred-work', '需要研究侧把 deferredWorkMigration 加入 run schema；插件本身不使用槽位'],
    ['@society0/core/embedding-http', '需要外部 embedding 服务的地址、模型和凭据'],
    ['@society0/core/model-codex-subscription', '需要 Codex 订阅凭据和模型配置'],
    ['@society0/extras/vector-providers', '与 vector-exact/vector-index 配套，需要 runId 和研究向量配置'],
    ['@society0/extras/vector-exact', '需要研究指定向量空间、generation 与 embedding identity'],
    ['@society0/extras/vector-index', '依赖 vector-exact 的具体向量空间配置'],
    ['@society0/social/social-semantic', '需要指定 embedding provider 与 vector provider'],
]);
const variants = [
    { name: 'ticks-sequence-staged', clock: '@society0/core/clock-ticks', proposal: '@society0/core/proposal-sequence', runtime: '@society0/core/runtime-staged' },
    { name: 'event-events', clock: '@society0/core/clock-event', proposal: '@society0/core/proposal-events', runtime: '@society0/core/runtime-events' },
    { name: 'ticks-round-robin-staged', clock: '@society0/core/clock-ticks', proposal: '@society0/social/round-robin', runtime: '@society0/core/runtime-staged' },
];

function entryName(pkg, exportPath) {
    return '@society0/' + pkg + (exportPath === '.' ? '' : exportPath.slice(1));
}

async function loadCatalog() {
    const catalog = [];
    for (const pkg of enginePackages) {
        const base = join('.build', pkg, 'generated');
        const meta = JSON.parse(await readFile(join(base, 'plugin.meta.json'), 'utf8'));
        const api = JSON.parse(await readFile(join(base, 'plugin.api.json'), 'utf8'));
        const schema = JSON.parse(await readFile(join(base, 'plugin.schema.json'), 'utf8'));
        for (const [index, item] of api.entries.entries()) {
            catalog.push({
                pkg, name: entryName(pkg, item.exportPath), api: item,
                metadata: meta.entries[index].metadata,
                schema: schema.schema.entries[index].schema,
            });
        }
    }
    return catalog;
}

function schemaDefaults(schema) {
    const seen = new Set();
    const resolveRef = uid => {
        if (seen.has(uid)) return undefined;
        seen.add(uid);
        const node = schema.refs[String(uid)];
        if (!node) return undefined;
        if (node.type === 'object' && node.dict) {
            const value = node.meta && Object.hasOwn(node.meta, 'default') && node.meta.default && typeof node.meta.default === 'object'
                ? structuredClone(node.meta.default) : {};
            for (const [key, ref] of Object.entries(node.dict)) {
                const child = resolveRef(ref);
                if (child !== undefined) value[key] = child;
            }
            return value;
        }
        if (node.meta && Object.hasOwn(node.meta, 'default')) return structuredClone(node.meta.default);
        return undefined;
    };
    return resolveRef(schema.uid) ?? {};
}

function configValue(config, key) {
    let value = config;
    for (const part of key.split('.')) {
        if (!value || typeof value !== 'object') return undefined;
        value = value[part];
    }
    return value;
}

function expand(pattern, config) {
    return pattern.replace(/\{([^{}]+)\}/g, (_, key) => {
        const value = configValue(config, key.trim());
        return value === undefined || value === null ? `{${key}}` : String(value);
    });
}

function findGroup(entries, id) {
    const found = flatten(entries).find(entry => entry.group && entry.id === id);
    assert.ok(found, 'profile must contain a ' + id + ' group');
    return found;
}

function removeEntry(entries, name) {
    for (let index = entries.length - 1; index >= 0; index--) {
        const entry = entries[index];
        if (entry.name === name) entries.splice(index, 1);
        else if (entry.group) removeEntry(entry.config ?? [], name);
    }
}

function buildProfile(profile, catalog, variant, eventPlugin) {
    const root = profile.patches[0].insert;
    const run = findGroup(root, 'run');
    const generation = findGroup(root, 'generation');
    const bootstrap = findGroup(root, 'bootstrap');
    const byName = new Map(catalog.map(item => [item.name, item]));
    const present = () => new Set(flatten(root).map(entry => entry.name));
    const provided = () => {
        const keys = new Set(hostServices);
        for (const entry of flatten(root)) {
            const api = byName.get(entry.name)?.api;
            for (const item of api?.provides ?? []) keys.add(item.key);
        }
        return keys;
    };
    const groupFor = scope => scope === 'run' ? run : scope === 'bootstrap' ? bootstrap : generation;
    const add = (item, scope = item.metadata.scope) => {
        groupFor(scope).config.push({ id: item.name.replace(/^@society0\//, '').replaceAll('/', '-'), name: item.name, config: {} });
    };

    for (const name of alternativeNames) removeEntry(root, name);

    let changed = true;
    const rejected = new Map();
    while (changed) {
        changed = false;
        const names = present(), keys = provided();
        for (const item of catalog) {
            if (names.has(item.name) || researchConfig.has(item.name) || alternativeNames.has(item.name) || item.metadata.scope === 'host') continue;
            if (!['run', 'bootstrap', 'generation'].includes(item.metadata.scope)) continue;
            const provides = (item.api.provides ?? []).map(value => value.key);
            if (provides.some(key => keys.has(key))) { rejected.set(item.name, 'service conflict: ' + provides.filter(key => keys.has(key)).join(', ')); continue; }
            const missing = (item.api.requires ?? []).filter(value => !optionalService(value) && !keys.has(value.service.key));
            if (missing.length) { rejected.set(item.name, 'missing service: ' + missing.map(value => value.service.key).join(', ')); continue; }
            add(item);
            changed = true;
            for (const key of provides) keys.add(key);
            names.add(item.name);
        }
    }

    for (const name of ['@society0/core/plain', '@society0/core/rule-passive', '@society0/core/driver-rule', '@society0/extras/datasets', '@society0/extras/dataset-jsonl', '@society0/extras/memory-repository', '@society0/extras/memory']) {
        rejected.delete(name);
        if (present().has(name)) continue;
        const item = byName.get(name);
        assert.ok(item, 'missing plugin ' + name);
        add(item);
    }
    for (const name of [variant.clock, variant.proposal, variant.runtime]) {
        const item = byName.get(name);
        assert.ok(item, 'missing alternative plugin ' + name);
        if (!present().has(name)) add(item);
    }

    if (variant.proposal === '@society0/core/proposal-sequence') {
        const entry = flatten(root).find(value => value.name === variant.proposal);
        entry.config = {
            proposals: [{
                id: 'd19-p1', planKind: 'staged/1',
                payload: {
                    phases: [{ id: 'act', activations: [
                        { id: 'd19-llm', actorId: 'alice', momentId: 'm1', source: 'agenda', driver: 'llm/v1', input: null },
                        { id: 'd19-rule', actorId: 'bob', momentId: 'm1', source: 'agenda', driver: 'rule', input: null },
                    ] }],
                    nextCursor: '1',
                },
            }],
        };
    } else if (variant.proposal === '@society0/social/round-robin') {
        flatten(root).find(value => value.name === variant.proposal).config = { actors: ['alice'], rounds: 1, driver: 'llm/v1' };
    }

    const llm = flatten(root).find(entry => entry.name === '@society0/core/driver-llm');
    assert.ok(llm, 'profile must contain driver-llm');
    llm.config = {
        ...llm.config, provider: 'replay/v1', model: 'd19-replay', maxRequests: 4, maxToolCalls: 2,
        perceptions: ['actor/v1'], requiredActions: [{ name: 'social.publish', minimumCompleted: 1 }],
    };
    removeEntry(root, '@society0/core/model-replay');
    run.config.push({
        id: 'model-replay', name: '@society0/core/model-replay',
        config: { name: 'replay/v1', fixtures: { 'd19-replay': [
            assistant([{ type: 'toolCall', id: 'd19-publish', name: 'act', arguments: { action: 'social.publish', input: { content: 'd19 slot audit' } } }], 'toolUse'),
            assistant([{ type: 'text', text: 'd19 audit complete' }]),
        ] } },
    });
    if (eventPlugin) generation.config.push({ id: 'd19-events', name: eventPlugin, config: {} });
    return { rejected: [...rejected].map(([name, reason]) => name + ' — ' + reason) };
}

function slotChecker(records, profile, catalog, warnings) {
    const profileEntries = flatten(profile.patches[0].insert).filter(entry => !entry.group);
    const entries = new Map(profileEntries.map(entry => [entry.id, entry.name]));
    const profileEntry = name => profileEntries.find(entry => entry.name === name);
    const itemFor = name => catalog.find(item => item.name === name);
    const slotsFor = value => {
        const name = typeof value === 'string' ? value : value.name;
        const entry = typeof value === 'string' || value.config !== undefined ? value : profileEntry(value.name);
        const item = itemFor(name);
        if (!item?.api.slots) return { provides: [], requires: [] };
        const config = { ...schemaDefaults(item.schema), ...(entry?.config ?? {}) };
        return {
            provides: (item.api.slots.provides ?? []).map(pattern => expand(pattern, config)),
            requires: (item.api.slots.requires ?? []).map(pattern => expand(pattern, config)),
        };
    };
    const ownerRecord = record => {
        const id = record.owner?.entry;
        const name = id ? entries.get(id) : undefined;
        return name ? { id, name } : undefined;
    };
    const observed = records.map(record => ({ ...record, entry: ownerRecord(record) })).filter(record => record.entry);
    const declarations = new Map();
    for (const record of observed.filter(record => record.kind === 'declare'))
        (declarations.get(record.key) ?? declarations.set(record.key, []).get(record.key)).push(record);
    const contributes = new Set(observed.filter(record => record.kind === 'contribute').map(record => record.entry.name + '\0' + record.key));
    const acquisitions = observed.filter(record => record.kind === 'acquire');
    const failures = [];
    const fail = (name, missing, key) => failures.push(`${name} → 缺 ${missing} → ${key}`);

    for (const record of observed.filter(record => record.kind === 'contribute')) {
        const owners = declarations.get(record.key) ?? [];
        const requiredByOther = owners.some(owner => owner.required === true && owner.entry.name !== record.entry.name);
        if (requiredByOther && !slotsFor(record.entry).provides.includes(record.key))
            fail(record.entry.name, 'provides', record.key);
    }
    for (const record of observed.filter(record => record.kind === 'declare' && record.required === true)) {
        if (contributes.has(record.entry.name + '\0' + record.key)) continue;
        if (!slotsFor(record.entry).requires.includes(record.key))
            fail(record.entry.name, 'requires', record.key);
    }
    for (const record of acquisitions) {
        // Driver names come from proposal data, not from the acquiring entry's config.
        // D18's static check covers driver names written in profile config.
        if (record.key.startsWith('core/drivers/')) continue;
        const owners = declarations.get(record.key) ?? [];
        const requiredByOther = owners.some(owner => owner.required === true && owner.entry.name !== record.entry.name);
        if (requiredByOther && !contributes.has(record.entry.name + '\0' + record.key) && !slotsFor(record.entry).requires.includes(record.key))
            fail(record.entry.name, 'requires', record.key);
    }
    assert.deepEqual([...new Set(failures)].sort(), [], [...new Set(failures)].sort().join('\n'));

    const observedProvides = new Set(observed.filter(record => record.kind === 'contribute').map(record => record.entry.name + '\0' + record.key));
    const observedRequires = new Set([
        ...observed.filter(record => record.kind === 'declare').map(record => record.entry.name + '\0' + record.key),
        ...acquisitions.map(record => record.entry.name + '\0' + record.key),
    ]);
    for (const entry of profileEntries) {
        if (!itemFor(entry.name)?.api.slots) continue;
        const slots = slotsFor(entry);
        for (const key of slots.provides)
            if (!key.includes('{') && !observedProvides.has(entry.name + '\0' + key)) warnings.add(`${entry.name} 声明 provides ${key}，运行时未观察到 contribute`);
        for (const key of slots.requires)
            if (!key.includes('{') && !observedRequires.has(entry.name + '\0' + key)) warnings.add(`${entry.name} 声明 requires ${key}，运行时未观察到 declare/acquire`);
    }
    return { observed };
}

const eventSource = `export default {
    inject: ['proposalSource'],
    async apply(ctx) {
        await ctx.proposalSource.push({
            id: 'd19-event-1', offset: '1', time: '1',
            payload: { handler: 'plain.update', actorId: 'alice', attributes: { name: 'Alice', message: 'd19 slot audit event' } }
        });
        ctx.proposalSource.end();
    }
};
`;

async function runVariant(catalog, variant, t) {
    const eventPlugin = variant.proposal === '@society0/core/proposal-events' ? '@fixture/local-plugin' : undefined;
    let local;
    const built = await fixture('social-sqlite', profile => {
        const { rejected } = buildProfile(profile, catalog, variant, eventPlugin);
        t.diagnostic(`${variant.name}: 未装配 ${rejected.length} 个与替代组无关的条目` + (rejected.length ? '：' + rejected.join('；') : ''));
    });
    if (eventPlugin) {
        local = await localExperiment(built.directory, eventPlugin, '1.0.0', { scope: 'generation', source: eventSource });
        await writeFile(join(local.plugin, 'plugin.api.json'), JSON.stringify({
            format: 'society-plugin-api/v1',
            entries: [{ exportPath: '.', provides: [], requires: [] }]
        }));
        const document = JSON.parse(await readFile(built.profile, 'utf8'));
        document.lockfile = pathToFileURL(local.lock).href;
        await writeFile(built.profile, JSON.stringify(document));
        const locked = call(['lock', '--base', local.exp, '--out', local.lock, local.name, '@society0/host', '@society0/core', '@society0/extras', '@society0/social', '--json']);
        assert.equal(locked.status, 0, locked.stderr);
    }

    const world = await worldFile(built.directory);
    const audit = auditPathFor(built.directory);
    await writeFile(audit, '');
    const base = local ? ['--base', local.exp] : [];
    const checked = call(['check', '--profile', built.profile, '--world', world, ...base, '--credentials-env', 'D19_CREDENTIALS', '--json'], { env: { ...process.env, D19_CREDENTIALS: '{}' } });
    assert.equal(checked.status, 0, checked.stdout + checked.stderr);

    const runDir = join(built.directory, 'run-d19');
    const started = call(['run', 'start', '--profile', built.profile, '--dir', runDir, '--world', world, ...base, '--ticks', '1', '--detach', '--json'], { env: { ...process.env, SOCIETY0_SLOT_AUDIT: audit, D19_CREDENTIALS: '{}' } });
    assert.equal(started.status, 0, started.stderr);
    json(started);
    const status = await waitFor(runDir, value => ['completed', 'failed', 'paused'].includes(value.state));
    assert.equal(status.state, 'completed', JSON.stringify(status));
    const result = json(call(['run', 'result', '--dir', runDir, '--json']));
    assert.ok(result.activations.total >= 1, JSON.stringify(result.activations));
    assert.equal(result.activations.failed, 0, JSON.stringify(result.activations));

    const records = (await readFile(audit, 'utf8')).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
    if (variant.proposal !== '@society0/social/round-robin')
        assert.ok(records.some(record => record.kind === 'acquire' && record.key === 'society/rule-handler'), 'rule activation must acquire the rule handler slot');
    const profile = JSON.parse(await readFile(built.profile, 'utf8'));
    return { profile, records };
}

test('D19 runtime slot use is fully declared by api.slots', { skip, timeout: 900_000 }, async (t) => {
    const catalog = await loadCatalog();
    const warnings = new Set();
    const covered = new Set();
    for (const variant of variants) {
        const { profile, records } = await runVariant(catalog, variant, t);
        const names = flatten(profile.patches[0].insert).filter(entry => !entry.group).map(entry => entry.name);
        for (const name of names) covered.add(name);
        for (const name of [variant.clock, variant.proposal, variant.runtime]) assert.ok(names.includes(name), `${variant.name} must mount ${name}`);
        slotChecker(records, profile, catalog, warnings);
    }
    const uncovered = catalog.filter(item => !covered.has(item.name) && !researchConfig.has(item.name));
    assert.deepEqual(uncovered.map(item => item.name), [], 'unexpected un-audited plugins: ' + uncovered.map(item => item.name).join(', '));
    for (const [name, reason] of researchConfig) t.diagnostic(`未审计 ${name}：${reason}`);
    for (const warning of warnings) t.diagnostic('warning: ' + warning);
});

const missingSlotDriverSource = `export default {
    inject: ['drivers', 'slots'],
    apply(ctx) {
        const key = { id: 'society/d19-missing', version: 1 };
        ctx.slots.declare({ key, strategy: 'keyed', required: true, validate: value => value });
        ctx.drivers.register('x', {
            async activate() {
                try {
                    await ctx.slots.acquire(key, { generation: 'd19' });
                    return { status: 'completed', value: null, evidence: {}, requiredOperations: [] };
                } catch (error) {
                    return {
                        status: 'incomplete', value: { reason: 'setup_error' },
                        evidence: { error: String(error), code: 'SLOT_REQUIRED', stage: 'runtime.acquire' },
                        requiredOperations: []
                    };
                }
            }
        });
    }
};
`;

test('D19 result failures carry the required slot key', { skip, timeout: 240_000 }, async () => {
    const { directory } = await fixture('rule-sqlite');
    const local = await localExperiment(directory, '@fixture/local-plugin', '1.0.0', { scope: 'generation', source: missingSlotDriverSource });
    await writeFile(join(local.plugin, 'plugin.api.json'), JSON.stringify({
        format: 'society-plugin-api/v1',
        entries: [{ exportPath: '.', provides: [], requires: [], slots: { provides: ['core/drivers/x'] } }]
    }));
    const built = await fixture('rule-sqlite', profile => {
        const generation = flatten(profile.patches[0].insert).find(entry => entry.group && entry.id === 'generation');
        assert.ok(generation, 'profile must contain a generation group');
        generation.config.push({ id: 'd19-driver', name: local.name, config: {} });
        const sequence = flatten(profile.patches[0].insert).find(entry => entry.name === '@society0/core/proposal-sequence');
        for (const proposal of sequence.config.proposals)
            for (const phase of proposal.payload?.phases ?? [])
                for (const activation of phase.activations ?? [])
                    activation.driver = 'x';
    });
    const profile = JSON.parse(await readFile(built.profile, 'utf8'));
    profile.lockfile = pathToFileURL(local.lock).href;
    await writeFile(built.profile, JSON.stringify(profile));
    const locked = call(['lock', '--base', local.exp, '--out', local.lock, local.name, '@society0/host', '@society0/core', '@society0/extras', '@society0/social', '--json']);
    assert.equal(locked.status, 0, locked.stderr);

    const runDir = join(built.directory, 'run-d19-required');
    const started = call(['run', 'start', '--profile', built.profile, '--dir', runDir, '--base', local.exp, '--ticks', '1', '--detach', '--json']);
    assert.equal(started.status, 0, started.stderr);
    json(started);
    const status = await waitFor(runDir, value => ['completed', 'failed', 'paused'].includes(value.state));
    assert.ok(['completed', 'failed', 'paused'].includes(status.state), JSON.stringify(status));
    const result = json(call(['run', 'result', '--dir', runDir, '--json']));
    assert.equal(result.activations.failures[0].code, 'SLOT_REQUIRED');
    assert.equal(result.activations.failures[0].slot, 'society/d19-missing');
    assert.equal(result.activations.failures[0].stage, 'runtime.acquire');
});
