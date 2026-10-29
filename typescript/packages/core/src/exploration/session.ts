import type { Context } from '@society0/cordis';
import type { SelectionLease } from '@society0/host/slots';
import type { TaskScope } from '@society0/host/tasks';
import { posix, matchesGlob } from 'node:path';
import type { Invocation, Lease, Json } from '../contracts/common.js';
import type { DiscoveryScope, PageRequest } from '../contracts/data.js';
import type { ExplorationNode, ExplorationMount, WritableExplorationMount, ExplorationSession, ExplorationMatch, ExplorationMutation, ExplorationContent } from '../contracts/exploration.js';
import type { ActionExecutionContext } from '../contracts/domain.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import { after, identity, page, fail, equal } from '../information/page.js';
import { canonical, type MountRegistration, type ExplorationOptions, type FindRequest, type GrepRequest } from './registry.js';
import { MountedRoot } from './root.js';
import { NativeSearch } from './search.js';
import '../sqlite/index.js';
async function* jsonLines(rows: AsyncIterable<Json>, maxRowBytes: number) {
    for await (const row of rows) {
        const encoded = JSON.stringify(row);
        if (Buffer.byteLength(encoded) > maxRowBytes)
            return fail('EXPLORATION_ROW_BUDGET');
        const raw = Buffer.from(encoded + '\n');
        for (let offset = 0; offset < raw.length; offset += 65536)
            yield raw.subarray(offset, offset + 65536);
    }
}
class MatchPage {
    total = 0;
    private items: ExplorationMatch[] = [];
    private positions = new WeakMap<ExplorationMatch, number>();
    constructor(private request: GrepRequest, private last: unknown, private id: unknown, private revision: string) { }
    append(node: ExplorationNode, offset: number, text: string) {
        this.total++;
        if (this.last !== undefined && this.total <= Number(this.last))
            return;
        if (this.items.length > this.request.page.maxItems)
            return;
        const value = { path: node.path, objectId: node.objectId, versions: node.versions, rawOffset: String(offset), text };
        this.positions.set(value, this.total);
        this.items.push(value);
    }
    finish() { return page(this.items, String(this.total), this.revision, this.request.page, this.id, item => this.positions.get(item)); }
}
export class ExplorationView implements ExplorationSession {
    private closing = false;
    private closed?: Promise<void>;
    private active = new Set<Promise<unknown>>();
    private routes: readonly MountRegistration[];
    private root: MountedRoot;
    constructor(private ctx: Context, private selected: SelectionLease<MountRegistration>, readonly scope: DiscoveryScope, private invocation: Invocation, private tasks: TaskScope, private options: ExplorationOptions, private registry: unknown) { this.routes = selected.items.map(item => item.value).sort((a, b) => b.mount.prefix.length - a.mount.prefix.length); this.root = new MountedRoot(this.routes, registry); }
    private check() {
        if (this.closing)
            return fail('EXPLORATION_RELEASED');
        this.invocation.signal.throwIfAborted();
    }
    private async track<T>(body: () => Promise<T>): Promise<T> {
        this.check();
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
    private read<T>(body: (view: SqliteReadView) => Promise<T>) {
        return this.track(async () => {
            const view = await this.ctx.database.snapshot({ maxAgeMs: 30000 });
            try {
                return await body(view);
            }
            finally {
                await view.close();
            }
        });
    }
    private mount(path: string): ExplorationMount {
        canonical(path);
        if (path === '/')
            return this.root;
        const found = this.routes.find(({ mount }) => path === mount.prefix || path.startsWith(mount.prefix + '/'));
        if (!found)
            return fail('EXPLORATION_MOUNT_MISSING');
        return found.mount;
    }
    private async visible(path: string, view: SqliteReadView) {
        const mount = this.mount(path);
        if (!await mount.visible(path, this.scope, view))
            return fail('EXPLORATION_NOT_VISIBLE');
        return mount;
    }
    stat(path: string) { return this.read(async (view) => (await this.visible(path, view)).stat(path, this.scope, view)); }
    list(path: string, request: PageRequest) { return this.read(async (view) => (await this.visible(path, view)).list(path, this.scope, view, request)); }
    collect(path: string, query: Json, request: PageRequest) { return this.read(async (view) => (await this.visible(path, view)).collect(path, query, this.scope, view, request)); }
    open(expected:ExplorationNode){return this.read(async view=>{const mount=this.mount(expected.path);if(!await mount.visible(expected.path,this.scope,view))return fail('EXPLORATION_STALE');const current=await mount.stat(expected.path,this.scope,view);if(!equal(current,expected))return fail('EXPLORATION_STALE');return this.pin(await mount.open(expected,this.scope,view))})}
    private pin(content: Lease<ExplorationContent>): Lease<ExplorationContent> {
        const pin = Promise.withResolvers<void>();
        this.active.add(pin.promise);
        return { value: content.value, release: async () => {
                try {
                    await content.release();
                }
                finally {
                    pin.resolve();
                    this.active.delete(pin.promise);
                }
            } };
    }
    find(request: FindRequest) { return this.read(view => this.findInView(request, view)); }
    private async findInView(request: FindRequest, view: SqliteReadView) {
        const id = identity(this.scope, view, { registry: this.registry, root: request.root, glob: request.glob }), last = after(request.page.cursor, id);
        let total = 0;
        const positions = new WeakMap<ExplorationNode, number>(), items: ExplorationNode[] = [];
        for await (const node of this.walk(request.root, view)) {
            if (!this.matches(node, request.glob))
                continue;
            total++;
            if (last !== undefined && total <= Number(last))
                continue;
            if (items.length <= request.page.maxItems) {
                positions.set(node, total);
                items.push(node);
            }
        }
        return page(items, String(total), view.revision, request.page, id, node => positions.get(node));
    }
    private matches(node: ExplorationNode, glob: string) { return matchesGlob(node.path, glob) || matchesGlob(posix.basename(node.path), glob); }
    private async *walk(root: string, view: SqliteReadView, depth = 0): AsyncGenerator<ExplorationNode> {
        if (depth > 128)
            return fail('EXPLORATION_DEPTH');
        const provider = this.mount(root);
        if (!await provider.visible(root, this.scope, view))
            return;
        const node = await provider.stat(root, this.scope, view);
        yield node;
        if (node.kind !== 'directory')
            return;
        let cursor: string | undefined;
        do {
            const children = await provider.list(root, this.scope, view, { maxItems: 64, maxResponseBytes: 65536, cursor });
            for (const child of children.items)
                yield* this.walk(child.path, view, depth + 1);
            cursor = children.next;
        } while (cursor);
    }
    async grep(request: GrepRequest) {
        this.check();
        const task = this.tasks.run('exploration.grep', signal => this.grepFiles(request, signal), { signal: this.invocation.signal, bytes: this.options.maxSearchOutputBytes });
        this.active.add(task.settled);
        try {
            return await task;
        }
        finally {
            await task.settled;
            this.active.delete(task.settled);
        }
    }
    private async *files(root: string, revision: string) {
        let cursor: string | undefined;
        do {
            const found = await this.find({ root, glob: '**', page: { maxItems: 32, maxResponseBytes: 65536, cursor } });
            if (found.revision !== revision)
                return fail('EXPLORATION_STALE');
            for (const node of found.items)
                if (node.kind === 'file')
                    yield node;
            cursor = found.next;
        } while (cursor);
    }
    private fileBytes(node: ExplorationNode, scanned: number) {
        const size = Number(node.rawBytes);
        if (!Number.isSafeInteger(size) || size < 0 || scanned + size > this.options.maxSearchBytes)
            return fail('EXPLORATION_SEARCH_BUDGET');
        return size;
    }
    private async grepFiles(request: GrepRequest, signal: AbortSignal) {
        const base = await this.read(view => Promise.resolve({ revision: view.revision, id: identity(this.scope, view, { registry: this.registry, root: request.root, pattern: request.pattern, mode: request.mode }) }));
        const found = new MatchPage(request, after(request.page.cursor, base.id), base.id, base.revision);
        let scanned = 0;
        for await (const node of this.files(request.root, base.revision)) {
            const size = this.fileBytes(node, scanned);
            scanned += size;
            await this.searchFile(node, size, request, signal, found);
        }
        return found.finish();
    }
    private async searchFile(node: ExplorationNode, size: number, request: GrepRequest, signal: AbortSignal, found: MatchPage) {
        const handle = await this.open(node);
        try {
            const search = new NativeSearch(request.pattern, request.mode, this.options);
            await search.run(handle.value.read({ offset: '0', length: size, maxBytes: this.options.maxSearchBytes }, signal), signal, (offset, text) => found.append(node, offset, text));
        }
        finally {
            await handle.release();
        }
    }
    private writable(path: string) {
        const mount = this.mount(path) as WritableExplorationMount;
        if (!mount.writeFile || !mount.mutate)
            return fail('EXPLORATION_READ_ONLY');
        if (!['/workspace', '/results'].some(root => path.startsWith(root + '/')))
            return fail('EXPLORATION_READ_ONLY');
        return mount;
    }
    writeFile(path: string, bytes: AsyncIterable<Uint8Array>, expected: ExplorationNode | null, context: ActionExecutionContext) { return this.track(() => this.writable(path).writeFile(path, bytes, expected, this.scope, context)); }
    mutate(request: ExplorationMutation, context: ActionExecutionContext) {
        return this.track(() => {
            const path = request.kind === 'rename' ? request.from : request.path, mount = this.writable(path);
            if (request.kind === 'rename' && this.mount(request.to) !== mount)
                return fail('EXPLORATION_CROSS_MOUNT');
            return mount.mutate(request, this.scope, context);
        });
    }
    saveResult(name: string, rows: AsyncIterable<Json>, context: ActionExecutionContext, provenance?: Json) {
        if (posix.basename(name) !== name || !name)
            return fail('EXPLORATION_RESULT_NAME');
        const path = '/results/' + name;
        return this.track(() => this.writable(path).writeFile(path, jsonLines(rows, this.options.maxInlineFileBytes), null, this.scope, context, provenance));
    }
}
