import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require = createRequire(new URL('../.build/release.json', import.meta.url));
const names = ['social-repository', 'social-init', 'topology-edges', 'social', 'propagation-neighbors', 'measurement-repository', 'measurements', 'measurement-every', 'round-robin', 'social-recommendations', 'social-ranking', 'social-semantic', 'social-measurement', 'social-perception'];
for (const name of names)
    test('PR10 public assembly ' + name, async () => {
        const mod = await import(pathToFileURL(require.resolve('@society0/social/' + name)));
        assert.equal(mod.default.Config, mod.Config);
        assert.equal(mod.api.exportPath, './' + name);
        assert.equal(mod.metadata.hostAbi, 'society0/host/1');
        assert.equal(mod.metadata.configVersion, 1);
        const inject = mod.default.inject ?? [];
        for (const dependency of mod.api.requires) {
            assert.equal(dependency.service.protocol, dependency.service.key);
            assert.equal(dependency.service.major, 1);
            assert.deepEqual(dependency.service.capabilities, []);
            if (!dependency.optional)
                assert.ok(inject.includes(dependency.service.key), dependency.service.key);
        }
        for (const service of mod.api.provides) {
            assert.equal(service.protocol, service.key);
            assert.equal(service.major, 1);
            assert.deepEqual(service.capabilities, []);
        }
    });
test('PR10 public data contract export resolves', () => assert.ok(require.resolve('@society0/social/contracts').endsWith('contracts.js')));
