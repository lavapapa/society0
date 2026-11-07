import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ maxInlineFileBytes: Schema.natural().min(1).default(1048576), maxSearchBytes: Schema.natural().min(1).default(67108864), maxSearchOutputBytes: Schema.natural().min(1).default(4194304), maxSearchMs: Schema.natural().min(1).default(30000) });
export const metadata: PluginMetadata = { "title": "exploration", "summary": "统一主体四空间、挂载、路径身份、可见性和有界读取；实际状态归领域SQL。", "configVersion": 1, "configPolicy": [{ "path": "/maxInlineFileBytes", "mode": "boundary", "affectsResults": true, "secret": false }, { "path": "/maxSearchBytes", "mode": "boundary", "affectsResults": true, "secret": false }, { "path": "/maxSearchOutputBytes", "mode": "boundary", "affectsResults": true, "secret": false }, { "path": "/maxSearchMs", "mode": "boundary", "affectsResults": true, "secret": false }], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./exploration", "provides": [{ "key": "exploration", "protocol": "exploration", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "tasks", "protocol": "tasks", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import { Context, Service } from '@society0/cordis';
import type { TaskScope } from '@society0/host/tasks';
import { randomUUID } from 'node:crypto';
import type { Invocation, Lease } from '../contracts/common.js';
import type { DiscoveryScope } from '../contracts/data.js';
import type { ExplorationMount, ExplorationSession } from '../contracts/exploration.js';
import { RegistryClock, fail } from '../information/page.js';
import { explorationMountSlot, canonical, type MountRegistration, type ExplorationOptions } from './registry.js';
import { ExplorationView } from './session.js';
import '@society0/host/tasks';
declare module '@society0/cordis' {
    interface Context {
        exploration: Exploration;
    }
}
export class Exploration extends Service {
    static Config = Config;
    static inject = ['database', 'artifacts', 'slots', 'tasks'];
    private tasks!: TaskScope;
    private registry = new RegistryClock();
    get limits(): ExplorationOptions { return this.options; }
    constructor(ctx: Context, private options: ExplorationOptions) { super(ctx, 'exploration'); ctx.slots.declare({ key: explorationMountSlot, strategy: 'keyed', required: false, validate: validateMount }); }
    [Service.init]() { this.tasks = this.ctx.tasks.bind(); this.ctx.effect(() => () => this.tasks.drain('exploration closing')); }
    register(mount: ExplorationMount) {
        canonical(mount.prefix);
        if (mount.prefix === '/')
            return fail('EXPLORATION_ROOT_RESERVED');
        return this.ctx.slots.contribute(explorationMountSlot, { id: mount.prefix, value: { mount, registrationId: randomUUID() } });
    }
    async acquire(scope: DiscoveryScope, context: Invocation): Promise<Lease<ExplorationSession>> { const selected = await this.ctx.slots.acquire(explorationMountSlot, context); const registry = this.registry.observe(selected.items.map(item => item.value.registrationId)); const view = new ExplorationView(this.ctx, selected, scope, context, this.tasks, this.options, registry); return { value: view, release: () => view.release() }; }
}
function validateMount(value: unknown): MountRegistration {
    const entry = value as MountRegistration;
    if (typeof entry?.mount?.open !== 'function')
        return fail('EXPLORATION_MOUNT');
    return entry;
}
export default Exploration;
