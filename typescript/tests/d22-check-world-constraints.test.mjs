// cases: D22 check --world translates FOREIGN KEY, CHECK and NOT NULL root-creation failures
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { call, fixture, flatten, localExperiment, skip, worldFile } from './d7-support.mjs';

const enginePackages = ['@society0/host', '@society0/core', '@society0/extras', '@society0/social'];

// The base social profile only writes rows that satisfy its constraints, so a bootstrap
// initializer reads the world's d22.constraint field and performs the violating write.
const initializerSource = `export default {
    inject: ['initializers', 'database'],
    apply(ctx) {
        ctx.initializers.register({
            id: 'd22.constraint',
            after: ['social.seed'],
            async initialize(context) {
                const constraint = context.input?.d22?.constraint;
                if (!constraint) return { operationIds: [] };
                const operationId = 'd22/' + constraint;
                await context.database.transaction(context.writeLease, {
                    operationId, maxDurationMs: 15000, maxStatements: 16,
                    maxInputBytes: 1048576, maxChangedRows: 16, maxCapturedBytes: 1048576
                }, async tx => {
                    if (constraint === 'foreign-key')
                        await tx.execute({ sql: 'INSERT INTO social_members(id,ordinal,preferences) VALUES(?,?,?)', parameters: ['d22-ghost', 9999, '{}'] });
                    else if (constraint === 'check')
                        await tx.execute({ sql: "INSERT INTO social_posts(ordinal,id,author_id,content,tags,created_time,kind,parent_id,text_version,likes,replies,reposts,views,metadata) VALUES(0,'d22-post','alice','x','[]','0','invalid',NULL,1,0,0,0,0,'{}')" });
                    else if (constraint === 'not-null')
                        await tx.execute({ sql: "INSERT INTO social_events(ordinal,id,actor,kind,target,created_time,data) VALUES(0,'d22-event','alice','x','y','0',NULL)" });
                    else
                        throw Error('D22_CONSTRAINT_UNKNOWN');
                });
                return { operationIds: [operationId] };
            }
        });
    }
};
`;

function addInitializer(profile, pluginName) {
    const bootstrap = flatten(profile.patches[0].insert).find(entry => entry.id === 'bootstrap');
    assert.ok(bootstrap, 'profile must include a bootstrap group');
    bootstrap.config.push({ id: 'd22-constraint', name: pluginName, config: {} });
}

async function checkConstraint(constraint, expectedId) {
    const { directory } = await fixture('social-sqlite');
    const local = await localExperiment(directory, '@fixture/local-plugin', '1.0.0', { scope: 'bootstrap', source: initializerSource });
    const built = await fixture('social-sqlite', profile => addInitializer(profile, local.name));
    const profile = JSON.parse(await readFile(built.profile, 'utf8'));
    profile.lockfile = pathToFileURL(local.lock).href;
    await writeFile(built.profile, JSON.stringify(profile));
    const locked = call(['lock', '--base', local.exp, '--out', local.lock, local.name, ...enginePackages, '--json']);
    assert.equal(locked.status, 0, locked.stderr);

    const world = await worldFile(built.directory);
    const document = JSON.parse(await readFile(world, 'utf8'));
    document.d22 = { constraint };
    await writeFile(world, JSON.stringify(document));

    process.env.D22_CHECK_CREDENTIALS = '{}';
    let result;
    try {
        result = call(['check', '--profile', built.profile, '--base', local.exp, '--world', world, '--credentials-env', 'D22_CHECK_CREDENTIALS', '--json']);
    } finally { delete process.env.D22_CHECK_CREDENTIALS; }
    assert.equal(result.status, 2, result.stderr);
    const report = JSON.parse(result.stdout);
    assert.equal(report.ok, false);
    const problem = report.missing.find(item => item.kind === 'world' && item.id === expectedId);
    assert.ok(problem, JSON.stringify(report.missing));
    return problem;
}

test('D22 check --world translates a FOREIGN KEY root-creation failure', { skip, timeout: 300_000 }, async () => {
    const problem = await checkConstraint('foreign-key', 'foreign-key');
    assert.match(problem.hint, /外键/);
    assert.match(problem.hint, /world 的 actors 段/);
    assert.match(problem.detail, /FOREIGN KEY constraint failed/);
});

test('D22 check --world translates a CHECK root-creation failure with the field', { skip, timeout: 300_000 }, async () => {
    const problem = await checkConstraint('check', 'root-creation.kind');
    assert.match(problem.hint, /字段 kind/);
    assert.match(problem.hint, /CHECK/);
    assert.match(problem.detail, /CHECK constraint failed: kind IN/);
});

test('D22 check --world translates a NOT NULL root-creation failure with the field', { skip, timeout: 300_000 }, async () => {
    const problem = await checkConstraint('not-null', 'social_events.data');
    assert.match(problem.hint, /social_events/);
    assert.match(problem.hint, /data/);
    assert.match(problem.hint, /NOT NULL/);
    assert.match(problem.detail, /NOT NULL constraint failed: social_events\.data/);
});
