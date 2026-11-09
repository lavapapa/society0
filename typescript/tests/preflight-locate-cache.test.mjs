// cases: D9 package graph locate cache
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

test('one package graph checks each (directory, package name) once', { timeout: 60_000 }, async () => {
    // 放在 .build 下，使 @society0/host / @society0/cordis 能从祖先 node_modules 解析。
    const directory = mkdtempSync(join(resolve('.build'), 'preflight-cache-'));
    const modules = join(directory, 'node_modules');
    for (const [name, manifest] of [
        ['leaf', { name: '@fixture/leaf', version: '1.0.0' }],
        ['root', { name: '@fixture/root', version: '1.0.0', exports: { './a': './a.js', './b': './b.js' }, peerDependencies: { '@society0/host': '^0.1.0' }, dependencies: { '@fixture/leaf': '1.0.0' } }],
    ]) {
        const folder = join(modules, '@fixture', name);
        mkdirSync(folder, { recursive: true });
        writeFileSync(join(folder, 'package.json'), JSON.stringify(manifest));
        writeFileSync(join(folder, 'a.js'), 'export default {}');
        writeFileSync(join(folder, 'b.js'), 'export default {}');
    }
    writeFileSync(join(directory, 'package.json'), JSON.stringify({ name: 'preflight-cache-root', version: '1.0.0' }));
    const rootManifest = join(modules, '@fixture', 'root', 'package.json');
    let lookups = 0;
    let reads = 0;
    const originalExists = fs.existsSync;
    const originalRead = fs.readFileSync;
    fs.existsSync = function (path, ...args) {
        if (String(path) === rootManifest) lookups++;
        return originalExists.call(this, path, ...args);
    };
    fs.readFileSync = function (path, ...args) {
        if (String(path) === rootManifest) reads++;
        return originalRead.call(this, path, ...args);
    };
    syncBuiltinESMExports();
    try {
        const { createPreflightSession } = await import('../.build/host/dist/packages.js');
        const session = createPreflightSession();
        const baseURL = pathToFileURL(join(directory, 'package.json')).href;
        assert.equal(session.preflight(['@fixture/root/a', '@fixture/root/b'], baseURL).length, 2);
        assert.equal(lookups, 1, 'same (directory, package name) is looked up once in one graph');
        assert.equal(reads, 1, 'same (directory, package name) reads its manifest once in one graph');
    } finally {
        fs.existsSync = originalExists;
        fs.readFileSync = originalRead;
        syncBuiltinESMExports();
        rmSync(directory, { recursive: true, force: true });
    }
});
