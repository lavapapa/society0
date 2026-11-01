import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHost } from '../.build/host/dist/index.js';

test('RV3-17-T04 all shipped profiles compile under strict config', async () => {
    const baseURL = new URL('../.build/release.json', import.meta.url).href;
    const host = await createHost({
        baseURL,
        boot: ['resources', 'tasks', 'composition-strategies', 'slot-strategies', 'slots', 'module-resolver', 'config-resolvers', 'profile-loader', 'inspector', 'run-directory'].map(name => ({ specifier: '@society0/host/' + name }))
    });
    try {
        const directory = await mkdtemp(join(tmpdir(), 'g14-lock-'));
        const lockfile = join(directory, 'module-lock.json');
        const packages = {};
        for (const packageName of ['host', 'core', 'extras', 'social']) {
            const meta = JSON.parse(await readFile(fileURLToPath(new URL(`../.build/${packageName}/generated/plugin.meta.json`, import.meta.url)), 'utf8'));
            packages['@society0/' + packageName] = meta.source;
        }
        await writeFile(lockfile, JSON.stringify({ lockfile: pathToFileURL(lockfile).href, packages }));
        for (const name of ['event-sqlite.json', 'rule-sqlite.json', 'social-codex-sqlite.json', 'social-sqlite.json']) {
            const profile = JSON.parse(await readFile(fileURLToPath(new URL('../profiles/' + name, import.meta.url)), 'utf8'));
            profile.lockfile = pathToFileURL(lockfile).href;
            profile.patches.push({ insert: [{ id: 'g14-model-credentials', name: '@society0/core/model-credentials', config: { stores: [] } }] });
            const plan = await host.ctx.profileLoader.compile(profile, { baseURL });
            assert.ok(plan.flat.length > 0, name);
        }
    }
    finally {
        await host.close();
    }
});
