/** /public 只读挂载：as-of 读取、受众求交、etag 与缓存。 */
import { isDeepStrictEqual } from 'node:util';
import { posix, matchesGlob } from 'node:path';
import { compareCodePoints } from '@society0/host/order';
import type { Context } from '@society0/cordis';
import type { Page, PageRequest, DiscoveryScope, StoredValue } from '../contracts/data.js';
import type { ExplorationContent, ExplorationMatch, ExplorationMount, ExplorationNode } from '../contracts/exploration.js';
import type { Audience, PublicDocDecl, PublicFile, PublicNode, PublicTableDecl } from '../contracts/public.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import { after, identity, page, fail } from '../information/page.js';
import { AS_OF, parentOf, readChildren, readFile, readFiles, readNode, renderRows } from './store.js';
import { effective, intersects, type AudienceResolver } from './audience.js';
import type { RenderCache } from './cache.js';

export interface MomentRevisionReader {
    revision(momentId: string, reader: SqliteReadView): Promise<string | null>;
}
export interface PublicMountHost {
    readonly prefix: string;
    readonly ctx: Context;
    readonly cache: RenderCache;
    readonly audiences: AudienceResolver;
    readonly tables: ReadonlyMap<string, PublicTableDecl>;
    readonly docs: ReadonlyMap<string, PublicDocDecl>;
    readonly momentViews?: () => MomentRevisionReader | undefined;
}
interface Virtual { readonly kind: 'readme' | 'schema'; readonly dir: string }

export function etagOf(path: string, revision: string, audiences: readonly Audience[]): string {
    return path + '\u0000' + revision + '\u0000' + audiences.join('\u0000');
}
function text(bytes: Uint8Array) { return Buffer.from(bytes).toString('utf8'); }
function audienceOk(audience: Audience, agent: readonly Audience[]) { return audience === 'all' || agent.includes(audience); }

export class PublicMount implements ExplorationMount {
    readonly id = 'public/files';
    readonly prefix: string;
    constructor(private readonly host: PublicMountHost) { this.prefix = host.prefix; }
    private async revision(scope: DiscoveryScope, view: SqliteReadView): Promise<string> {
        const service = this.host.momentViews?.();
        if (service) {
            const value = await service.revision(scope.momentId, view);
            if (value !== null)
                return value;
        }
        return view.revision;
    }
    private directories(): Set<string> {
        const directories = new Set<string>([this.prefix]);
        for (const path of [...this.host.tables.keys(), ...this.host.docs.keys()]) {
            let parent = parentOf(path);
            while (parent.startsWith(this.prefix) && parent !== '/') {
                directories.add(parent);
                if (parent === this.prefix)
                    break;
                parent = parentOf(parent);
            }
        }
        return directories;
    }
    private virtual(path: string): Virtual | undefined {
        const dir = parentOf(path);
        if (posix.basename(path) === 'README.md' && this.directories().has(dir))
            return { kind: 'readme', dir };
        if (posix.basename(path) === 'schema.json' && this.host.tables.has(dir))
            return { kind: 'schema', dir };
        return undefined;
    }
    private async audiences(scope: DiscoveryScope, view: SqliteReadView) {
        return this.host.audiences.resolve(scope, view);
    }
    async visible(path: string, scope: DiscoveryScope, view: SqliteReadView): Promise<boolean> {
        if (path !== this.prefix && !path.startsWith(this.prefix + '/'))
            return false;
        const revision = await this.revision(scope, view), agent = await this.audiences(scope, view);
        const file = await readFile(view, path, revision);
        if (file)
            return intersects(file.audiences, agent);
        const node = await readNode(view, path, revision);
        if (node)
            return audienceOk(node.audience, agent);
        if (this.directories().has(path))
            return true;
        if (this.virtual(path))
            return true;
        return false;
    }
    async stat(path: string, scope: DiscoveryScope, view: SqliteReadView): Promise<ExplorationNode> {
        if (!await this.visible(path, scope, view))
            fail('EXPLORATION_NOT_VISIBLE');
        const revision = await this.revision(scope, view), agent = await this.audiences(scope, view);
        const file = await readFile(view, path, revision);
        if (file)
            return this.fileStat(file, scope, view, revision, agent);
        const node = await readNode(view, path, revision);
        if (node)
            return this.nodeOf(node, revision, agent);
        if (this.directories().has(path))
            return this.directoryNode(path, revision);
        const virtual = this.virtual(path);
        if (virtual)
            return this.virtualNode(virtual, revision, agent, view);
        return fail('EXPLORATION_NOT_FOUND');
    }
    private directoryNode(path: string, revision: string): ExplorationNode {
        const table = this.host.tables.get(path);
        return {
            path, objectId: 'public:dir:' + path, kind: 'directory',
            versions: [{ owner: 'public', version: revision }, { owner: 'moment', version: revision }],
            provenance: table ? { title: table.title, description: table.description } : undefined
        };
    }
    private fileNode(file: PublicFile, revision: string, agent: readonly Audience[], bytes?: number): ExplorationNode {
        const audiences = effective(file.audiences, agent);
        if (!audiences.length)
            return fail('EXPLORATION_NOT_VISIBLE');
        const etag = etagOf(file.file, file.revision, audiences);
        return {
            path: file.file, objectId: 'public:file:' + etag, kind: 'file',
            versions: [{ owner: 'public', version: file.revision }, { owner: 'moment', version: revision }, { owner: 'audience', version: audiences.join(',') }],
            rawBytes: String(bytes ?? file.bytes), provenance: { etag, rows: file.rows, table: file.tablePath }
        };
    }
    private nodeOf(node: PublicNode, revision: string, agent: readonly Audience[]): ExplorationNode {
        if (node.kind === 'dir')
            return this.directoryNode(node.path, revision);
        const audiences = node.audience === 'all' ? ['all'] : [node.audience];
        const etag = etagOf(node.path, node.revision, audiences);
        return {
            path: node.path, objectId: 'public:doc:' + etag, kind: 'file',
            versions: [{ owner: 'public', version: node.revision }, { owner: 'moment', version: revision }, { owner: 'audience', version: audiences.join(',') }],
            ...(node.bytes === undefined ? {} : { rawBytes: String(node.bytes) }), provenance: { etag, media: node.media, meta: node.meta }
        };
    }
    private async fileStat(file: PublicFile, scope: DiscoveryScope, view: SqliteReadView, revision: string, agent: readonly Audience[]): Promise<ExplorationNode> {
        const audiences = effective(file.audiences, agent);
        if (!audiences.length)
            return fail('EXPLORATION_NOT_VISIBLE');
        const etag = etagOf(file.file, file.revision, audiences);
        let bytes = file.bytes;
        if (!file.audiences.every(audience => agent.includes(audience)))
            bytes = (await this.host.cache.bytes(etag, () => renderRows(view, file.file, revision, audiences))).byteLength;
        return this.fileNode(file, revision, agent, bytes);
    }
    private async virtualBytes(virtual: Virtual, scope: DiscoveryScope, view: SqliteReadView, revision: string, agent: readonly Audience[]): Promise<Uint8Array> {
        const dir = virtual.dir;
        if (virtual.kind === 'schema') {
            const decl = this.host.tables.get(dir)!;
            return Buffer.from(JSON.stringify({ path: decl.path, title: decl.title, description: decl.description, schema: decl.schema, key: decl.key, sort: decl.sort, shard: decl.shard, ...(decl.decimals ? { decimals: decl.decimals } : {}), ...(decl.notify ? { notify: decl.notify } : {}) }, null, 2) + '\n');
        }
        const files = (await readFiles(view, dir, revision, { maxRows: 10000, maxBytes: 4 * 1024 * 1024 })).filter(file => intersects(file.audiences, agent));
        const children = (await readChildren(view, dir, revision, { maxRows: 10000, maxBytes: 4 * 1024 * 1024 })).filter(node => audienceOk(node.audience, agent));
        const table = this.host.tables.get(dir);
        const lines = ['# ' + (table?.title ?? (posix.basename(dir) || dir)), ''];
        if (table?.description)
            lines.push(table.description, '');
        if (files.length) {
            lines.push('| 文件 | 行数 | 字节 |', '|---|---|---|');
            for (const file of files)
                lines.push(`| ${posix.basename(file.file)} | ${file.rows} | ${file.bytes} |`);
            lines.push('');
        }
        if (children.length) {
            lines.push('## 条目', '');
            for (const child of children)
                lines.push('- ' + posix.basename(child.path));
            lines.push('');
        }
        return Buffer.from(lines.join('\n'));
    }
    private async virtualNode(virtual: Virtual, revision: string, agent: readonly Audience[], view: SqliteReadView, scope?: DiscoveryScope): Promise<ExplorationNode> {
        const path = virtual.dir + '/' + (virtual.kind === 'schema' ? 'schema.json' : 'README.md');
        const bytes = await this.virtualBytes(virtual, { actorId: '', momentId: '', query: null }, view, revision, agent);
        const etag = etagOf(path, revision, agent);
        return {
            path, objectId: 'public:virtual:' + etag, kind: 'file',
            versions: [{ owner: 'public', version: revision }, { owner: 'moment', version: revision }, { owner: 'audience', version: agent.join(',') }],
            rawBytes: String(bytes.byteLength), provenance: { etag, media: virtual.kind === 'schema' ? 'application/schema+json' : 'text/markdown' }
        };
    }
    async list(path: string, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest): Promise<Page<ExplorationNode>> {
        if (!await this.visible(path, scope, view))
            fail('EXPLORATION_NOT_VISIBLE');
        if (!this.directories().has(path) && (await readNode(view, path, await this.revision(scope, view)))?.kind !== 'dir')
            fail('EXPLORATION_NOT_DIRECTORY');
        const revision = await this.revision(scope, view), agent = await this.audiences(scope, view);
        const items = new Map<string, ExplorationNode>();
        for (const dir of this.directories())
            if (dir !== path && parentOf(dir) === path)
                items.set(dir, this.directoryNode(dir, revision));
        for (const node of await readChildren(view, path, revision, { maxRows: request.maxItems + 1024, maxBytes: 4 * 1024 * 1024 })) {
            if (node.kind === 'table' || node.kind === 'derived')
                continue;
            if (node.kind !== 'dir' && !audienceOk(node.audience, agent))
                continue;
            items.set(node.path, await this.nodeOf(node, revision, agent));
        }
        for (const file of await readFiles(view, path, revision, { maxRows: request.maxItems + 1024, maxBytes: 4 * 1024 * 1024 })) {
            if (!intersects(file.audiences, agent))
                continue;
            items.set(file.file, await this.fileStat(file, scope, view, revision, agent));
        }
        const readme = { kind: 'readme', dir: path } as const;
        items.set(path + '/README.md', await this.virtualNode(readme, revision, agent, view));
        const table = this.host.tables.get(path);
        if (table) {
            const schema = { kind: 'schema', dir: path } as const;
            items.set(path + '/schema.json', await this.virtualNode(schema, revision, agent, view));
        }
        const id = identity(scope, view, { mount: this.id, path }), last = after(request.cursor, id);
        const nodes = [...items.values()].sort((a, b) => compareCodePoints(a.path, b.path));
        const remaining = last === undefined ? nodes : nodes.filter(node => compareCodePoints(node.path, String(last)) > 0);
        return page(remaining, String(nodes.length), view.revision, request, id, node => node.path);
    }
    async find(root: string, glob: string, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest): Promise<Page<ExplorationNode>> {
        if (root !== this.prefix && !root.startsWith(this.prefix + '/'))
            fail('EXPLORATION_NOT_VISIBLE');
        const revision = await this.revision(scope, view), agent = await this.audiences(scope, view);
        const lower = root + '/', upper = root + '0', at = this.revisionValue(revision);
        const id = identity(scope, view, { mount: this.id, root, glob }), last = after(request.cursor, id);
        const simple = this.simpleGlob(glob);
        if (simple) {
            const pattern = this.globLike(glob);
            const audience = agent.map(audience => 'audiences LIKE ?');
            const fileFilter = `file>=? AND file<? AND ${AS_OF} AND (${audience.map(() => 'audiences LIKE ?').join(' OR ')}) AND file LIKE ? ESCAPE '\\'`;
            const nodeFilter = `path>=? AND path<? AND ${AS_OF} AND (${agent.map(() => 'audience=?').join(' OR ')}) AND kind<>'dir' AND path LIKE ? ESCAPE '\\'`;
            const [fileTotal] = await view.query({ sql: `SELECT count(*) AS n FROM public_files WHERE ${fileFilter}`, parameters: [lower, upper, at, at, ...agent.map(a => `%"${a}"%`), pattern] }, { maxRows: 1, maxBytes: 65536 });
            const [nodeTotal] = await view.query({ sql: `SELECT count(*) AS n FROM public_nodes WHERE ${nodeFilter}`, parameters: [lower, upper, at, at, ...agent, pattern] }, { maxRows: 1, maxBytes: 65536 });
            const total = Number(fileTotal?.n ?? 0) + Number(nodeTotal?.n ?? 0);
            const bound = last === undefined ? '' : ' AND file>?';
            const nodeBound = last === undefined ? '' : ' AND path>?';
            const files = await view.query({ sql: `SELECT file,rev_from,audiences FROM public_files WHERE ${fileFilter}${bound} ORDER BY file LIMIT ?`, parameters: [lower, upper, at, at, ...agent.map(a => `%"${a}"%`), pattern, ...(last === undefined ? [] : [String(last)]), request.maxItems + 1] }, { maxRows: request.maxItems + 1, maxBytes: 4 * 1024 * 1024 });
            const nodes = await view.query({ sql: `SELECT path,kind,audience,rev_from FROM public_nodes WHERE ${nodeFilter}${nodeBound} ORDER BY path LIMIT ?`, parameters: [lower, upper, at, at, ...agent, pattern, ...(last === undefined ? [] : [String(last)]), request.maxItems + 1] }, { maxRows: request.maxItems + 1, maxBytes: 4 * 1024 * 1024 });
            const matched: ExplorationNode[] = [];
            for (const row of files) {
                const path = String(row.file), audiences = JSON.parse(String(row.audiences)) as Audience[];
                if (!intersects(audiences, agent) || !this.match(path, glob))
                    continue;
                matched.push(this.foundFile(path, String(row.rev_from), revision, effective(audiences, agent)));
            }
            for (const row of nodes) {
                const path = String(row.path);
                if (!audienceOk(String(row.audience), agent) || !this.match(path, glob))
                    continue;
                matched.push({ path, objectId: 'public:doc:' + path, kind: 'file', versions: [{ owner: 'public', version: String(row.rev_from) }, { owner: 'moment', version: revision }] });
            }
            matched.sort((a, b) => compareCodePoints(a.path, b.path));
            return page(matched, String(total), view.revision, request, id, node => node.path);
        }
        const files = await view.query({
            sql: `SELECT file,parent,rev_from,audiences FROM public_files WHERE file>=? AND file<? AND ${AS_OF} ORDER BY file LIMIT ?`,
            parameters: [lower, upper, at, at, 100000]
        }, { maxRows: 100000, maxBytes: 4 * 1024 * 1024 });
        const nodes = await view.query({
            sql: `SELECT path,kind,audience,rev_from FROM public_nodes WHERE path>=? AND path<? AND ${AS_OF} ORDER BY path LIMIT ?`,
            parameters: [lower, upper, at, at, 100000]
        }, { maxRows: 100000, maxBytes: 4 * 1024 * 1024 });
        const matched: ExplorationNode[] = [];
        for (const row of files) {
            const path = String(row.file), audiences = JSON.parse(String(row.audiences)) as Audience[];
            if (!intersects(audiences, agent) || !this.match(path, glob))
                continue;
            matched.push(this.foundFile(path, String(row.rev_from), revision, effective(audiences, agent)));
        }
        for (const row of nodes) {
            const path = String(row.path), kind = String(row.kind);
            if (kind === 'dir' || !audienceOk(String(row.audience), agent) || !this.match(path, glob))
                continue;
            matched.push({ path, objectId: 'public:doc:' + path, kind: 'file', versions: [{ owner: 'public', version: String(row.rev_from) }, { owner: 'moment', version: revision }] });
        }
        matched.sort((a, b) => compareCodePoints(a.path, b.path));
        const remaining = last === undefined ? matched : matched.filter(node => compareCodePoints(node.path, String(last)) > 0);
        return page(remaining, String(matched.length), view.revision, request, id, node => node.path);
    }
    private foundFile(path: string, revision: string, moment: string, audiences: readonly Audience[]): ExplorationNode {
        const etag = etagOf(path, revision, audiences);
        return {
            path, objectId: 'public:file:' + etag, kind: 'file',
            versions: [{ owner: 'public', version: revision }, { owner: 'moment', version: moment }, { owner: 'audience', version: audiences.join(',') }],
            provenance: { etag }
        };
    }
    private simpleGlob(glob: string) { return !glob.includes('/') && !glob.includes('?') && !glob.includes('[') && !glob.includes('**'); }
    private globLike(glob: string) { return glob.replace(/[\\%_]/g, '\\$&').replace(/\*/g, '%'); }
    private revisionValue(revision: string) { return BigInt(revision); }
    private match(path: string, glob: string) { return matchesGlob(path, glob) || matchesGlob(posix.basename(path), glob); }
    async open(node: ExplorationNode, scope: DiscoveryScope, view: SqliteReadView): Promise<{ value: ExplorationContent; release: () => Promise<void> }> {
        const current = await this.stat(node.path, scope, view);
        if (current.objectId !== node.objectId || !isDeepStrictEqual(current.versions, node.versions))
            fail('EXPLORATION_STALE');
        const revision = await this.revision(scope, view), agent = await this.audiences(scope, view);
        const file = await readFile(view, node.path, revision);
        if (file) {
            const audiences = effective(file.audiences, agent);
            const etag = etagOf(file.file, file.revision, audiences);
            const handle = await this.host.cache.open(etag, () => renderRows(view, file.file, revision, audiences));
            const size = (await handle.stat()).size;
            let released = false;
            return {
                value: {
                    node: current,
                    read: async function* (range, signal) {
                        if (released)
                            fail('EXPLORATION_RELEASED');
                        signal.throwIfAborted();
                        const offset = Number(range.offset);
                        if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(range.length) || range.length < 0 || range.length > range.maxBytes || offset + range.length > size)
                            fail('PAYLOAD_RANGE');
                        if (!range.length)
                            return;
                        const buffer = Buffer.allocUnsafe(range.length);
                        let read = 0;
                        while (read < range.length) {
                            const result = await handle.read(buffer, read, range.length - read, offset + read);
                            if (!result.bytesRead)
                                break;
                            read += result.bytesRead;
                        }
                        yield buffer.subarray(0, read);
                    }
                },
                release: async () => {
                    if (released)
                        return;
                    released = true;
                    await handle.close();
                }
            };
        }
        const record = await readNode(view, node.path, revision);
        const virtual = this.virtual(node.path);
        const bytes = record?.body ?? (record?.artifact ? await this.artifact(record) : virtual ? await this.virtualBytes(virtual, scope, view, revision, agent) : undefined);
        if (!bytes)
            return fail('EXPLORATION_NOT_FOUND');
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
                    yield bytes.subarray(offset, offset + range.length);
                }
            },
            release: async () => { released = true; }
        };
    }
    private async artifact(node: PublicNode): Promise<Uint8Array> {
        const ref = await this.host.ctx.artifacts.inspect(node.artifact!);
        const chunks: Uint8Array[] = [];
        for await (const chunk of this.host.ctx.artifacts.read(ref))
            chunks.push(chunk);
        return Buffer.concat(chunks);
    }
    async collect(): Promise<Page<StoredValue>> { return fail('EXPLORATION_NOT_COLLECTION'); }
}
export type { ExplorationMatch };
