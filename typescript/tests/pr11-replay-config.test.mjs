import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import Replay, { Config, metadata } from '../.build/core/dist/replay/index.js';
import { compileLayers } from '../.build/host/dist/profile-loader/compiler.js';

const externalServices = ['history', 'attempts', 'artifacts'];
const layer = { kind: 'profile', uri: 'replay-config.json', version: '1', patches: [{ insert: [{ id: 'run', name: 'run', group: true, lifetime: 'run', config: [{ id: 'replay', name: 'replay', config: { mode: 'replay' } }] }] }] };

test('RV3-17-T01 replay exposes no unconsumed mode field', async () => {
    assert.equal('mode' in (Config.dict ?? {}), false);
    assert.equal(Config.dict?.onMissingExternalResult?.type, 'const');
    assert.equal(Config.dict?.onMissingExternalResult?.value, 'fail');
    assert.deepEqual(Config({}), { onMissingExternalResult: 'fail' });
    assert.throws(() => Config({ onMissingExternalResult: 'bad' }), /expected fail/);
    assert.deepEqual(metadata.configPolicy.map(item => item.path), ['/onMissingExternalResult']);
    const generated = JSON.parse(await readFile(fileURLToPath(new URL('../.build/core/generated/plugin.meta.json', import.meta.url)), 'utf8'));
    const replay = generated.entries.find(entry => entry.exportPath === './replay');
    assert.deepEqual(replay.metadata.configPolicy.map(item => item.path), ['/onMissingExternalResult']);
    await assert.rejects(compileLayers([layer], { replay: { plugin: Replay, metadata, api: (await import('../.build/core/dist/replay/index.js')).api } }, { externalServices }), /UNKNOWN_CONFIG_FIELD replay\/mode/);
});
