import {compareCodePoints} from '@society0/host/order';
import type { Context } from '@society0/cordis';
import type { SelectionLease } from '@society0/host/slots';
import { randomUUID } from 'node:crypto';
import type { Invocation, Json } from '../contracts/common.js';
import type { PageBounds } from '../contracts/data.js';
import type { PerceptionSource, PerceptionScope, PerceptionBatch, PerceptionInput, PerceptionSelection, PerceptionRead, PerceptionPreparation } from '../contracts/perception.js';
import type { SqliteReadView, SqliteTransaction } from '../sqlite/contracts.js';
import { equal, fail, bytes, bounds as pageBounds } from '../information/page.js';
import { attachValue } from '../results/payload.js';
import { perceptionSourceSlot, type SourceRegistration } from './registry.js';
import '../thread/index.js';
import '../sqlite/index.js';
interface Position {
    cursor: Json;
    through: Json;
}
type Reader = SqliteReadView | SqliteTransaction;
function domain(scope: PerceptionScope, source: PerceptionSource) { return source.cursorScope === 'actor' ? '' : scope.threadId; }
export function selectSources(ids: readonly string[], lease: SelectionLease<SourceRegistration>) {
    if (new Set(ids).size !== ids.length)
        return fail('PERCEPTION_DUPLICATE');
    return ids.map(id => {
        const entry = lease.items.find(item => item.id === id);
        if (!entry)
            return fail('PERCEPTION_MISSING');
        return entry.value;
    });
}
export class PerceptionSession implements PerceptionSelection {
    private closing = false;
    private active = new Set<Promise<unknown>>();
    private closed?: Promise<void>;
    constructor(private ctx: Context, private selected: SelectionLease<SourceRegistration>, private entries: readonly SourceRegistration[], private context: Invocation) { }
    private async run<T>(body: () => Promise<T>): Promise<T> {
        if (this.closing)
            return fail('PERCEPTION_RELEASED');
        this.context.signal.throwIfAborted();
        const work = body();
        this.active.add(work);
        try {
            return await work;
        }
        finally {
            this.active.delete(work);
        }
    }
    release() { return this.closed ??= (async () => { this.closing = true; await Promise.allSettled([...this.active]); await this.selected.release(); })(); }
    private async position(scope: PerceptionScope, source: PerceptionSource, reader: Reader): Promise<Position | undefined> {
        const [row] = await reader.query({ sql: 'SELECT cursor,through FROM perception_progress WHERE actor_id=? AND source_id=? AND domain_id=?', parameters: [scope.actorId, source.id, domain(scope, source)] }, { maxRows: 1, maxBytes: 65536 });
        if (!row)
            return undefined;
        return { cursor: JSON.parse(String(row.cursor)), through: row.through === null ? null : JSON.parse(String(row.through)) };
    }
    collect(scope: PerceptionScope, bounds: PageBounds, context: Invocation) { return this.run(() => this.collectBatch(scope, bounds, context)); }
    private async collectBatch(scope: PerceptionScope, bounds: PageBounds, context: Invocation): Promise<PerceptionBatch> {
        pageBounds(bounds);
        if (this.entries.length > bounds.maxItems)
            return fail('PERCEPTION_BUDGET');
        const prepared = await Promise.all(this.entries.map(entry => entry.source.prepare?.(scope, bounds, context)));
        this.context.signal.throwIfAborted();
        const view = await this.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            const reads = await this.readSources(scope, bounds, prepared, view);
            const batch = { id: randomUUID(), scope, reads };
            if (bytes(batch) > bounds.maxResponseBytes)
                return fail('PERCEPTION_BUDGET');
            return batch;
        }
        finally {
            await view.close();
        }
    }
    private async progress(scope: PerceptionScope, entry: SourceRegistration, view: SqliteReadView): Promise<Position> { return await this.position(scope, entry.source, view) ?? { cursor: null, through: null }; }
    private async readSources(scope: PerceptionScope, bounds: PageBounds, prepared: readonly (PerceptionPreparation | undefined)[], view: SqliteReadView) {
        const reads: PerceptionRead[] = [];
        let used = 0;
        const maxItems = Math.max(1, Math.floor(bounds.maxItems / Math.max(1, this.entries.length)));
        for (const [index, entry] of this.entries.entries()) {
            // 剩余字节按尚未读取的来源数平均分配：前面的来源没用完的份额自动留给后面。
            const share = Math.floor((bounds.maxResponseBytes - used) / (this.entries.length - index));
            const read = await this.readSource(scope, entry, prepared[index], view, { maxItems, maxResponseBytes: share });
            if (read) {
                reads.push(read);
                used += bytes(read);
            }
        }
        return reads;
    }
    private async readSource(scope: PerceptionScope, entry: SourceRegistration, prepared: PerceptionPreparation | undefined, view: SqliteReadView, bounds: PageBounds): Promise<PerceptionRead | undefined> {
        const source = entry.source;
        if (!await source.available(scope, view))
            return undefined;
        const versions = await source.versions(scope, view);
        if (prepared && !equal(prepared.versions, versions))
            return fail('PERCEPTION_PREPARED_STALE');
        const position = await this.progress(scope, entry, view);
        const page = await source.collect(scope, position.cursor, position.through, view, bounds, prepared);
        if (page.items.length > bounds.maxItems || bytes(page) > bounds.maxResponseBytes)
            return fail('PERCEPTION_BUDGET');
        return { sourceId: source.id, registrationId: entry.registrationId, cursorScope: source.cursorScope, before: position.cursor, page, versions };
    }
    commit(tx: SqliteTransaction, batch: PerceptionBatch, inputs: readonly PerceptionInput[]) { return this.run(() => this.commitBatch(tx, batch, inputs)); }
    private async commitBatch(tx: SqliteTransaction, batch: PerceptionBatch, inputs: readonly PerceptionInput[]) {
        if (await this.ctx.threads.owner(batch.scope.threadId, tx) !== batch.scope.actorId)
            return fail('PERCEPTION_THREAD_SCOPE');
        const current = await this.ctx.slots.acquire(perceptionSourceSlot, this.context);
        try {
            const sourceIds = new Set(batch.reads.map(read => read.sourceId));
            if (inputs.some(input => !sourceIds.has(input.sourceId)))
                return fail('PERCEPTION_INPUT_MAPPING');
            const registrations = new Set(current.items.map(item => item.value.registrationId));
            for (const read of batch.reads)
                await this.commitSource(tx, batch, read, inputs, registrations);
            this.context.signal.throwIfAborted();
        }
        finally {
            await current.release();
        }
    }
    private source(read: PerceptionRead, current: ReadonlySet<string>) {
        const entry = this.entries.find(entry => entry.source.id === read.sourceId);
        if (!entry || entry.registrationId !== read.registrationId || !current.has(read.registrationId))
            return fail('PERCEPTION_REGISTRATION_STALE');
        return entry.source;
    }
    private async validateState(source: PerceptionSource, scope: PerceptionScope, read: PerceptionRead, tx: SqliteTransaction) {
        if (!await source.available(scope, tx))
            return fail('PERCEPTION_UNAVAILABLE');
        const position = await this.position(scope, source, tx);
        if (!equal(position?.cursor ?? null, read.before))
            return fail('PERCEPTION_STALE');
        if (!equal(await source.versions(scope, tx), read.versions))
            return fail('PERCEPTION_STALE');
    }
    private inputs(read: PerceptionRead, inputs: readonly PerceptionInput[]) {
        const selected = inputs.filter(input => input.sourceId === read.sourceId), mapped = selected.flatMap(input => input.perceptIds), expected = read.page.items.map(item => item.id);
        if (new Set(expected).size !== expected.length || new Set(mapped).size !== mapped.length || !equal([...mapped].sort(compareCodePoints), [...expected].sort(compareCodePoints)))
            return fail('PERCEPTION_INPUT_MAPPING');
        return selected;
    }
    private async commitSource(tx: SqliteTransaction, batch: PerceptionBatch, read: PerceptionRead, inputs: readonly PerceptionInput[], current: ReadonlySet<string>) {
        const source = this.source(read, current);
        await this.validateState(source, batch.scope, read, tx);
        const selected = this.inputs(read, inputs);
        await this.materials(tx, batch, read);
        for (const input of selected)
            await this.input(tx, batch, read, input);
        await this.advance(tx, batch.scope, source, read);
    }
    private async materials(tx: SqliteTransaction, batch: PerceptionBatch, read: PerceptionRead) {
        for (const percept of read.page.items) {
            await attachValue(tx, percept.material, this.ctx.artifacts);
            await tx.execute({ sql: 'INSERT INTO perception_material VALUES(?,?,?,?,?,?,?)', parameters: [batch.id, read.sourceId, percept.id, batch.scope.threadId, percept.kind, JSON.stringify(percept.material), JSON.stringify(percept.provenance)] });
        }
    }
    private async input(tx: SqliteTransaction, batch: PerceptionBatch, read: PerceptionRead, input: PerceptionInput) {
        const consumerId = 'perception:' + read.sourceId;
        await this.ctx.threads.appendInput(tx, { threadId: batch.scope.threadId, expectedConsumer: await this.ctx.threads.consumer(batch.scope.threadId, consumerId, tx) ?? null, nextConsumer: { consumerId, cursor: read.page.nextCursor }, message: input.message, contexts: input.contexts });
        for (const id of input.perceptIds)
            await tx.execute({ sql: 'INSERT INTO perception_links VALUES(?,?,?,?)', parameters: [batch.id, read.sourceId, id, input.message.id] });
    }
    private async advance(tx: SqliteTransaction, scope: PerceptionScope, source: PerceptionSource, read: PerceptionRead) { await tx.execute({ sql: 'INSERT INTO perception_progress VALUES(?,?,?,?,?) ON CONFLICT(actor_id,source_id,domain_id) DO UPDATE SET cursor=excluded.cursor,through=excluded.through', parameters: [scope.actorId, read.sourceId, domain(scope, source), JSON.stringify(read.page.nextCursor), read.page.hasMore ? JSON.stringify(read.page.through) : null] }); }
}
