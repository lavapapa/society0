import { getTraceable } from '@society0/cordis';
import Schema from '@society0/schemastery';
import { hostMetadata, hostApi } from '../module-resolver/metadata.js';
import { Context, Service, type Fiber } from '@society0/cordis';
import { Retention, immutableItems } from '../composition-strategies/lifecycle.js';
import type { Candidate } from '../composition-strategies/index.js';
import '../resources/index.js';
export const Config = Schema.object({});
export const metadata = hostMetadata('slots', 'Own declaration epochs and immutable selection leases', [], ['host', 'run', 'generation']);
export const api = hostApi('./slots', ["slots"], ["compositionStrategies", "resources"]);
declare const valueType: unique symbol;
export interface SlotKey<T> {
    readonly id: string;
    readonly version: number;
    readonly [valueType]?: (value: T) => T;
}
type Key = Pick<SlotKey<unknown>, 'id' | 'version'>;
export function slotKey<T>(id: string, version = 1): SlotKey<T> {
    if (!id.includes('/') || !Number.isSafeInteger(version) || version < 1)
        throw TypeError('INVALID_SLOT_KEY');
    return Object.freeze({ id, version });
}
export interface SlotSpec<T> {
    readonly key: SlotKey<T>;
    readonly validate: (value: unknown) => T;
    readonly strategy: string;
    readonly required: boolean;
}
export interface Contribution<T> extends Candidate {
    readonly value: T;
}
export interface SlotDescriptor {
    readonly id: string;
    readonly version: number;
    readonly epoch: number;
    readonly strategy: string;
    readonly owner: {
        readonly uid: number | null;
        readonly name: string;
    };
}
export interface SelectionLease<T> {
    readonly epoch: number;
    readonly generation: string;
    readonly items: readonly {
        readonly id: string;
        readonly value: T;
    }[];
    release(): Promise<void>;
}
interface Declaration {
    key: Key;
    validate: (value: unknown) => unknown;
    strategy: string;
    required: boolean;
    descriptor: SlotDescriptor;
    retention: Retention;
}
interface RecordEntry {
    key: Key;
    contribution: Contribution<unknown>;
    retention: Retention;
    epoch?: number;
    owner: {
        uid: number | null;
        name: string;
    };
}
interface Watcher {
    ctx: Context;
    key: Key;
    apply: (ctx: Context, descriptor: SlotDescriptor) => unknown;
    fiber?: Fiber;
    declaration?: Declaration;
    stopped: boolean;
    transition: Promise<void>;
}
declare module '@society0/cordis' {
    interface Context {
        slots: Slots;
    }
}
export class Slots extends Service {
    static Config = Config;
    static inject = ['compositionStrategies', 'resources'];
    private readonly declarations = new Map<string, Declaration>();
    private readonly contributions = new Map<string, Map<string, RecordEntry>>();
    private readonly watchers = new Set<Watcher>();
    private epoch = 0;
    private readonly selections = new Set<{
        key: string;
        epoch: number;
        generation: string;
        items: readonly RecordEntry[];
    }>();
    constructor(ctx: Context) { super(ctx, 'slots'); }
    private version(key: Key, decl = this.declarations.get(key.id)) {
        if (decl && key.version !== decl.key.version)
            throw Error(`SLOT_VERSION ${key.id}: requested ${key.version}, declared ${decl.key.version}`);
    }
    private refresh(w: Watcher): Promise<void> {
        w.transition = w.transition.then(async () => {
            const desired = w.stopped ? undefined : this.declarations.get(w.key.id);
            if (desired === w.declaration)
                return;
            const previous = w.fiber;
            w.fiber = undefined;
            w.declaration = undefined;
            await previous?.dispose();
            const current = w.stopped ? undefined : this.declarations.get(w.key.id);
            w.declaration = current;
            if (current && !w.stopped) {
                this.version(w.key, current);
                const caller = getTraceable(w.ctx, w.ctx);
                w.fiber = await caller.plugin({ apply: ctx => w.apply(ctx, current.descriptor) });
            }
        });
        return w.transition;
    }
    private notify(id: string) { return Promise.all([...this.watchers].filter(w => w.key.id === id).map(w => this.refresh(w))); }
    private pendingDeclaration<T>(spec: SlotSpec<T>): RecordEntry[] {
        for (const watcher of this.watchers)
            if (watcher.key.id === spec.key.id && watcher.key.version !== spec.key.version)
                throw Error(`SLOT_VERSION ${spec.key.id}: injector ${watcher.key.version}, declared ${spec.key.version}`);
        const pending = [...(this.contributions.get(spec.key.id)?.values() ?? [])];
        for (const item of pending) {
            if (item.key.version !== spec.key.version)
                throw Error(`SLOT_VERSION ${spec.key.id} contributor ${item.contribution.id}`);
            spec.validate(item.contribution.value);
        }
        return pending;
    }
    declare<T>(spec: SlotSpec<T>): () => void | Promise<void> {
        slotKey(spec.key.id, spec.key.version);
        if (this.declarations.has(spec.key.id))
            throw Error(`SLOT_DUPLICATE ${spec.key.id}`);
        const pending = this.pendingDeclaration(spec);
        const epoch = ++this.epoch;
        const record: Declaration = { ...spec, key: { id: spec.key.id, version: spec.key.version }, retention: new Retention(), descriptor: Object.freeze({ id: spec.key.id, version: spec.key.version, epoch, strategy: spec.strategy, owner: { uid: this.ctx.fiber.uid, name: this.ctx.fiber.name } }) };
        const close = this.ctx.resources.use(`slots.declare(${spec.key.id}:${epoch})`, async () => {
            if (this.declarations.get(spec.key.id) === record)
                this.declarations.delete(spec.key.id);
            const members = [...(this.contributions.get(spec.key.id)?.values() ?? [])].filter(item => item.epoch === epoch);
            for (const item of members) {
                const map = this.contributions.get(spec.key.id);
                if (map?.get(item.contribution.id) === item)
                    map.delete(item.contribution.id);
            }
            await Promise.all([this.notify(spec.key.id), record.retention.retire(), ...members.map(item => item.retention.retire())]);
        });
        this.declarations.set(spec.key.id, record);
        for (const item of pending)
            item.epoch = epoch;
        void this.notify(spec.key.id).catch(error => this.ctx.logger.error(error));
        return close;
    }
    contribute<T>(key: SlotKey<T>, contribution: Contribution<T>): () => void | Promise<void> {
        this.version(key);
        const decl = this.declarations.get(key.id);
        decl?.validate(contribution.value);
        let map = this.contributions.get(key.id);
        if (!map) {
            map = new Map();
            this.contributions.set(key.id, map);
        }
        if (map.has(contribution.id))
            throw Error(`CONTRIBUTION_DUPLICATE ${key.id}/${contribution.id}`);
        if (contribution.replace && !map.has(contribution.replace))
            throw Error(`REPLACE_TARGET_MISSING ${key.id}/${contribution.id} -> ${contribution.replace}`);
        const record: RecordEntry = { key: { id: key.id, version: key.version }, contribution: Object.freeze({ ...contribution }), retention: new Retention(), epoch: decl?.descriptor.epoch, owner: { uid: this.ctx.fiber.uid, name: this.ctx.fiber.name } };
        const close = this.ctx.resources.use(`slots.contribute(${key.id}/${contribution.id})`, async () => {
            if (map!.get(contribution.id) === record)
                map!.delete(contribution.id);
            await record.retention.retire();
        });
        map.set(contribution.id, record);
        return close;
    }
    inject<T>(key: SlotKey<T>, apply: (ctx: Context, descriptor: SlotDescriptor) => unknown): () => void | Promise<void> {
        this.version(key);
        const w: Watcher = { ctx: this.ctx, key: { id: key.id, version: key.version }, apply, stopped: false, transition: Promise.resolve() };
        return this.ctx.effect(() => { this.watchers.add(w); void this.refresh(w).catch(error => this.ctx.logger.error(error)); return async () => { w.stopped = true; this.watchers.delete(w); await this.refresh(w); }; }, `slots.inject(${key.id})`);
    }
    private visible(key: Key, decl: Declaration): RecordEntry[] {
        const candidates = [...(this.contributions.get(key.id)?.values() ?? [])].filter(item => item.epoch === decl.descriptor.epoch && !item.retention.retiring);
        const replacements = candidates.map(item => item.contribution.replace).filter((id): id is string => !!id);
        if (new Set(replacements).size !== replacements.length)
            throw Error(`REPLACE_CONFLICT ${key.id}: ${replacements.join(', ')}`);
        const replaced = new Set(candidates.map(item => item.contribution.replace).filter((id): id is string => !!id));
        const visible = candidates.filter(item => !replaced.has(item.contribution.id));
        return visible;
    }
    private selectItems<T>(key: Key, decl: Declaration, ids: readonly string[], visible: readonly RecordEntry[], releases: (() => Promise<void>)[]) {
        if (new Set(ids).size !== ids.length)
            throw Error(`STRATEGY_DUPLICATE_SELECTION ${decl.strategy}: ${ids.join(', ')}`);
        if (decl.required && !ids.length)
            throw Error(`SLOT_REQUIRED ${key.id}`);
        const selectedItems: RecordEntry[] = [];
        const items = ids.map(id => {
            const item = visible.find(item => item.contribution.id === id);
            if (!item)
                throw Error(`STRATEGY_UNKNOWN_CONTRIBUTION ${decl.strategy}/${id}`);
            releases.push(item.retention.hold());
            selectedItems.push(item);
            return { id, value: decl.validate(item.contribution.value) as T };
        });
        return { items, selectedItems };
    }
    private selectionLease<T>(key: Key, decl: Declaration, generation: string, items: readonly {
        id: string;
        value: T;
    }[], selectedItems: RecordEntry[], releases: readonly (() => Promise<void>)[]): SelectionLease<T> {
        const selected = { key: key.id, epoch: decl.descriptor.epoch, generation: generation, items: selectedItems };
        this.selections.add(selected);
        let released = false;
        return Object.freeze({ epoch: decl.descriptor.epoch, generation: generation, items: immutableItems(items), release: async () => {
                if (released)
                    return;
                released = true;
                this.selections.delete(selected);
                await Promise.all(releases.map(release => release()));
            } });
    }
    async acquire<T>(key: SlotKey<T>, options: {
        readonly generation: string;
        readonly signal?: AbortSignal;
    }): Promise<SelectionLease<T>> {
        options.signal?.throwIfAborted();
        this.version(key);
        const decl = this.declarations.get(key.id);
        if (!decl)
            throw Error(`SLOT_PENDING ${key.id}`);
        const strategy = await this.ctx.compositionStrategies.acquire(decl.strategy, options);
        const releases: (() => Promise<void>)[] = [strategy.release];
        try {
            options.signal?.throwIfAborted();
            if (this.declarations.get(key.id) !== decl)
                throw Error(`SLOT_RETIRING ${key.id}`);
            releases.push(decl.retention.hold());
            const visible = this.visible(key, decl);
            const ids = strategy.value.select(Object.freeze(visible.map(item => Object.freeze({ ...item.contribution, value: undefined }))));
            const { items, selectedItems } = this.selectItems<T>(key, decl, ids, visible, releases);
            return this.selectionLease(key, decl, options.generation, items, selectedItems, releases);
        }
        catch (error) {
            await Promise.all(releases.map(release => release()));
            throw error;
        }
    }
    /** Snapshot ownership only; explaining a slot never invokes author selection code. */
    inspect(id: string) {
        const declaration = this.declarations.get(id);
        const candidates = [...(this.contributions.get(id)?.values() ?? [])];
        const strategy = declaration ? this.ctx.compositionStrategies.describe().find(item => item.name === declaration.strategy) : undefined;
        const pending: string[] = [];
        if (!declaration)
            pending.push('declaration');
        else {
            if (!strategy)
                pending.push(`strategy:${declaration.strategy}`);
            if (declaration.required && !candidates.length)
                pending.push('contribution');
        }
        const item = (record: RecordEntry) => ({ id: record.contribution.id, owner: { ...record.owner }, epoch: record.epoch, retiring: record.retention.retiring });
        return { declaration: declaration?.descriptor, strategy, pending, contributions: candidates.map(item), selections: [...this.selections].filter(selection => selection.key === id).map(selection => ({ epoch: selection.epoch, generation: selection.generation, items: selection.items.map(item) })) };
    }
    describe(): readonly SlotDescriptor[] { return [...this.declarations.values()].map(record => ({ ...record.descriptor })); }
}
export default Slots;
