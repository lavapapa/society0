import { Context, Service } from '@society0/cordis';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { SqliteReadView, SqliteTransaction } from '@society0/core/sqlite';
import type { ThreadView } from '@society0/core/contracts/domain';
import type { ActivationContext } from '@society0/core/contracts/runtime';
import '@society0/host/slots';
import '@society0/core/actions';
import '../memory-repository/index.js';
import { fail, bytes } from '@society0/core/information/page';
import type { Experience } from '../memory-repository/index.js';
import { memoryWrite, memoryRecall, validateWrite, validateRecall, type MemoryQuery } from './policies.js';
import { memoryActions } from './tools.js';
export * from './policies.js';
export const Config = Schema.object({ write: Schema.boolean().default(true), recall: Schema.boolean().default(true), tools: Schema.boolean().default(true), writePolicy: Schema.string().default('explicit-experience/v1'), recallPolicy: Schema.string().default('sql/v1'), maxExtractedBytes: Schema.natural().min(1).default(65536) });
export const metadata: PluginMetadata = { title: 'memory', summary: 'Independent optional experience extraction, recall and active memory actions', configVersion: 1, configPolicy: [], scope: 'generation', allowedScopes: ['generation'], hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './memory', provides: [{ key: 'memory', protocol: 'memory', major: 1, capabilities: [] }], requires: ['memoryRepository', 'slots', 'actions'].map(key => ({ service: { key, protocol: key, major: 1, capabilities: [] }, optional: false, phase: 'module' })) };
declare module '@society0/cordis' {
    interface Context {
        memory: Memory;
    }
}
export class Memory extends Service {
    static Config = Config;
    static inject = ['memoryRepository', 'slots', 'actions'];
    private owner!: Context;
    constructor(ctx: Context, private options: ReturnType<typeof Config>) { super(ctx, 'memory'); }
    [Service.init]() { this.owner = this.ctx; this.ctx.slots.declare({ key: memoryWrite, strategy: 'keyed', required: false, validate: validateWrite }); this.ctx.slots.declare({ key: memoryRecall, strategy: 'keyed', required: false, validate: validateRecall }); this.ctx.slots.contribute(memoryRecall, { id: 'sql/v1', value: { recall: (query, view) => this.owner.memoryRepository.scan(view, query) } }); if (this.options.tools)
        for (const action of memoryActions(this.owner))
            this.ctx.actions.register(action); }
    async write(tx: SqliteTransaction, experience: Experience): Promise<string> { if (!this.options.write)
        fail('MEMORY_WRITE_DISABLED'); await this.owner.memoryRepository.append(tx, experience); return experience.id; }
    async extract(thread: ThreadView, context: ActivationContext): Promise<readonly Experience[]> {
        if (!this.options.write)
            fail('MEMORY_WRITE_DISABLED');
        const lease = await this.owner.slots.acquire(memoryWrite, context);
        try {
            const policy = lease.items.find(item => item.id === this.options.writePolicy);
            if (!policy)
                fail('MEMORY_WRITE_POLICY_UNAVAILABLE');
            const values = await policy.value.extract(thread, context);
            if (bytes(values) > this.options.maxExtractedBytes)
                fail('MEMORY_EXTRACTION_BUDGET');
            return values;
        }
        finally {
            await lease.release();
        }
    }
    async recall(query: MemoryQuery, view: SqliteReadView) {
        if (!this.options.recall)
            fail('MEMORY_RECALL_DISABLED');
        const lease = await this.owner.slots.acquire(memoryRecall, { generation: 'memory-recall' });
        try {
            const policy = lease.items.find(item => item.id === this.options.recallPolicy);
            if (!policy)
                fail('MEMORY_RECALL_POLICY_UNAVAILABLE');
            return await policy.value.recall(query, view);
        }
        finally {
            await lease.release();
        }
    }
}
export default Memory;
