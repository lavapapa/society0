import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compileLayers } from '../.build/host/dist/profile-loader/compiler.js';
import * as ActionFiles from '../.build/core/dist/action-files/index.js';

const metadata = {
    title: 'fake-consumer',
    summary: 'D24 假消费方',
    configVersion: 1,
    configPolicy: [],
    scope: 'generation',
    hostAbi: 'society0/host/1',
};

const descriptor = { key: 'actionFiles', protocol: 'actionFiles', major: 1, capabilities: [] };

function consumer(requiresActionFiles) {
    return {
        plugin: { apply() {} },
        metadata,
        api: {
            exportPath: '.',
            provides: [],
            requires: requiresActionFiles
                ? [{ service: descriptor, optional: false, phase: 'module' }]
                : [],
        },
    };
}

const layer = entries => [{
    kind: 'profile',
    uri: 'd24-action-files.json',
    version: '1',
    patches: [{ insert: [{
        id: 'run',
        name: 'run',
        group: true,
        lifetime: 'run',
        config: [{ id: 'generation', name: 'generation', group: true, lifetime: 'generation', config: entries }],
    }] }],
}];
const externalServices = ['actions', 'publicSelf'];

test('action-files declares the actionFiles marker service', () => {
    assert.deepEqual(ActionFiles.api.provides, [descriptor]);
    assert.deepEqual(ActionFiles.ACTION_FILES_SERVICE, {
        offersPath: ActionFiles.OFFERS_PATH,
        catalogPath: ActionFiles.CATALOG_PATH,
    });
    assert.equal(ActionFiles.OFFERS_PATH, '/self/actions/offers.jsonl');
    assert.equal(ActionFiles.CATALOG_PATH, '/self/actions/catalog.jsonl');
});

test('requires actionFiles pulls action-files into the composed plan', async () => {
    const plan = await compileLayers(layer([
        { id: 'action-files', name: '@society0/core/action-files' },
        { id: 'consumer', name: 'consumer' },
    ]), {
        '@society0/core/action-files': {
            plugin: ActionFiles.default,
            metadata: ActionFiles.metadata,
            api: ActionFiles.api,
        },
        consumer: consumer(true),
    }, { externalServices });

    const provider = plan.flat.find(entry => entry.name === '@society0/core/action-files');
    const dependent = plan.flat.find(entry => entry.name === 'consumer');
    assert.ok(provider);
    assert.ok(dependent);
    assert.ok(dependent.inject.includes('actionFiles'));
});

test('without requires actionFiles the plugin is neither required nor added', async () => {
    const plan = await compileLayers(layer([{ id: 'consumer', name: 'consumer' }]), {
        consumer: consumer(false),
    }, { externalServices });

    const dependent = plan.flat.find(entry => entry.name === 'consumer');
    assert.ok(dependent);
    assert.equal(plan.flat.some(entry => entry.name === '@society0/core/action-files'), false);
    assert.equal(dependent.inject.includes('actionFiles'), false);
});

test('requires actionFiles fails composition when action-files is absent', async () => {
    await assert.rejects(
        compileLayers(layer([{ id: 'consumer', name: 'consumer' }]), {
            consumer: consumer(true),
        }, { externalServices }),
        /SERVICE_MISSING consumer requires actionFiles/,
    );
});
