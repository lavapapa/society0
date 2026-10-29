import { sqlConsumer } from './sql-consumer.js';
import { hostMetadata, hostApi } from '../module-resolver/metadata.js';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import Schema from '@society0/schemastery';
import { FiberState, type Context } from '@society0/cordis';
import type { ProgramSource } from '../config-resolvers/types.js';
import type { ModuleLock } from '../module-resolver/index.js';
import '../commands/index.js';
export const Config = Schema.object({});
export const metadata = hostMetadata('scaffold', 'Generate and validate independently packaged author plugins');
export const api = hostApi('./scaffold', [], ["commands", "moduleResolver", "profileLoader", "inspector"]);
export interface ScaffoldOptions {
    readonly directory: string;
    readonly name: string;
    readonly template: 'service-slot' | 'sqlite-domain' | 'config-bundle';
    readonly source: ProgramSource;
}
/** All generated sidecars are emitted from this package's compiled author module. */
function pluginMetadata(name: string, template: ScaffoldOptions['template']) { const metadata = { title: name, summary: 'An independently packaged Society0 plugin', configVersion: 1, configPolicy: [{ path: '/label', mode: 'recreate', affectsResults: false }], scope: template === 'sqlite-domain' ? 'generation' : 'host', allowedScopes: template === 'sqlite-domain' ? ['generation'] : ['host', 'generation'], hostAbi: 'society0/host/1' }; return metadata; }
function pluginApi(name: string, template: ScaffoldOptions['template']) { const api = { exportPath: '.', provides: template === 'config-bundle' ? [] : [{ key: template === 'sqlite-domain' ? 'exampleDomain' : 'authorEntries', protocol: name, major: 1, capabilities: [] }], requires: template === 'config-bundle' ? [] : [{ service: { key: template === 'sqlite-domain' ? 'database' : 'slots', protocol: template === 'sqlite-domain' ? 'database' : 'slots', major: 1, capabilities: [] }, optional: false, phase: 'module' }], slots: template === 'service-slot' ? [{ key: { id: name + '/filter', version: 1 }, role: 'declare' }] : [], sqliteSchemas: template === 'sqlite-domain' ? [{ owner: name, version: 1 }] : [], commands: [] }; return api; }
function pluginManifest(name: string, version: string, template: ScaffoldOptions['template']) { const manifest = { name, version, type: 'module', license: 'MIT', engines: { node: '>=24.16.0' }, files: ['dist', 'generated', 'LICENSE', 'README.md'], exports: { '.': { types: './dist/index.d.ts', default: './dist/index.js' }, './key': { types: './dist/slot-key.d.ts', default: './dist/slot-key.js' }, './metadata': './generated/plugin.meta.json', './config-schema': './generated/plugin.schema.json', './api': './generated/plugin.api.json', './bundle': './generated/bundle.patch.json', './package.json': './package.json' }, peerDependencies: { '@society0/host': '0.1.0', ...(template === 'sqlite-domain' ? { '@society0/core': '0.1.0' } : {}) }, dependencies: { '@society0/schemastery': '3.18.5-society0.1' }, devDependencies: { typescript: '6.0.3', '@types/node': '24.10.1' }, scripts: { build: 'tsc -p tsconfig.json && node scripts/metadata.mjs', test: 'node --test test/consumer.mjs' }, society: { plugin: { metadata: './metadata', configSchema: './config-schema', api: './api' }, ...(template === 'config-bundle' ? { bundle: { patch: './bundle' } } : {}) } }; return manifest; }
function metadataEmitter(name: string, template: ScaffoldOptions['template']) { const emitter = `import {mkdir,writeFile} from 'node:fs/promises';\nimport {Config,metadata,api,source} from '../dist/index.js';\nawait mkdir('generated',{recursive:true});\nconst save=(file,value)=>writeFile('generated/'+file,JSON.stringify(value,null,2)+'\\n');\nawait save('plugin.meta.json',{format:'society-plugin-metadata/v1',package:{name:${JSON.stringify(name)},version:'0.1.0'},source,entries:${template === 'config-bundle' ? '[]' : "[{exportPath:'.',metadata,configSchemaPointer:'#/schema',apiPointer:'#/entries/0'}]"}});\nawait save('plugin.schema.json',{format:'society-config-schema/v1',schema:Config.toJSON(),limitations:[]});\nawait save('plugin.api.json',{format:'society-plugin-api/v1',entries:${template === 'config-bundle' ? '[]' : '[api]'}});\nawait save('bundle.patch.json',[]);\n`; return emitter; }
function consumerTest(template: ScaffoldOptions['template']) { const test = template === 'service-slot' ? `import {test} from 'node:test';import assert from 'node:assert/strict';import {createTestHost} from '@society0/host/testkit';import * as author from '../dist/index.js';import {filterKey} from '../dist/slot-key.js';\ntest('native service and external contribution are owned and drained',async()=>{const host=await createTestHost();const owner=await host.ctx.plugin(author);const contributor=await host.ctx.plugin({inject:['slots','authorEntries'],apply(ctx){ctx.authorEntries.register('external');ctx.slots.contribute(filterKey,{id:'upper',value:value=>value.toUpperCase()})}});const lease=await host.ctx.slots.acquire(filterKey,{generation:'test'});assert.equal(lease.items[0].value('hello'),'HELLO');await lease.release();await contributor.dispose();assert.deepEqual(host.ctx.authorEntries.list(),[]);await owner.dispose();host.assertQuiescent();await host.close()});\n` : template === 'sqlite-domain' ? sqlConsumer : `import {test} from 'node:test';import assert from 'node:assert/strict';import {patches} from '../dist/index.js';test('bundle contains literal patches',()=>assert.deepEqual(patches,[]));\n`; return test; }
export function pluginFiles(options: ScaffoldOptions): Readonly<Record<string, string>> {
    const { name, template } = options;
    if (!/^(?:@[a-z0-9-]+\/)?[a-z0-9-]+$/.test(name))
        throw Error(`PACKAGE_NAME ${name}`);
    const version = '0.1.0';
    const source = { ...options.source, package: name, version, patches: [...options.source.patches, `scaffold:${template}`] };
    const metadata = pluginMetadata(name, template);
    const api = pluginApi(name, template);
    const shared = `import Schema from '@society0/schemastery';\nimport {Service,type Context} from '@society0/host';\nexport const Config=Schema.object({label:Schema.string().default('author')});\nexport const metadata=${JSON.stringify(metadata)};\nexport const api=${JSON.stringify(api)};\nexport const source=${JSON.stringify(source)};\n`;
    const service = `import {filterKey} from './slot-key.js';\ndeclare module '@society0/host' {interface Context {authorEntries:AuthorEntries}}\nexport class AuthorEntries extends Service {private values=new Set<string>();constructor(ctx:Context){super(ctx,'authorEntries')}register(value:string){return this.ctx.effect(()=>{this.values.add(value);return()=>{this.values.delete(value)}})}list(){return [...this.values]}}\nexport const inject=['slots'];\nexport async function apply(ctx:Context){await ctx.plugin(AuthorEntries);ctx.slots.declare({key:filterKey,strategy:'ordered',required:true,validate(value){if(typeof value!=='function')throw Error('filter requires function');return value as (text:string)=>string}})}\n`;
    const sql = `import type {SqliteTransaction,SqliteMigration} from '@society0/core/sqlite/contracts';\nexport const migrations=[{owner:${JSON.stringify(name)},from:0,to:1,after:[],source,statements:[{sql:'CREATE TABLE example_messages (id TEXT PRIMARY KEY NOT NULL, body TEXT NOT NULL)'},{sql:'CREATE INDEX example_messages_body ON example_messages(body)'}]}] satisfies readonly SqliteMigration[];\nexport async function insert(tx:SqliteTransaction,id:string,body:string){return tx.execute({sql:'INSERT INTO example_messages(id, body) VALUES (?, ?)',parameters:[id,body]})}\nexport const inject=['database'];\nexport function apply(ctx:Context){ctx.provide('exampleDomain',{insert,migrations})}\n`;
    const module = template === 'service-slot' ? shared + service : template === 'sqlite-domain' ? shared + sql : shared + 'export const patches=[];\n';
    const manifest = pluginManifest(name, version, template);
    const emitter = metadataEmitter(name, template);
    const test = consumerTest(template);
    const files: Record<string, string> = { 'package.json': JSON.stringify(manifest, null, 2) + '\n', 'tsconfig.json': JSON.stringify({ compilerOptions: { target: 'ES2024', module: 'NodeNext', strict: true, skipLibCheck: false, isolatedModules: true, declaration: true, rootDir: 'src', outDir: 'dist' }, include: ['src/**/*.ts'] }, null, 2) + '\n', 'src/index.ts': module, 'src/slot-key.ts': `import {slotKey} from '@society0/host/slots';\nexport const filterKey=slotKey<(text:string)=>string>(${JSON.stringify(name + '/filter')},1);\n`, 'scripts/metadata.mjs': emitter, 'test/consumer.mjs': test, 'README.md': `# ${name}\n\nRun npm run build, npm test, then npm pack. Sidecars come from compiled metadata; static inspect never imports this module.\n\n${template === 'sqlite-domain' ? 'The domain owns its SQL migration and accepts the caller-owned SqliteTransaction from @society0/core/sqlite/contracts. Pass exported migrations to sqliteBootstrap.prepare/create in request.migrations; the bootstrap service applies them before initializers.' : ''}\n`, 'CHANGELOG.md': '# 0.1.0\n\nInitial author package.\n', 'LICENSE': 'MIT License\n\nCopyright (c) Society0 plugin authors\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.\n' };
    return files;
}
export async function createPlugin(options: ScaffoldOptions): Promise<{
    directory: string;
    files: readonly string[];
}> {
    const directory = resolve(options.directory);
    const files = pluginFiles(options);
    await mkdir(directory);
    for (const [file, content] of Object.entries(files)) {
        const target = join(directory, file);
        await mkdir(resolve(target, '..'), { recursive: true });
        await writeFile(target, content, 'utf8');
    }
    return { directory, files: Object.keys(files) };
}
export const inject = ['commands', 'moduleResolver', 'profileLoader', 'inspector'];
function apply(ctx: Context) {
    ctx.commands.register({ name: 'create-plugin', summary: 'Create an independently packaged plugin', scope: 'host', input: Schema.object({ directory: Schema.string().required(), name: Schema.string().required(), template: Schema.union(['service-slot', 'sqlite-domain', 'config-bundle']).required(), source: Schema.any<ProgramSource>().required() }), execute: (_, input: ScaffoldOptions) => createPlugin(input) });
    const input = Schema.object({ specifier: Schema.string().required(), lock: Schema.any<ModuleLock>().required() });
    ctx.commands.register({ name: 'inspect', summary: 'Read static package metadata without importing it', scope: 'host', input, execute: (_, value: {
            specifier: string;
            lock: ModuleLock;
        }) => ctx.moduleResolver.inspect(ctx.moduleResolver.resolve(value.specifier, value.lock)) });
    ctx.commands.register({ name: 'validate', summary: 'Validate a trusted package and its entrypoint', scope: 'host', input, execute: async (_, value: {
            specifier: string;
            lock: ModuleLock;
        }) => { const identity = ctx.moduleResolver.resolve(value.specifier, value.lock); ctx.moduleResolver.inspect(identity); await ctx.moduleResolver.importVerified(identity); return { valid: true, source: identity.source }; } });
    ctx.commands.register({ name: 'explain', summary: 'Explain live entry ownership and effective configuration', scope: 'host', input: Schema.object({ entryId: Schema.string().required() }), execute: (_, query: {
            entryId: string;
        }) => ctx.inspector.explain(query) });
    ctx.commands.register({ name: 'test', summary: 'Activate and drain a trusted package in an owned test child', scope: 'host', input, execute: async (owner, value: {
            specifier: string;
            lock: ModuleLock;
        }) => {
            const identity = ctx.moduleResolver.resolve(value.specifier, value.lock);
            const module = await ctx.moduleResolver.importVerified(identity);
            const test = await owner.plugin({ name: 'author-test', apply() { } });
            try {
                const fiber = await test.ctx.plugin(module.plugin);
                await fiber.await();
                if (fiber.state !== FiberState.ACTIVE)
                    throw Error(`TEST_PENDING ${value.specifier}: ${Object.keys(fiber.inject).join(', ')}`);
                return { passed: true };
            }
            finally {
                await test.dispose();
            }
        } });
}
export default { Config, name: 'author-tools', inject, apply };
