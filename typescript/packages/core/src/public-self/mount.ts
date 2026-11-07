/** /self 只读实时挂载：每次操作读最新已提交状态，按 actor 隔离并缓存渲染。 */
import { isDeepStrictEqual } from 'node:util';
import { posix, matchesGlob } from 'node:path';
import { compareCodePoints } from '@society0/host/order';
import type { Context } from '@society0/cordis';
import type { Page, PageRequest, DiscoveryScope, StoredValue } from '../contracts/data.js';
import type { ExplorationContent, ExplorationMount, ExplorationNode } from '../contracts/exploration.js';
import type { SelfProvider } from '../contracts/public-self.js';
import type { SqliteReadView, SqliteSnapshot } from '../sqlite/contracts.js';
import { after, fail, identity, page } from '../information/page.js';
import { SelfCache } from './cache.js';
import '../sqlite/index.js';

export interface PublicSelfMountHost {
    readonly prefix: string;
    readonly ctx: Context;
    readonly providers: ReadonlyMap<string, SelfProvider>;
    readonly cache: SelfCache;
}
interface Rendered { readonly versions: readonly { owner: string; version: string }[]; readonly bytes: Uint8Array }
interface FileRecord { readonly node: ExplorationNode; readonly bytes: Uint8Array }

export class PublicSelfMount implements ExplorationMount {
    readonly id = 'self/files';
    readonly prefix: string;
    constructor(private readonly host: PublicSelfMountHost) { this.prefix = host.prefix; }
    /** /self 是实时视图：不使用调用方传入的会话快照，改为每次读最新已提交 revision。 */
    private async live<T>(body: (view: SqliteReadView) => Promise<T>): Promise<T> {
        const view = await this.host.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            return await body(view);
        }
        finally {
            await (view as SqliteSnapshot).close();
        }
    }
    private provider(path: string): SelfProvider | undefined {
        const provider = this.host.providers.get(path);
        if (!provider)
            return undefined;
        return path.startsWith(this.prefix + '/') ? provider : undefined;
    }
    private directories(): Set<string> {
        const directories = new Set<string>([this.prefix]);
        for (const path of this.host.providers.keys()) {
            if (!path.startsWith(this.prefix + '/'))
                continue;
            let parent = posix.dirname(path);
            while (parent.startsWith(this.prefix)) {
                directories.add(parent);
                if (parent === this.prefix)
                    break;
                parent = posix.dirname(parent);
            }
        }
        return directories;
    }
    private readmePath() { return this.prefix + '/README.md'; }
    private async providerVisible(provider: SelfProvider, actorId: string, view: SqliteReadView): Promise<boolean> {
        if (!provider.visible)
            return true;
        return provider.visible(actorId, view);
    }
    /** 目录仅当子树内至少有一个对该 actor 可见的提供者时可见；挂载根始终可见。 */
    private async visibleDirectoryIn(view: SqliteReadView, path: string, scope: DiscoveryScope): Promise<boolean> {
        if (path === this.prefix)
            return true;
        if (!this.directories().has(path))
            return false;
        for (const provider of this.host.providers.values()) {
            if (!provider.path.startsWith(path + '/'))
                continue;
            if (await this.providerVisible(provider, scope.actorId, view))
                return true;
        }
        return false;
    }
    private async visibleIn(view: SqliteReadView, path: string, scope: DiscoveryScope): Promise<boolean> {
        if (path !== this.prefix && !path.startsWith(this.prefix + '/'))
            return false;
        if (path === this.prefix)
            return true;
        if (this.directories().has(path))
            return this.visibleDirectoryIn(view, path, scope);
        if (path === this.readmePath())
            return true;
        const provider = this.provider(path);
        return provider ? this.providerVisible(provider, scope.actorId, view) : false;
    }
    private async rendered(provider: SelfProvider, actorId: string, view: SqliteReadView, scope: DiscoveryScope): Promise<Rendered> {
        const versions = (await provider.versions(actorId, view, scope)).map(item => ({ owner: String(item.owner), version: String(item.version) }));
        const key = SelfCache.key(provider.path, actorId, versions);
        const bytes = await this.host.cache.bytes(key, async () => {
            const chunks: Uint8Array[] = [];
            for await (const chunk of provider.render(actorId, view, new AbortController().signal, scope))
                chunks.push(chunk instanceof Uint8Array ? chunk : new Uint8Array(chunk as ArrayBufferLike));
            return Buffer.concat(chunks);
        });
        return { versions, bytes };
    }
    private fileRecord(path: string, rendered: Rendered, actorId: string, media: string): FileRecord {
        const versions = [...rendered.versions, { owner: 'actor', version: actorId }];
        const node: ExplorationNode = {
            path, objectId: 'self:file:' + path + ':' + versions.map(item => item.owner + '=' + item.version).join(','), kind: 'file',
            versions, rawBytes: String(rendered.bytes.byteLength), provenance: { media }
        };
        return { node, bytes: rendered.bytes };
    }
    private directoryNode(path: string, actorId: string): ExplorationNode {
        return { path, objectId: 'self:dir:' + path + ':' + actorId, kind: 'directory', versions: [{ owner: 'self', version: 'dir' }, { owner: 'actor', version: actorId }] };
    }
    private async readmeBytes(view: SqliteReadView, scope: DiscoveryScope): Promise<Uint8Array> {
        const paths = [...this.host.providers.keys()].filter(path => path.startsWith(this.prefix + '/')).sort(compareCodePoints);
        const lines = ['# ' + this.prefix, ''];
        for (const path of paths) {
            const provider = this.host.providers.get(path)!;
            if (!await this.providerVisible(provider, scope.actorId, view))
                continue;
            lines.push('- ' + path.slice(this.prefix.length + 1) + ' (' + provider.media + ')');
        }
        lines.push('');
        return Buffer.from(lines.join('\n'));
    }
    private readmeRecord(path: string, bytes: Uint8Array, actorId: string): FileRecord {
        const node: ExplorationNode = {
            path, objectId: 'self:readme:' + actorId, kind: 'file',
            versions: [{ owner: 'self', version: 'readme' }, { owner: 'actor', version: actorId }],
            rawBytes: String(bytes.byteLength), provenance: { media: 'text/markdown' }
        };
        return { node, bytes };
    }
    private async recordIn(view: SqliteReadView, path: string, scope: DiscoveryScope): Promise<FileRecord> {
        if (path === this.readmePath())
            return this.readmeRecord(path, await this.readmeBytes(view, scope), scope.actorId);
        const provider = this.provider(path);
        if (!provider || !await this.providerVisible(provider, scope.actorId, view))
            return fail('EXPLORATION_NOT_FOUND');
        return this.fileRecord(path, await this.rendered(provider, scope.actorId, view, scope), scope.actorId, provider.media);
    }
    private async statIn(view: SqliteReadView, path: string, scope: DiscoveryScope): Promise<ExplorationNode> {
        if (!await this.visibleIn(view, path, scope))
            return fail('EXPLORATION_NOT_VISIBLE');
        if (path === this.prefix || this.directories().has(path))
            return this.directoryNode(path, scope.actorId);
        const provider = this.provider(path);
        if (!provider)
            return fail('EXPLORATION_NOT_FOUND');
        return (await this.recordIn(view, path, scope)).node;
    }
    async visible(path: string, scope: DiscoveryScope, _view: SqliteReadView): Promise<boolean> {
        return this.live(view => this.visibleIn(view, path, scope));
    }
    async stat(path: string, scope: DiscoveryScope, _view: SqliteReadView): Promise<ExplorationNode> {
        return this.live(view => this.statIn(view, path, scope));
    }
    async list(path: string, scope: DiscoveryScope, _view: SqliteReadView, request: PageRequest): Promise<Page<ExplorationNode>> {
        return this.live(async view => {
            if (!await this.visibleIn(view, path, scope))
                return fail('EXPLORATION_NOT_VISIBLE');
            if (path !== this.prefix && !this.directories().has(path))
                return fail('EXPLORATION_NOT_DIRECTORY');
            const items: ExplorationNode[] = [];
            for (const directory of this.directories())
                if (directory !== path && posix.dirname(directory) === path && await this.visibleDirectoryIn(view, directory, scope))
                    items.push(this.directoryNode(directory, scope.actorId));
            if (path === this.prefix)
                items.push(this.readmeRecord(this.readmePath(), await this.readmeBytes(view, scope), scope.actorId).node);
            for (const provider of [...this.host.providers.values()].sort((a, b) => compareCodePoints(a.path, b.path))) {
                if (posix.dirname(provider.path) !== path)
                    continue;
                if (!await this.providerVisible(provider, scope.actorId, view))
                    continue;
                items.push((await this.recordIn(view, provider.path, scope)).node);
            }
            items.sort((a, b) => compareCodePoints(a.path, b.path));
            const id = identity(scope, view, { mount: this.id, path }), last = after(request.cursor, id);
            const remaining = last === undefined ? items : items.filter(node => compareCodePoints(node.path, String(last)) > 0);
            return page(remaining, String(items.length), view.revision, request, id, node => node.path);
        });
    }
    async find(root: string, glob: string, scope: DiscoveryScope, _view: SqliteReadView, request: PageRequest): Promise<Page<ExplorationNode>> {
        return this.live(async view => {
            if (root !== this.prefix && !root.startsWith(this.prefix + '/'))
                return fail('EXPLORATION_NOT_VISIBLE');
            if (!await this.visibleIn(view, root, scope))
                return fail('EXPLORATION_NOT_VISIBLE');
            const id = identity(scope, view, { mount: this.id, root, glob }), last = after(request.cursor, id);
            const items: ExplorationNode[] = [];
            const matched: ExplorationNode[] = [];
            for (const provider of [...this.host.providers.values()].sort((a, b) => compareCodePoints(a.path, b.path))) {
                if (!provider.path.startsWith(root + '/') || !this.matches(provider.path, glob))
                    continue;
                if (!await this.providerVisible(provider, scope.actorId, view))
                    continue;
                matched.push((await this.recordIn(view, provider.path, scope)).node);
            }
            const readme = this.readmePath();
            if (root === this.prefix && this.matches(readme, glob))
                matched.push(this.readmeRecord(readme, await this.readmeBytes(view, scope), scope.actorId).node);
            matched.sort((a, b) => compareCodePoints(a.path, b.path));
            for (const node of matched)
                if (last === undefined || compareCodePoints(node.path, String(last)) > 0)
                    items.push(node);
            return page(items, String(matched.length), view.revision, request, id, node => node.path);
        });
    }
    private matches(path: string, glob: string) { return matchesGlob(path, glob) || matchesGlob(posix.basename(path), glob); }
    async open(node: ExplorationNode, scope: DiscoveryScope, _view: SqliteReadView): Promise<{ value: ExplorationContent; release: () => Promise<void> }> {
        return this.live(async view => {
            if (!await this.visibleIn(view, node.path, scope))
                return fail('EXPLORATION_STALE');
            const current = await this.statIn(view, node.path, scope);
            if (!isDeepStrictEqual(current, node))
                return fail('EXPLORATION_STALE');
            const { bytes } = await this.recordIn(view, node.path, scope);
            let released = false;
            return {
                value: {
                    node: current,
                    read: async function* (range, signal) {
                        if (released)
                            fail('EXPLORATION_RELEASED');
                        signal.throwIfAborted();
                        const offset = Number(range.offset);
                        if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(range.length) || range.length < 0 || range.length > range.maxBytes || offset + range.length > bytes.byteLength)
                            fail('PAYLOAD_RANGE');
                        if (!range.length)
                            return;
                        yield bytes.subarray(offset, offset + range.length);
                    }
                },
                release: async () => { released = true; }
            };
        });
    }
    async collect(): Promise<Page<StoredValue>> { return fail('EXPLORATION_NOT_COLLECTION'); }
}
