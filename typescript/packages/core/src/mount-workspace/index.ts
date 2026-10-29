import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "mount-workspace", "summary": "注册当前主体/workspace可写目录", "configVersion": 1, "configPolicy": [], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./mount-workspace", "provides": [], "requires": [{ "service": { "key": "exploration", "protocol": "exploration", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "workspace", "protocol": "workspace", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import type { Context } from '@society0/cordis';
import type { WritableExplorationMount } from '../contracts/exploration.js';
import '../exploration/index.js';
export function writableMount(workspace: WritableExplorationMount, prefix: string): WritableExplorationMount { return { id: 'mount' + prefix + '/v1', prefix, visible: (...args) => workspace.visible(...args), stat: (...args) => workspace.stat(...args), list: (...args) => workspace.list(...args), open: (...args) => workspace.open(...args), collect: (...args) => workspace.collect(...args), writeFile: (...args) => workspace.writeFile(...args), mutate: (...args) => workspace.mutate(...args) }; }
export default { Config, inject: ["exploration", "workspace"], apply(ctx: Context) { ctx.exploration.register(writableMount(ctx.get('workspace') as WritableExplorationMount, '/workspace')); } };
