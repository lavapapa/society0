import type { MountedTree } from '../profile-loader/index.js';
import Schema from '@society0/schemastery';
import { hostMetadata, hostApi } from '../module-resolver/metadata.js';
import { Context, Service, FiberState, type Fiber } from '@society0/cordis';
import type { ModuleIdentity } from '../module-resolver/index.js';
import '../profile-loader/index.js';
import '../diagnostics/index.js';
export const Config = Schema.object({});
export const metadata = hostMetadata('inspector', 'Explain native ownership and effective configuration');
export const api = hostApi('./inspector', ["inspector"], ["diagnostics", "profileLoader", "moduleResolver"]);
declare module '@society0/cordis' {
    interface Context {
        inspector: Inspector;
    }
}
export class Inspector extends Service {
    static Config = Config;
    static inject = ['diagnostics', 'profileLoader', 'moduleResolver'];
    constructor(ctx: Context) { super(ctx, 'inspector'); }
    private fiberDetails(fiber: Fiber | undefined) {
        if (!fiber) {
            const requires: string[] = [], services: {
                key: string;
                retiring: boolean;
            }[] = [], effects: {
                label: string;
                count: number;
            }[] = [];
            return { name: undefined, status: 'pending', owner: undefined, requires, missingServices: requires, services, effects };
        }
        const requires = Object.keys(fiber.inject);
        const missingServices = requires.filter(key => !fiber.store?.[key] && fiber.ctx.get(key) === undefined);
        return { name: fiber.name, status: FiberState[fiber.state].toLowerCase(), owner: this.ctx.diagnostics.owner(fiber), requires, missingServices, services: [...fiber.provisions].map(binding => ({ key: binding.name, retiring: !!binding.retired })), effects: fiber.getEffects().map(effect => ({ label: effect.label, count: effect.children.length })) };
    }
    private treeDetails(tree: MountedTree) {
        return tree.plan.flat.map(entry => { const fiber = tree.entries.get(entry.id); const actual = this.fiberDetails(fiber); return { ...actual, entryId: entry.id, source: entry.binding?.identity?.source, status: entry.disabled ? 'disabled' : fiber ? actual.status : 'unmounted', scope: entry.scope, isolate: entry.isolate, config: entry.evidence }; });
    }
    plugins() {
        const managed = this.ctx.profileLoader.trees().flatMap(tree => this.treeDetails(tree));
        const known = new Set(managed.map(entry => entry.owner?.uid));
        const native = [...this.ctx.registry.values()].flatMap(runtime => [...runtime.fibers]).filter(fiber => fiber.uid !== null && !known.has(fiber.uid));
        return [...managed, ...native.map(fiber => ({ ...this.fiberDetails(fiber), entryId: undefined, source: undefined, scope: undefined, isolate: undefined, config: undefined }))];
    }
    explain(query: {
        entryId?: string;
        service?: string;
        slot?: string;
    }) {
        if (query.entryId) {
            const entry = this.plugins().find(entry => entry.entryId === query.entryId);
            if (!entry)
                throw Error(`ENTRY_UNKNOWN ${query.entryId}`);
            return entry;
        }
        if (query.slot)
            return this.ctx.get('slots')?.inspect(query.slot) ?? { pending: ['slots-service'], contributions: [], selections: [] };
        const plugins = this.plugins();
        if (query.service)
            return { providers: plugins.filter(entry => entry.services.some(service => service.key === query.service)), consumers: plugins.filter(entry => entry.requires.includes(query.service!)), pending: plugins.filter(entry => entry.missingServices.includes(query.service!)) };
        return { plugins, resources: this.ctx.get('resources')?.closeReport() };
    }
    errors() { return this.ctx.diagnostics.snapshot().errors; }
    inspect(identity: ModuleIdentity) { return this.ctx.moduleResolver.inspect(identity); }
}
export default Inspector;
