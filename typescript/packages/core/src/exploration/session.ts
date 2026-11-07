import type { Context } from '@society0/cordis';
import type { SelectionLease } from '@society0/host/slots';
import type { TaskScope } from '@society0/host/tasks';
import { posix, matchesGlob } from 'node:path';
import type { Invocation, Lease, Json } from '../contracts/common.js';
import type { DiscoveryScope, PageRequest } from '../contracts/data.js';
import { explorationBackend, type ExplorationNode, type ExplorationMount, type WritableExplorationMount, type ExplorationSession, type ExplorationMatch, type ExplorationMutation, type ExplorationContent } from '../contracts/exploration.js';
import type { ActionExecutionContext } from '../contracts/domain.js';
import type { SqliteReadView, SqliteSnapshot } from '../sqlite/contracts.js';
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
const MATCH_LINE_BYTES = 512;
function clipLine(text: string, max = MATCH_LINE_BYTES) {
    const bytes = Buffer.from(text);
    if (bytes.length <= max) return text;
    let end = max;
    while (end > 0 && (bytes[end]! & 0xc0) === 0x80) end--;
    return bytes.subarray(0, end).toString('utf8') + '…';
}
class MatchPage {
    total = 0;
    private items: ExplorationMatch[] = [];
    private positions = new WeakMap<ExplorationMatch, number>();
    constructor(private request: GrepRequest, private last: unknown, private id: unknown, private revision: string) { }
    append(node: ExplorationNode, offset: number, text: string, line: number, column: number, lineText: string) {
        this.admit({ path: node.path, objectId: node.objectId, versions: node.versions, rawOffset: String(offset), text, lineText, line, column });
    }
    appendMatch(match: ExplorationMatch) { this.admit(match); }
    private admit(match: ExplorationMatch) {
        this.total++;
        if (this.last !== undefined && this.total <= Number(this.last))
            return;
        if (this.items.length > this.request.page.maxItems)
            return;
        const value = { ...match, lineText: clipLine(match.lineText) };
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
        this.activity++;
        const work = body();
        this.active.add(work);
        try {
            return await work;
        }
        finally {
            this.active.delete(work);
            await this.idleRelease();
        }
    }
    release() { return this.closed ??= (async () => { this.closing = true; await Promise.allSettled([...this.active]); await this.releaseView(); await this.selected.release(); })(); }
    private leased?: SqliteSnapshot;
    private retired = new Set<SqliteSnapshot>();
    private opening?: Promise<SqliteSnapshot>;
    private scoped = 0;
    private users = 0;
    private dirty = false;
    private activity = 0;
    private leaseAge() { return this.ctx.database.maxSnapshotAgeMs; }
    /** 同一 revision 内复用视图；空闲或写入提交后立即释放，避免占用全局快照槽。 */
    private ensureView(): Promise<SqliteSnapshot> {
        this.opening ??= (async () => {
            const current = this.leased;
            if (current) {
                const revision = await this.ctx.database.readRevision();
                if (String(revision) === String(current.revision))
                    return current;
                if (this.users > 0) {
                    this.dirty = true;
                    return current;
                }
                this.leased = undefined;
                await current.close().catch(() => {});
            }
            const view = await this.ctx.database.snapshot({ maxAgeMs: this.leaseAge() });
            this.leased = view;
            return view;
        })();
        return this.opening.finally(() => { this.opening = undefined; });
    }
    private async releaseView() {
        const view = this.leased;
        this.leased = undefined;
        const retired = [...this.retired];
        this.retired.clear();
        if (view)
            await view.close().catch(() => {});
        for (const snapshot of retired)
            await snapshot.close().catch(() => {});
    }
    private async settleLease() {
        if (this.users > 0 || this.scoped > 0)
            return;
        const retired = [...this.retired];
        this.retired.clear();
        for (const snapshot of retired)
            await snapshot.close().catch(() => {});
        if (this.dirty) {
            this.dirty = false;
            await this.releaseView();
        }
    }
    private async idleRelease() {
        if (this.active.size > 0 || this.scoped > 0 || this.users > 0 || this.dirty)
            return;
        const token = this.activity;
        await Promise.resolve();
        if (token === this.activity && this.active.size === 0 && this.scoped === 0 && this.users === 0 && !this.dirty)
            await this.releaseView();
    }
    private read<T>(body: (view: SqliteReadView) => Promise<T>) {
        return this.track(async () => {
            let view = this.scoped > 0 && this.leased ? this.leased : await this.ensureView();
            this.users++;
            try {
                return await body(view);
            }
            catch (error) {
                if ((error as { code?: string })?.code !== 'SQLITE_SNAPSHOT_EXPIRED')
                    throw error;
                if (this.users > 1)
                    throw error;
                if (this.leased === view)
                    await this.releaseView();
                view = await this.ensureView();
                return body(view);
            }
            finally {
                this.users--;
                await this.settleLease();
            }
        });
    }
    withView<T>(body: (session: ExplorationSession) => Promise<T>): Promise<T> {
        return this.track(async () => {
            if (this.scoped === 0)
                await this.ensureView();
            this.scoped++;
            this.users++;
            try {
                return await body(this);
            }
            finally {
                this.scoped--;
                this.users--;
                await this.settleLease();
            }
        });
    }
    /** 本会话自己的写提交后换一份快照，保证命令内的 read-your-writes；旧视图等并发读取结束后再关。 */
    private async advanceLease() {
        const previous = this.leased;
        if (!previous)
            return;
        if (this.users === 0) {
            this.leased = undefined;
            await previous.close().catch(() => {});
            return;
        }
        this.retired.add(previous);
        this.dirty = false;
        this.leased = await this.ctx.database.snapshot({ maxAgeMs: this.leaseAge() });
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
                    await this.idleRelease();
                }
            } };
    }
    find(request: FindRequest) { return this.read(view => this.findInView(request, view)); }
    private async findInView(request: FindRequest, view: SqliteReadView) {
        const direct = this.mount(request.root);
        if (direct.find)
            return direct.find(request.root, request.glob, this.scope, view, request.page);
        const id = identity(this.scope, view, { registry: this.registry, root: request.root, glob: request.glob }), last = after(request.page.cursor, id);
        let total = 0, seen = last === undefined;
        const items: ExplorationNode[] = [];
        for await (const node of this.walk(request.root, view)) {
            if (!this.matches(node, request.glob))
                continue;
            total++;
            // 游标记录上一次返回的最后一个路径；walk 是深度优先，按“走到该路径之后”继续，
            // 不能用字典序比较（'/x/a.txt' 与 '/x/a/z' 的字典序和遍历序不同）。
            if (!seen) {
                if (node.path === String(last))
                    seen = true;
                continue;
            }
            if (items.length <= request.page.maxItems)
                items.push(node);
        }
        return page(items, String(total), view.revision, request.page, id, node => node.path);
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
        const task = this.tasks.run('exploration.grep', signal => this.withView(() => this.read(view => this.grepInView(request, view, signal))), { signal: this.invocation.signal, bytes: this.options.maxSearchOutputBytes });
        this.active.add(task.settled);
        try {
            return await task;
        }
        finally {
            await task.settled;
            this.active.delete(task.settled);
            await this.idleRelease();
        }
    }
    private async grepInView(request: GrepRequest, view: SqliteReadView, signal: AbortSignal) {
        const id = identity(this.scope, view, { registry: this.registry, root: request.root, pattern: request.pattern, mode: request.mode });
        const found = new MatchPage(request, after(request.page.cursor, id), id, view.revision);
        const provider = this.mount(request.root);
        if (provider.search) {
            await provider.search({ root: request.root, pattern: request.pattern, mode: request.mode, ...(request.ignoreCase ? { ignoreCase: true } : {}) }, this.scope, view, signal, match => { found.appendMatch(match); return true; });
            return found.finish();
        }
        let scanned = 0;
        for await (const node of this.files(request.root, view)) {
            const size = this.fileBytes(node, scanned);
            scanned += size;
            const handle = this.pin(await this.mount(node.path).open(node, this.scope, view));
            try {
                const search = new NativeSearch(request.pattern, request.mode, this.options, request.ignoreCase);
                await search.run(handle.value.read({ offset: '0', length: size, maxBytes: this.options.maxSearchBytes }, signal), signal, (offset, text, line, column, lineText) => found.append(node, offset, text, line, column, lineText));
            }
            finally {
                await handle.release();
            }
        }
        return found.finish();
    }
    private async *files(root: string, view: SqliteReadView) {
        for await (const node of this.walk(root, view))
            if (node.kind === 'file')
                yield node;
    }
    private fileBytes(node: ExplorationNode, scanned: number) {
        const size = Number(node.rawBytes);
        if (!Number.isSafeInteger(size) || size < 0 || scanned + size > this.options.maxSearchBytes)
            return fail('EXPLORATION_SEARCH_BUDGET');
        return size;
    }
    private writable(path: string) {
        const mount = this.mount(path) as WritableExplorationMount;
        if (!mount.writeFile || !mount.mutate)
            return fail('EXPLORATION_READ_ONLY');
        if (!['/workspace', '/results'].some(root => path.startsWith(root + '/')))
            return fail('EXPLORATION_READ_ONLY');
        return mount;
    }
    writeFile(path: string, bytes: AsyncIterable<Uint8Array>, expected: ExplorationNode | null, context: ActionExecutionContext) {
        return this.track(async () => {
            const node = await this.writable(path).writeFile(path, bytes, expected, this.scope, context);
            await this.advanceLease();
            return node;
        });
    }
    mutate(request: ExplorationMutation, context: ActionExecutionContext) {
        return this.track(async () => {
            const path = request.kind === 'rename' ? request.from : request.path, mount = this.writable(path);
            if (request.kind === 'rename' && this.mount(request.to) !== mount) {
                const backend = (mount as any)[explorationBackend], target = (this.mount(request.to) as any)[explorationBackend];
                if (!backend || backend !== target)
                    return fail('EXPLORATION_CROSS_MOUNT');
            }
            await mount.mutate(request, this.scope, context);
            await this.advanceLease();
        });
    }
    saveResult(name: string, rows: AsyncIterable<Json>, context: ActionExecutionContext, provenance?: Json) {
        if (posix.basename(name) !== name || !name)
            return fail('EXPLORATION_RESULT_NAME');
        const path = '/results/' + name;
        const source = provenance !== null && typeof provenance === 'object' && !Array.isArray(provenance)
            ? { kind: 'save-result', ...(provenance as Record<string, Json>) }
            : { kind: 'save-result' };
        return this.track(() => this.writable(path).writeFile(path, jsonLines(rows, this.options.maxInlineFileBytes), null, this.scope, context, source));
    }
}
