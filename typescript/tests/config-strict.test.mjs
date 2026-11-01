import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import Schema from '../.build/schemastery/dist/index.js';
import { compileLayers } from '../.build/host/dist/profile-loader/compiler.js';
import { createHost } from '../.build/host/dist/index.js';

const topology = await import('../.build/social/dist/topology-edges/index.js');
const prompts = await import('../.build/core/dist/prompts/index.js');
const modelPi = await import('../.build/core/dist/model-pi/index.js');
const social = await import('../.build/social/dist/social/index.js');

const externalServices = ['slots', 'actions', 'database', 'exploration', 'resources', 'information', 'perceptions', 'socialRepository', 'recommendations', 'actors', 'actorEligibility', 'artifacts', 'attempts', 'tasks', 'history', 'replay', 'modelProviders', 'modelCredentials', 'vectorProviders', 'drivers', 'threads', 'results', 'workspace', 'initializers', 'sqliteBootstrap', 'executionFence'];
const layer = insert => ({ kind: 'profile', uri: 'strict-config.json', version: '1', patches: [{ insert }] });
const run = config => ({ id: 'run', name: 'run', group: true, lifetime: 'run', config });
const generation = config => ({ id: 'generation', name: 'generation', group: true, lifetime: 'generation', config });
const bootstrap = config => ({ id: 'bootstrap', name: 'bootstrap', group: true, lifetime: 'bootstrap', config });
const author = {
    Config: Schema.object({ known: Schema.number().default(1) }),
    apply() { }
};
const authorBinding = {
    plugin: author,
    metadata: { title: 'author', summary: 'synthetic', configVersion: 1, configPolicy: [], scope: 'host', hostAbi: 'society0/host/1' },
    api: { exportPath: '.', provides: [], requires: [] }
};
const binding = module => ({ plugin: module.default, metadata: module.metadata, api: module.api });
async function compile(insert, bindings = {}) {
    return compileLayers([layer(insert)], bindings, { externalServices });
}

test('RV3-17-T02 plugin config rejects unknown keys at any depth', async () => {
    await assert.rejects(compile([{ id: 'author', name: 'author', config: { unknownKey: 1 } }], { author: authorBinding }), /UNKNOWN_CONFIG_FIELD author\/unknownKey/);
    await assert.rejects(compile([run([bootstrap([{ id: 'topology', name: 'topology', config: { actors: [{ id: 'a', x: 1 }], edges: [] } }])])], { topology: binding(topology) }), /UNKNOWN_CONFIG_FIELD topology\/actors\/0\/x/);
    await assert.rejects(compile([run([generation([{ id: 'prompts', name: 'prompts', config: { sections: [{ id: 's', text: 't', bogus: 1 }] } }])])], { prompts: binding(prompts) }), /UNKNOWN_CONFIG_FIELD prompts\/sections\/0\/bogus/);
    await compile([run([{ id: 'model', name: 'model', config: { providerId: 'fixture', credential: 'fixture', models: [{ id: 'm', arbitrary: 1 }] } }])], { model: binding(modelPi) });
    await compile([run([generation([{ id: 'social', name: 'social', config: { contentLengthLimit: -1 } }])])], { social: binding(social) });
    await compile([run([generation([{ id: 'social', name: 'social', config: { contentLengthLimit: 10 } }])])], { social: binding(social) });
    const baseURL = new URL('../.build/release.json', import.meta.url).href;
    await assert.rejects(createHost({ baseURL, boot: [{ specifier: '@society0/social/topology-edges', config: { unknownKey: 1 } }] }), /UNKNOWN_CONFIG_FIELD @society0\/social\/topology-edges\/unknownKey/);
    await assert.rejects(createHost({ baseURL, boot: [{ specifier: '@society0/host/source' }] }), /PLUGIN_ENTRY_INVALID: @society0\/host\/source/);
});
