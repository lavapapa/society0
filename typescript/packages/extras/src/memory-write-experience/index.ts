import type { Context } from '@society0/cordis';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { MemoryWritePolicy, MemoryExtractionContext } from '@society0/core/contracts/cognition';
import type { DeferredProduce, DeferredWork } from '@society0/core/contracts/deferred-work';
import type { Json } from '@society0/core/contracts/common';
import type { ArtifactRef } from '@society0/core/artifacts-local';
import type { StoredValue } from '@society0/core/contracts/data';
import type { Experience } from '../memory-repository/index.js';
import type { MemoryRepository } from '../memory-repository/index.js';
import { memoryWrite } from '../memory/policies.js';
import { bytes, fail } from '@society0/core/information/page';
import '@society0/core/thread';
import '@society0/host/slots';
export const Config = Schema.object({ pageItems: Schema.natural().min(1).default(16), maxMessages: Schema.natural().min(1).default(512), maxMessageBytes: Schema.natural().min(1).default(65536), maxOutputBytes: Schema.natural().min(1).default(65536) });
export const metadata: PluginMetadata = { title: 'memory-write-experience', summary: 'Extract explicitly marked experience messages while preserving complete Thread history', configVersion: 1, configPolicy: [{ path: '/pageItems', mode: 'new-run', affectsResults: true }, { path: '/maxMessages', mode: 'new-run', affectsResults: true }, { path: '/maxMessageBytes', mode: 'new-run', affectsResults: true }, { path: '/maxOutputBytes', mode: 'new-run', affectsResults: true }], scope: 'generation', allowedScopes: ['generation'], hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './memory-write-experience', provides: [], requires: ['slots', 'threads'].map(key => ({ service: { key, protocol: key, major: 1, capabilities: [] }, optional: false, phase: 'module' })) };
const Message = Schema.object({ id: Schema.string().required(), content: Schema.any<Json>().required(), originalProviderFields: Schema.any<Json>() });
function artifact(value: Json | undefined): ArtifactRef | undefined { if (!value || typeof value !== 'object' || Array.isArray(value))
    return; const object = value as Readonly<Record<string, Json>>; if (typeof object.id === 'string' && typeof object.bytes === 'string' && typeof object.mediaType === 'string')
    return { id: object.id, bytes: object.bytes, mediaType: object.mediaType }; }
class ExplicitExperience implements MemoryWritePolicy {
    private threads: Context['threads'];
    constructor(ctx: Context, private options: ReturnType<typeof Config>) { this.threads = ctx.threads; }
    private async message(payload: StoredValue, signal: AbortSignal): Promise<ReturnType<typeof Message>> {
        if (payload.kind === 'inline')
            return Schema.resolve(payload.value, Message, {})[0];
        if (Number(payload.ref.rawBytes) > this.options.maxMessageBytes)
            fail('MEMORY_THREAD_MESSAGE_BUDGET');
        const chunks: Uint8Array[] = [];
        for await (const chunk of this.threads.readPayload(payload.ref, { offset: '0', length: Number(payload.ref.rawBytes), maxBytes: this.options.maxMessageBytes }, signal))
            chunks.push(chunk);
        return Schema.resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')), Message, {})[0];
    }
    async extract(thread: Parameters<MemoryWritePolicy['extract']>[0], context: MemoryExtractionContext): Promise<readonly Experience[]> {
        const view = await context.database.snapshot({ maxAgeMs: 30000 });
        try {
            const current = await this.threads.open(context.actorId, context.momentId, view);
            if (current.id !== thread.id || current.headSeq !== thread.headSeq)
                fail('MEMORY_THREAD_STALE');
            const result: Experience[] = [];
            let cursor: string | undefined, seen = 0, size = 0;
            do {
                context.signal.throwIfAborted();
                const page = await this.threads.page(thread.id, { actorId: context.actorId, momentId: context.momentId, query: null }, view, { maxItems: this.options.pageItems, maxResponseBytes: this.options.maxMessageBytes, cursor });
                seen += page.items.length;
                if (seen > this.options.maxMessages)
                    fail('MEMORY_THREAD_SCAN_BUDGET');
                for (const entry of page.items) {
                    if (entry.role !== 'experience')
                        continue;
                    const message = await this.message(entry.payload, context.signal);
                    const value: Experience = { id: thread.id + ':' + message.id, actorId: thread.actorId, threadId: thread.id, body: message.content, artifacts: [artifact(message.content), artifact(message.originalProviderFields)].filter((item): item is ArtifactRef => item !== undefined) };
                    size += bytes(value);
                    if (size > this.options.maxOutputBytes)
                        fail('MEMORY_EXTRACTION_BUDGET');
                    result.push(value);
                }
                cursor = page.next;
            } while (cursor);
            return result;
        }
        finally {
            await view.close();
        }
    }
}
export const MEMORY_EXPERIENCE_JOB = 'memory-write-experience.extract';
export const MEMORY_EXPERIENCE_NAMESPACE = 'memory-write-experience';
export const inject = ['slots', 'threads'];
function apply(ctx: Context, options: ReturnType<typeof Config>) { ctx.slots.inject(memoryWrite, async (child) => { await child.plugin({ inject, apply(owner) {
    const policy = new ExplicitExperience(owner, options);
    owner.slots.contribute(memoryWrite, { id: 'explicit-experience/v1', value: policy });
    owner.inject(['deferredWork'], scoped => {
        const deferred = scoped.get('deferredWork') as DeferredWork;
        scoped.effect(() => deferred.register(MEMORY_EXPERIENCE_JOB, {
            run: async (job, context) => {
                const inputs = job.inputs as {
                    readonly threadId?: unknown; readonly actorId?: unknown; readonly momentId?: unknown;
                    readonly logicalTime?: unknown; readonly runId?: unknown; readonly generation?: unknown;
                } | null;
                if (typeof inputs?.threadId !== 'string' || typeof inputs.actorId !== 'string' || typeof inputs.momentId !== 'string'
                    || inputs.logicalTime === undefined || typeof inputs.runId !== 'string' || typeof inputs.generation !== 'string')
                    fail('MEMORY_JOB_INPUT');
                const view = await context.database.snapshot({ maxAgeMs: 30000 });
                let thread: Awaited<ReturnType<Context['threads']['open']>>;
                try {
                    thread = await owner.threads.open(inputs.actorId, inputs.momentId, view);
                }
                finally { await view.close(); }
                if (thread.id !== inputs.threadId)
                    fail('MEMORY_JOB_THREAD');
                const extraction: MemoryExtractionContext = {
                    ctx: owner, runId: inputs.runId, generation: inputs.generation,
                    operationId: 'deferred:' + job.id, signal: context.signal,
                    actorId: inputs.actorId, momentId: inputs.momentId,
                    logicalTime: inputs.logicalTime as Json, database: context.database
                };
                const values = await policy.extract(thread, extraction);
                const repository = owner.get('memoryRepository', false) as MemoryRepository | undefined;
                if (values.length && !repository)
                    fail('MEMORY_REPOSITORY_UNAVAILABLE');
                const produce: DeferredProduce = {
                    output: { count: values.length },
                    async write(tx) {
                        await repository!.appendMany(tx, values);
                    }
                };
                return produce;
            }
        }));
    });
} }); }); }
export default { Config, inject, apply };
