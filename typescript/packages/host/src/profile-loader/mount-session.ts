import { Context, FiberState, getTraceable, type Fiber } from '@society0/cordis';
import type { PluginScope } from '../module-resolver/index.js';
import type { CompositionPlan, PlannedEntry } from './types.js';
import { entryContext, preparedPlugin } from './mount-plugin.js';
export interface MountedTree {
    readonly plan: CompositionPlan;
    readonly root: Fiber;
    readonly entries: ReadonlyMap<string, Fiber>;
    close(): Promise<void>;
}
export interface MountSession {
    readonly tree: MountedTree;
    mountScopes(scopes: readonly PluginScope[]): Promise<MountedTree>;
    disposeScopes(scopes: readonly PluginScope[]): Promise<void>;
    close(): Promise<void>;
}
class NativeMountSession implements MountSession {
    readonly tree: MountedTree;
    private readonly entries = new Map<string, Fiber>();
    private readonly labels = new Map<string, symbol>();
    private readonly nodes: ReadonlyMap<string, PlannedEntry>;
    private readonly retiredGroups = new Set<string>();
    private transition: Promise<unknown> = Promise.resolve();
    private closing?: Promise<void>;
    constructor(private plan: CompositionPlan, private parent: Context, private root: Fiber) {
        this.nodes = new Map(plan.flat.map(entry => [entry.id, entry]));
        this.tree = { plan, root, entries: this.entries, close: () => this.close() };
    }
    private enqueue<T>(operation: () => Promise<T>): Promise<T> {
        if (this.closing)
            return Promise.reject(Error('MOUNT_SESSION_CLOSED'));
        const work = this.transition.then(operation, operation);
        this.transition = work;
        return work;
    }
    private selected(scopes: ReadonlySet<PluginScope>): Set<string> {
        const ids = new Set<string>();
        for (const entry of this.plan.flat) {
            if (entry.disabled || !scopes.has(entry.scope))
                continue;
            let node: PlannedEntry | undefined = entry;
            while (node && !ids.has(node.id)) {
                ids.add(node.id);
                node = node.parentId ? this.nodes.get(node.parentId) : undefined;
            }
        }
        return ids;
    }
    private async attach(entry: PlannedEntry, owner: Context, scopes: ReadonlySet<PluginScope>, created: string[]): Promise<Fiber> {
        const existing = this.entries.get(entry.id);
        if (existing)
            return existing;
        if (this.retiredGroups.has(entry.id) && !scopes.has(entry.scope))
            throw Error(`MOUNT_PARENT_RELEASED ${entry.id}`);
        const ctx = entryContext(entry, owner, this.parent.root, this.labels);
        const fiber = await ctx.plugin(preparedPlugin(entry), entry.config);
        this.entries.set(entry.id, fiber);
        this.retiredGroups.delete(entry.id);
        created.push(entry.id);
        return fiber;
    }
    private async attachTree(nodes: readonly PlannedEntry[], owner: Context, selected: ReadonlySet<string>, scopes: ReadonlySet<PluginScope>, created: string[]): Promise<void> {
        for (const entry of nodes) {
            if (entry.disabled || !selected.has(entry.id))
                continue;
            const fiber = await this.attach(entry, owner, scopes, created);
            if (entry.group)
                await this.attachTree(entry.children, fiber.ctx, selected, scopes, created);
        }
    }
    private async active(): Promise<void> {
        await Promise.all([...this.entries.values()].map(fiber => fiber.await()));
        for (const [id, fiber] of this.entries)
            if (fiber.state !== FiberState.ACTIVE)
                throw Error(`ENTRY_PENDING ${id}: ${Object.keys(fiber.inject).join(', ')}`);
    }
    private hasAncestor(id: string, ids: ReadonlySet<string>): boolean {
        let parent = this.nodes.get(id)?.parentId;
        while (parent) {
            if (ids.has(parent))
                return true;
            parent = this.nodes.get(parent)?.parentId;
        }
        return false;
    }
    private async remove(ids: ReadonlySet<string>): Promise<void> {
        const roots = [...ids].filter(id => !this.hasAncestor(id, ids));
        await Promise.all(roots.map(async (id) => {
            const fiber = this.entries.get(id);
            if (fiber) {
                await fiber.dispose();
                await fiber.await();
            }
        }));
        for (const id of ids) {
            this.entries.delete(id);
            if (this.nodes.get(id)?.group)
                this.retiredGroups.add(id);
        }
    }
    mountScopes(scopes: readonly PluginScope[]): Promise<MountedTree> {
        const selectedScopes = new Set(scopes);
        return this.enqueue(async () => {
            const created: string[] = [];
            try {
                await this.attachTree(this.plan.entries, this.root.ctx, this.selected(selectedScopes), selectedScopes, created);
                await this.active();
                return this.tree;
            }
            catch (error) {
                try {
                    await this.remove(new Set(created));
                }
                catch (cleanup) {
                    throw new AggregateError([error, cleanup], 'MOUNT_PHASE_CLEANUP');
                }
                throw error;
            }
        });
    }
    disposeScopes(scopes: readonly PluginScope[]): Promise<void> {
        const selectedScopes = new Set(scopes);
        return this.enqueue(async () => {
            const selected = new Set([...this.entries.keys()].filter(id => selectedScopes.has(this.nodes.get(id)!.scope)));
            for (const id of this.entries.keys())
                if (!selected.has(id) && this.hasAncestor(id, selected))
                    throw Error(`MOUNT_SCOPE_HAS_LIVE_CHILD ${id}`);
            await this.remove(selected);
        });
    }
    close(): Promise<void> {
        if (this.closing)
            return this.closing;
        const close = async () => { await this.root.dispose(); await this.root.await(); this.entries.clear(); };
        return this.closing = this.transition.then(close, close);
    }
}
export async function createMountSession(plan: CompositionPlan, parent: Context): Promise<MountSession> {
    const caller = getTraceable(parent, parent);
    const root = await caller.plugin({ name: 'profile-tree', apply() { } });
    return new NativeMountSession(plan, caller, root);
}
export async function completeMount(session: MountSession): Promise<MountedTree> {
    try {
        return await session.mountScopes([...new Set(session.tree.plan.flat.map(entry => entry.scope))]);
    }
    catch (error) {
        await session.close();
        throw error;
    }
}
export async function mountPlan(plan: CompositionPlan, parent: Context): Promise<MountedTree> { return completeMount(await createMountSession(plan, parent)); }
