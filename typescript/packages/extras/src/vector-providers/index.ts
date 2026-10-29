import { Context, Service } from '@society0/cordis';
import Schema from '@society0/schemastery';
import type { ApiEntry, PluginMetadata } from '@society0/host/module-resolver';
import type { Lease } from '@society0/core/contracts/common';
import '@society0/host/resources';
import { vectorFault, type MemoryIndex } from './types.js';
export * from './types.js';
export const Config = Schema.object({ runId: Schema.string().required() });
export const metadata: PluginMetadata = { title: 'vector-providers', summary: 'Run-owned, lease-retained vector backends', configVersion: 1, configPolicy: [{ path: '/runId', mode: 'new-run', affectsResults: true, secret: false }], scope: 'run', allowedScopes: ['run'], hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './vector-providers', provides: [{ key: 'vectorProviders', protocol: 'vectorProviders', major: 1, capabilities: [] }], requires: [{ service: { key: 'resources', protocol: 'resources', major: 1, capabilities: [] }, optional: false, phase: 'module' }] };
interface Registration {
    index: MemoryIndex;
    leases: number;
    retiring: boolean;
    idle: PromiseWithResolvers<void>;
}
declare module '@society0/cordis' {
    interface Context {
        vectorProviders: VectorProviders;
    }
}
export class VectorProviders extends Service {
    static Config = Config;
    static inject = ['resources'];
    readonly runId: string;
    private registrations = new Map<string, Registration>();
    constructor(ctx: Context, options: {
        runId: string;
    }) { super(ctx, 'vectorProviders'); this.runId = options.runId; }
    register(name: string, index: MemoryIndex): () => void | Promise<void> {
        if (index.runId !== this.runId)
            vectorFault('VECTOR_RUN_MISMATCH');
        if (this.registrations.has(name))
            vectorFault('VECTOR_PROVIDER_DUPLICATE');
        const entry: Registration = { index, leases: 0, retiring: false, idle: Promise.withResolvers<void>() };
        const dispose = this.ctx.resources.use('vector.provider:' + name, async () => {
            entry.retiring = true;
            if (this.registrations.get(name) === entry)
                this.registrations.delete(name);
            if (!entry.leases)
                entry.idle.resolve();
            await entry.idle.promise;
            await index.close();
        });
        this.registrations.set(name, entry);
        return dispose;
    }
    async acquire(name: string): Promise<Lease<MemoryIndex>> {
        const entry = this.registrations.get(name);
        if (!entry || entry.retiring)
            vectorFault('VECTOR_PROVIDER_UNAVAILABLE');
        entry.leases++;
        let released = false;
        const release = () => { if (released)
            return; released = true; entry.leases--; if (entry.retiring && !entry.leases)
            entry.idle.resolve(); };
        try {
            const owned = this.ctx.resources.use('vector.lease:' + name, release);
            return { value: entry.index, release: async () => { await owned(); } };
        }
        catch (error) {
            release();
            throw error;
        }
    }
    describe() { return [...this.registrations].map(([name, entry]) => ({ name, runId: entry.index.runId, generation: entry.index.generation, space: entry.index.space, identity: entry.index.identity, distance: entry.index.distance, leases: entry.leases })); }
}
export default VectorProviders;
