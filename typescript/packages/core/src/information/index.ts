import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ pageSize: Schema.natural().min(1).default(100) });
export const metadata: PluginMetadata = { "title": "information", "summary": "登记类型化信息源；领域提供者以原生SQL分页，保留总数、来源、主体可见性和快照revision。", "configVersion": 1, "configPolicy": [{ "path": "/pageSize", "mode": "boundary", "affectsResults": true, "secret": false }], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./information", "provides": [{ "key": "information", "protocol": "information", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import { Context, Service } from '@society0/cordis';
import { randomUUID } from 'node:crypto';
import { slotKey } from '@society0/host/slots';
import type { InformationSource } from '../contracts/domain.js';
import type { DiscoveryScope, PageRequest, Page, StoredValue } from '../contracts/data.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import { after, cursor, identity, page, sized, fail, bounds, RegistryClock } from './page.js';
const key = slotKey<{
    source: InformationSource;
    registrationId: string;
}>('society/information-source');
declare module '@society0/cordis' {
    interface Context {
        information: Information;
    }
}
export class Information extends Service {
    static Config = Config;
    static inject = ['slots', 'database'];
    private registryClock = new RegistryClock();
    readonly metrics = { availabilityScans: 0, keysetCandidates: 0 };
    constructor(ctx: Context, private options: {
        pageSize: number;
    }) {
        super(ctx, 'information');
        ctx.slots.declare({ key, strategy: 'keyed', required: false, validate: (v: any) => {
                if (typeof v?.source?.query !== 'function' || typeof v?.source?.available !== 'function')
                    return fail('INFORMATION_INVALID');
                return v;
            } });
    }
    register(source: InformationSource) { return this.ctx.slots.contribute(key, { id: source.id, value: { source, registrationId: randomUUID() } }); }
    async describe(name: string, scope: DiscoveryScope, view: SqliteReadView) {
        const selected = await this.ctx.slots.acquire(key, { generation: 'read' });
        try {
            const entry = selected.items.find(x => x.id === name)?.value;
            if (!entry || !await entry.source.available(scope, view))
                return fail('INFORMATION_UNAVAILABLE');
            return { id: entry.source.id, schema: entry.source.schema, capabilities: entry.source.capabilities, registrationId: entry.registrationId };
        }
        finally {
            await selected.release();
        }
    }
    version() {
        return this.registryClock.capture(this.ctx.slots.acquire(key, { generation: 'read' }));
    }
    async discover(scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) {
        bounds(request);
        request = { ...request, maxItems: Math.min(request.maxItems, this.options.pageSize) };
        const selected = await this.ctx.slots.acquire(key, { generation: 'read' });
        try {
            const id = identity(scope, view, this.registryClock.observe(selected.items.map(x => x.value.registrationId)));
            const last = after(request.cursor, id);
            const available = [];
            let total = 0;
            for (const entry of [...selected.items].sort((a, b) => a.id < b.id ? -1 : 1)) {
                this.metrics.availabilityScans++;
                if (!await entry.value.source.available(scope, view))
                    continue;
                total++;
                if (last !== undefined && entry.id <= last)
                    continue;
                this.metrics.keysetCandidates++;
                if (available.length <= request.maxItems) {
                    const { id, schema, capabilities } = entry.value.source;
                    available.push({ id, schema, capabilities });
                }
            }
            return page(available, String(total), view.revision, request, id, x => x.id);
        }
        finally {
            await selected.release();
        }
    }
    async query(name: string, input: any, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest): Promise<Page<StoredValue>> {
        bounds(request);
        request = { ...request, maxItems: Math.min(request.maxItems, this.options.pageSize) };
        const selected = await this.ctx.slots.acquire(key, { generation: 'read' });
        try {
            const entry = selected.items.find(x => x.id === name)?.value;
            if (!entry || !await entry.source.available(scope, view))
                return fail('INFORMATION_UNAVAILABLE');
            const id = identity(scope, view, entry.registrationId, { scope: scope.query, input, name });
            const next = after(request.cursor, id);
            const reserve = Buffer.byteLength(cursor(id, '')) + 64;
            const remaining = request.maxResponseBytes - reserve;
            if (remaining < 1)
                return fail('PAGE_RESPONSE_BUDGET');
            const result = await entry.source.query(input, scope, view, { ...request, maxResponseBytes: remaining, cursor: next });
            if (result.revision !== view.revision)
                return fail('INFORMATION_SNAPSHOT');
            return sized({ ...result, ...(result.next ? { next: cursor(id, result.next) } : {}), responseBytes: 0 }, request.maxResponseBytes);
        }
        finally {
            await selected.release();
        }
    }
}
export default Information;
export * from './page.js';
