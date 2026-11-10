/** /public 只读挂载：as-of 读取、受众求交、etag 与缓存。 */
import { isDeepStrictEqual } from 'node:util';
import { posix, matchesGlob } from 'node:path';
import { readFile as readDisk } from 'node:fs/promises';
import { compareCodePoints } from '@society0/host/order';
import type { Context } from '@society0/cordis';
import type { Page, PageRequest, DiscoveryScope, StoredValue } from '../contracts/data.js';
import type { ExplorationContent, ExplorationMatch, ExplorationMount, ExplorationNode } from '../contracts/exploration.js';
import type { Audience, PublicDocDecl, PublicFile, PublicNode, PublicTableDecl } from '../contracts/public.js';
import type { SqliteReadView, SqliteRow } from '../sqlite/contracts.js';
import { after, identity, page, fail } from '../information/page.js';
import { AS_OF, parentOf, readChildren, readFile, readFiles, readNode, renderRows } from './store.js';
import { effective, intersects, type AudienceResolver } from './audience.js';
import { NativeSearch, SEARCH_BATCH } from '../exploration/search.js';
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
    /** 目录路径对应的唯一声明；同一目录下有多张文件表时为空。 */
    readonly tableForDirectory: (path: string) => PublicTableDecl | undefined;
    readonly docs: ReadonlyMap<string, PublicDocDecl>;
    readonly momentViews?: () => MomentRevisionReader | undefined;
    readonly searchIndex: 'trigram' | 'none';
    readonly searchOptions: () => { maxInlineFileBytes: number; maxSearchBytes: number; maxSearchOutputBytes: number; maxSearchMs: number };
    readonly limits: { readonly maxQueryBytes: number; readonly maxQueryRows: number };
    readonly stats: { searchStale: number; fallbackScans: number };
}
interface Virtual { readonly kind: 'readme' | 'schema'; readonly dir: string }

export function etagOf(path: string, revision: string, audiences: readonly Audience[]): string {
    return path + '\u0000' + revision + '\u0000' + audiences.join('\u0000');
}
function text(bytes: Uint8Array) { return Buffer.from(bytes).toString('utf8'); }
const ALL_AUDIENCES: readonly Audience[] = ['all'];
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
    private render(file: PublicFile, revision: string, audiences: readonly Audience[], view: SqliteReadView) {
        return renderRows(view, file.file, revision, audiences, this.host.limits, file.rows, file.bytes);
    }
    private readonly nodeSizes = new Map<string, number>();
    private nodeBytes(node: ExplorationNode): number {
        const known = this.nodeSizes.get(node.objectId);
        if (known !== undefined)
            return known;
        const bytes = Buffer.byteLength(JSON.stringify(node), 'utf8');
        if (this.nodeSizes.size >= this.host.limits.maxQueryRows * 2)
            this.nodeSizes.clear();
        this.nodeSizes.set(node.objectId, bytes);
        return bytes;
    }
    private get batchRows(): number { return this.host.limits.maxQueryRows; }
    private get batchBytes(): number { return this.host.limits.maxQueryBytes; }
    /** 单页先按每行 256 字节估算；实际行更宽时按预算错误折半重试。 */
    private async boundedRows(run: (size: number) => Promise<readonly SqliteRow[]>, size: number): Promise<{ rows: readonly SqliteRow[]; size: number }> {
        let current = Math.max(1, Math.min(size, this.batchRows, Math.floor(this.batchBytes / 256)));
        for (;;) {
            try {
                return { rows: await run(current), size: current };
            }
            catch (error) {
                const code = (error as { code?: string } | null)?.code ?? '';
                if (current === 1 || (!/SQLITE_(RESULT_LIMIT|QUERY_BUDGET|RETURN_BUDGET)/.test(String(error)) && !/SQLITE_(RESULT_LIMIT|QUERY_BUDGET|RETURN_BUDGET)/.test(code)))
                    throw error;
                current = Math.max(1, Math.floor(current / 2));
            }
        }
    }
    /** 目录可能超过单次查询上限：按 file/path keyset 分批取完，保持完整列表语义。 */
    private async allDirectoryFiles(view: SqliteReadView, parent: string, revision: string): Promise<readonly PublicFile[]> {
        const files: PublicFile[] = [];
        let after: string | undefined;
        do {
            const batch = await readFiles(view, parent, revision, { maxRows: this.batchRows, maxBytes: this.batchBytes, ...(after === undefined ? {} : { after }) });
            files.push(...batch);
            after = batch.length === this.batchRows ? batch[batch.length - 1]!.file : undefined;
        } while (after !== undefined);
        return files;
    }
    private async allDirectoryChildren(view: SqliteReadView, parent: string, revision: string, kinds?: readonly string[]): Promise<readonly PublicNode[]> {
        const nodes: PublicNode[] = [];
        let after: string | undefined;
        do {
            const batch = await readChildren(view, parent, revision, { maxRows: this.batchRows, maxBytes: this.batchBytes, ...(kinds === undefined ? {} : { kinds }), ...(after === undefined ? {} : { after }) });
            nodes.push(...batch);
            after = batch.length === this.batchRows ? batch[batch.length - 1]!.path : undefined;
        } while (after !== undefined);
        return nodes;
    }
    /** 按 keyset 分批扫描文件区间；每批都在数据库查询预算之内。 */
    private async scanFiles(view: SqliteReadView, lower: string, upper: string, at: bigint, limit: number, select: string): Promise<SqliteRow[]> {
        const rows: SqliteRow[] = [];
        let cursor: string | undefined;
        while (rows.length < limit) {
            const size = Math.min(limit - rows.length, this.batchRows);
            const bound = cursor === undefined ? '' : ' AND file>?';
            const batch = await this.boundedRows(page => view.query({
                sql: `SELECT ${select} FROM public_files WHERE file>=? AND file<? AND ${AS_OF}${bound} ORDER BY file LIMIT ?`,
                parameters: [lower, upper, at, at, ...(cursor === undefined ? [] : [cursor]), page]
            }, { maxRows: page, maxBytes: this.batchBytes }), size);
            rows.push(...batch.rows);
            if (batch.rows.length < batch.size)
                break;
            cursor = String(batch.rows[batch.rows.length - 1]!.file);
        }
        return rows;
    }
    private async scanNodes(view: SqliteReadView, lower: string, upper: string, at: bigint, select: string): Promise<SqliteRow[]> {
        const rows: SqliteRow[] = [];
        let cursor: string | undefined;
        while (true) {
            const bound = cursor === undefined ? '' : ' AND path>?';
            const batch = await this.boundedRows(size => view.query({
                sql: `SELECT ${select} FROM public_nodes WHERE path>=? AND path<? AND ${AS_OF}${bound} ORDER BY path LIMIT ?`,
                parameters: [lower, upper, at, at, ...(cursor === undefined ? [] : [cursor]), size]
            }, { maxRows: size, maxBytes: this.batchBytes }), this.batchRows);
            rows.push(...batch.rows);
            if (batch.rows.length < batch.size)
                break;
            cursor = String(batch.rows[batch.rows.length - 1]!.path);
        }
        return rows;
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
        if (posix.basename(path) === 'schema.json' && this.host.tableForDirectory(dir))
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
        const table = this.host.tableForDirectory(path);
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
            bytes = (await this.host.cache.bytes(etag, () => this.render(file, revision, audiences, view))).byteLength;
        return this.fileNode(file, revision, agent, bytes);
    }
    private async virtualBytes(virtual: Virtual, scope: DiscoveryScope, view: SqliteReadView, revision: string, agent: readonly Audience[]): Promise<Uint8Array> {
        const dir = virtual.dir;
        if (virtual.kind === 'schema') {
            const decl = this.host.tableForDirectory(dir)!;
            return Buffer.from(JSON.stringify({ path: decl.path, title: decl.title, description: decl.description, schema: decl.schema, key: decl.key, sort: decl.sort, shard: decl.shard, ...(decl.decimals ? { decimals: decl.decimals } : {}), ...(decl.notify ? { notify: decl.notify } : {}) }, null, 2) + '\n');
        }
        const files = (await this.allDirectoryFiles(view, dir, revision)).filter(file => intersects(file.audiences, agent));
        const children = (await this.allDirectoryChildren(view, dir, revision)).filter(node => audienceOk(node.audience, agent));
        const table = this.host.tableForDirectory(dir);
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
    private virtualNodeFrom(kind: Virtual['kind'], dir: string, bytes: Uint8Array, revision: string, agent: readonly Audience[]): ExplorationNode {
        const path = dir + '/' + (kind === 'schema' ? 'schema.json' : 'README.md');
        const etag = etagOf(path, revision, agent);
        return {
            path, objectId: 'public:virtual:' + etag, kind: 'file',
            versions: [{ owner: 'public', version: revision }, { owner: 'moment', version: revision }, { owner: 'audience', version: agent.join(',') }],
            rawBytes: String(bytes.byteLength), provenance: { etag, media: kind === 'schema' ? 'application/schema+json' : 'text/markdown' }
        };
    }
    private readmeBytes(dir: string, files: readonly PublicFile[], children: readonly PublicNode[]): Uint8Array {
        const table = this.host.tableForDirectory(dir);
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
        const bytes = await this.virtualBytes(virtual, { actorId: '', momentId: '', query: null }, view, revision, agent);
        return this.virtualNodeFrom(virtual.kind, virtual.dir, bytes, revision, agent);
    }
    async list(path: string, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest): Promise<Page<ExplorationNode>> {
        const revision = await this.revision(scope, view), agent = await this.audiences(scope, view);
        const directory = this.directories().has(path), virtual = this.virtual(path);
        const file = directory ? null : await readFile(view, path, revision);
        const node = file || directory ? null : await readNode(view, path, revision);
        if (!file && !node && !directory && !virtual)
            fail('EXPLORATION_NOT_VISIBLE');
        if (!directory && node?.kind !== 'dir')
            fail('EXPLORATION_NOT_DIRECTORY');
        const items = new Map<string, ExplorationNode>();
        for (const dir of this.directories())
            if (dir !== path && parentOf(dir) === path)
                items.set(dir, this.directoryNode(dir, revision));
        const children = (await this.allDirectoryChildren(view, path, revision, ['dir', 'doc', 'derived'])).filter(node => audienceOk(node.audience, agent));
        for (const child of children) {
            if (child.kind === 'table' || child.kind === 'derived')
                continue;
            items.set(child.path, await this.nodeOf(child, revision, agent));
        }
        const files = (await this.allDirectoryFiles(view, path, revision)).filter(file => intersects(file.audiences, agent));
        for (const entry of files)
            items.set(entry.file, await this.fileStat(entry, scope, view, revision, agent));
        items.set(path + '/README.md', this.virtualNodeFrom('readme', path, this.readmeBytes(path, files, children), revision, agent));
        const table = this.host.tableForDirectory(path);
        if (table) {
            const bytes = Buffer.from(JSON.stringify({ path: table.path, title: table.title, description: table.description, schema: table.schema, key: table.key, sort: table.sort, shard: table.shard, ...(table.decimals ? { decimals: table.decimals } : {}), ...(table.notify ? { notify: table.notify } : {}) }, null, 2) + '\n');
            items.set(path + '/schema.json', this.virtualNodeFrom('schema', path, bytes, revision, agent));
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
            const pattern = this.globLike(glob, true), suffix = this.suffix(glob);
            const audience = agent.map(audience => 'audiences LIKE ?');
            const fileMatch = suffix === undefined ? `file LIKE ? ESCAPE '\\'` : 'extension=?';
            const fileMatchParameters = suffix === undefined ? [pattern] : [suffix];
            const nodeFilter = `path>=? AND path<? AND ${AS_OF} AND (${agent.map(() => 'audience=?').join(' OR ')}) AND kind IN ('doc','derived') AND path LIKE ? ESCAPE '\\'`;
            const limit = request.maxItems + 1;
            const audienceParameters = agent.map(a => `%"${a}"%`);
            // 当前版本与历史版本分开查询：live 走部分索引，历史版本只覆盖 R_m 之前仍有效的旧版本。
            const liveSql = `SELECT file,rev_from,audiences FROM public_files INDEXED BY public_files_live_name WHERE file>=? AND file<? AND rev_to IS NULL AND rev_from<=? AND (${audience.map(() => 'audiences LIKE ?').join(' OR ')}) AND ${fileMatch}`;
            const historySql = `SELECT file,rev_from,audiences FROM public_files INDEXED BY public_files_history_name WHERE file>=? AND file<? AND rev_to IS NOT NULL AND rev_to>? AND rev_from<=? AND (${audience.map(() => 'audiences LIKE ?').join(' OR ')}) AND ${fileMatch}`;
            const liveParameters = [lower, upper, at, ...audienceParameters, ...fileMatchParameters];
            const historyParameters = [lower, upper, at, at, ...audienceParameters, ...fileMatchParameters];
            // 热路径沿用单条 group_concat 聚合；只有聚合结果超出数据库预算时才退回 keyset 分页。
            const scan = async (sql: string, parameters: readonly any[], count: number): Promise<[string, string, string][]> => {
                const bound = last === undefined ? '' : ' AND file>?';
                const boundParameters = last === undefined ? [] : [String(last)];
                try {
                    const [encoded] = await view.query({
                        sql: `SELECT group_concat(file || char(31) || rev_from || char(31) || audiences, char(30)) AS data FROM (${sql}${bound} ORDER BY file LIMIT ?)`,
                        parameters: [...parameters, ...boundParameters, count]
                    }, { maxRows: 1, maxBytes: this.batchBytes });
                    const data = encoded?.data === null || encoded?.data === undefined ? '' : String(encoded.data);
                    return data ? data.split('\u001e').map(line => {
                        const first = line.indexOf('\u001f'), second = line.indexOf('\u001f', first + 1);
                        return [line.slice(0, first), line.slice(first + 1, second), line.slice(second + 1)] as [string, string, string];
                    }) : [];
                }
                catch (error) {
                    const code = (error as { code?: string } | null)?.code ?? '';
                    if (!/SQLITE_(RESULT_LIMIT|QUERY_BUDGET|RETURN_BUDGET)/.test(String(error)) && !/SQLITE_(RESULT_LIMIT|QUERY_BUDGET|RETURN_BUDGET)/.test(code))
                        throw error;
                }
                const rows: [string, string, string][] = [];
                let cursor: string | undefined = last === undefined ? undefined : String(last);
                while (rows.length < count) {
                    const size = Math.min(count - rows.length, this.batchRows);
                    const pageBound = cursor === undefined ? '' : ' AND file>?';
                    const batch = await this.boundedRows(page => view.query({ sql: `${sql}${pageBound} ORDER BY file LIMIT ?`, parameters: [...parameters, ...(cursor === undefined ? [] : [cursor]), page] }, { maxRows: page, maxBytes: this.batchBytes }), size);
                    for (const row of batch.rows)
                        rows.push([String(row.file), String(row.rev_from), String(row.audiences)]);
                    if (batch.rows.length < batch.size)
                        break;
                    cursor = rows[rows.length - 1]![0];
                }
                return rows;
            };
            const live = await scan(liveSql, liveParameters, limit);
            const history = await scan(historySql, historyParameters, limit);
            const files: [string, string, string][] = [];
            let left = 0, right = 0;
            while (files.length < limit && (left < live.length || right < history.length)) {
                if (right >= history.length || (left < live.length && live[left]![0] < history[right]![0]))
                    files.push([...live[left++]!]);
                else
                    files.push([...history[right++]!]);
            }
            let fileTotal = files.length;
            if (last !== undefined || files.length > request.maxItems) {
                const [liveCount] = await view.query({ sql: `SELECT count(*) AS n FROM public_files WHERE file>=? AND file<? AND rev_to IS NULL AND rev_from<=? AND (${audience.map(() => 'audiences LIKE ?').join(' OR ')}) AND ${fileMatch}`, parameters: [lower, upper, at, ...audienceParameters, ...fileMatchParameters] }, { maxRows: 1, maxBytes: Math.min(65536, this.batchBytes) });
                const [historyCount] = await view.query({ sql: `SELECT count(*) AS n FROM public_files WHERE file>=? AND file<? AND rev_to IS NOT NULL AND rev_to>? AND rev_from<=? AND (${audience.map(() => 'audiences LIKE ?').join(' OR ')}) AND ${fileMatch}`, parameters: [lower, upper, at, at, ...audienceParameters, ...fileMatchParameters] }, { maxRows: 1, maxBytes: Math.min(65536, this.batchBytes) });
                fileTotal = Number(liveCount?.n ?? 0) + Number(historyCount?.n ?? 0);
            }
            const nodeBound = last === undefined ? '' : ' AND path>?';
            const nodeLimit = request.maxItems + 1;
            const nodes: SqliteRow[] = [];
            let nodeCursor = last === undefined ? undefined : String(last);
            while (nodes.length < nodeLimit) {
                const size = Math.min(nodeLimit - nodes.length, this.batchRows);
                const cursorBound = nodeCursor === undefined ? '' : ' AND path>?';
                let batch: readonly SqliteRow[];
                try {
                    batch = await view.query({ sql: `SELECT path,kind,audience,rev_from FROM public_nodes WHERE ${nodeFilter}${nodeBound}${cursorBound} ORDER BY path LIMIT ?`, parameters: [lower, upper, at, at, ...agent, pattern, ...(last === undefined ? [] : [String(last)]), ...(nodeCursor === undefined ? [] : [nodeCursor]), size] }, { maxRows: size, maxBytes: this.batchBytes });
                }
                catch (error) {
                    const code = (error as { code?: string } | null)?.code ?? '';
                    if (!/SQLITE_(RESULT_LIMIT|QUERY_BUDGET|RETURN_BUDGET)/.test(String(error)) && !/SQLITE_(RESULT_LIMIT|QUERY_BUDGET|RETURN_BUDGET)/.test(code))
                        throw error;
                    batch = (await this.boundedRows(page => view.query({ sql: `SELECT path,kind,audience,rev_from FROM public_nodes WHERE ${nodeFilter}${nodeBound}${cursorBound} ORDER BY path LIMIT ?`, parameters: [lower, upper, at, at, ...agent, pattern, ...(last === undefined ? [] : [String(last)]), ...(nodeCursor === undefined ? [] : [nodeCursor]), page] }, { maxRows: page, maxBytes: this.batchBytes }), size)).rows;
                }
                nodes.push(...batch);
                if (batch.length < size)
                    break;
                nodeCursor = String(batch[batch.length - 1]!.path);
            }
            const nodeTotal = last === undefined && nodes.length <= request.maxItems
                ? nodes.length
                : Number((await view.query({ sql: `SELECT count(*) AS n FROM public_nodes WHERE ${nodeFilter}`, parameters: [lower, upper, at, at, ...agent, pattern] }, { maxRows: 1, maxBytes: Math.min(65536, this.batchBytes) }))[0]?.n ?? 0);
            const total = fileTotal + nodeTotal;
            const matchedFiles: ExplorationNode[] = [], matchedNodes: ExplorationNode[] = [];
            for (const row of files) {
                const path = String(row[0]), rawAudiences = String(row[2]), audiences = rawAudiences === '["all"]' ? ALL_AUDIENCES : JSON.parse(rawAudiences) as Audience[];
                if (!intersects(audiences, agent))
                    continue;
                matchedFiles.push(this.foundFile(path, String(row[1]), revision, effective(audiences, agent)));
            }
            for (const row of nodes) {
                const path = String(row.path);
                if (!audienceOk(String(row.audience), agent))
                    continue;
                matchedNodes.push({ path, objectId: 'public:doc:' + path, kind: 'file', versions: [{ owner: 'public', version: String(row.rev_from) }, { owner: 'moment', version: revision }] });
            }
            const merged = this.mergeByPath(matchedFiles, matchedNodes);
            return page(merged, String(total), view.revision, request, id, node => node.path, false, merged.map(node => this.nodeBytes(node)));
        }
        const files = await this.scanFiles(view, lower, upper, at, Number.MAX_SAFE_INTEGER, 'file,parent,rev_from,audiences');
        const nodes = await this.scanNodes(view, lower, upper, at, 'path,kind,audience,rev_from');
        const matchedFiles: ExplorationNode[] = [], matchedNodes: ExplorationNode[] = [];
        for (const row of files) {
            const path = String(row.file), audiences = JSON.parse(String(row.audiences)) as Audience[];
            if (!intersects(audiences, agent) || !this.match(path, glob))
                continue;
            matchedFiles.push(this.foundFile(path, String(row.rev_from), revision, effective(audiences, agent)));
        }
        for (const row of nodes) {
            const path = String(row.path), kind = String(row.kind);
            if (kind === 'dir' || kind === 'table' || !audienceOk(String(row.audience), agent) || !this.match(path, glob))
                continue;
            matchedNodes.push({ path, objectId: 'public:doc:' + path, kind: 'file', versions: [{ owner: 'public', version: String(row.rev_from) }, { owner: 'moment', version: revision }] });
        }
        const matched = this.mergeByPath(matchedFiles, matchedNodes);
        const remaining = last === undefined ? matched : matched.filter(node => compareCodePoints(node.path, String(last)) > 0);
        return page(remaining, String(matched.length), view.revision, request, id, node => node.path);
    }
    private mergeByPath(left: readonly ExplorationNode[], right: readonly ExplorationNode[]): ExplorationNode[] {
        if (!right.length)
            return left as ExplorationNode[];
        if (!left.length)
            return right as ExplorationNode[];
        const merged: ExplorationNode[] = [];
        let a = 0, b = 0;
        while (a < left.length && b < right.length) {
            if (compareCodePoints(left[a]!.path, right[b]!.path) <= 0)
                merged.push(left[a++]!);
            else
                merged.push(right[b++]!);
        }
        while (a < left.length)
            merged.push(left[a++]!);
        while (b < right.length)
            merged.push(right[b++]!);
        return merged;
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
    private suffix(glob: string) { const match = /^\*\\.([^*?\\[\\]]+)$/.exec(glob); return match ? match[1] : undefined; }
    private globLike(glob: string, basename: boolean) { const like = glob.replace(/[\\%_]/g, '\\$&').replace(/\*/g, '%'); return basename && !like.startsWith('%') ? '%/' + like : like; }
    private revisionValue(revision: string) { return BigInt(revision); }
    private match(path: string, glob: string) { return matchesGlob(path, glob) || matchesGlob(posix.basename(path), glob); }
    private async openCached(node: ExplorationNode, etag: string, render: () => Promise<Uint8Array>): Promise<{ value: ExplorationContent; release: () => Promise<void> }> {
        const handle = await this.host.cache.open(etag, render);
        const size = (await handle.stat()).size;
        let released = false;
        return {
            value: {
                node,
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
    async open(node: ExplorationNode, scope: DiscoveryScope, view: SqliteReadView): Promise<{ value: ExplorationContent; release: () => Promise<void> }> {
        const revision = await this.revision(scope, view), agent = await this.audiences(scope, view);
        const provenance = node.provenance !== null && typeof node.provenance === 'object' && !Array.isArray(node.provenance) ? node.provenance as Record<string, unknown> : undefined;
        const momentVersion = node.versions.find(version => version.owner === 'moment')?.version;
        // 同一 moment 的 public 文件 etag 不变：stat 返回的节点自带 etag/rows 时直接开缓存，省一次主键查询。
        if (momentVersion === revision && typeof provenance?.etag === 'string' && typeof provenance.rows === 'number') {
            const publicVersion = node.versions.find(version => version.owner === 'public')?.version;
            const audienceVersion = node.versions.find(version => version.owner === 'audience')?.version;
            if (typeof publicVersion === 'string' && typeof audienceVersion === 'string') {
                const audiences = audienceVersion === 'all' ? ALL_AUDIENCES : audienceVersion.split(',');
                const file: PublicFile = {
                    file: node.path, parent: '', tablePath: String(provenance.table ?? ''),
                    rows: Number(provenance.rows), bytes: Number(node.rawBytes ?? 0), revision: publicVersion, audiences
                };
                return this.openCached(node, provenance.etag, () => this.render(file, revision, audiences, view));
            }
        }
        const file = await readFile(view, node.path, revision);
        let current: ExplorationNode;
        if (file) {
            const audiences = effective(file.audiences, agent);
            if (!audiences.length)
                fail('EXPLORATION_STALE');
            current = this.fileNode(file, revision, agent, file.bytes);
            if (current.objectId !== node.objectId || !isDeepStrictEqual(current.versions, node.versions))
                fail('EXPLORATION_STALE');
            const etag = etagOf(file.file, file.revision, audiences);
            return this.openCached(current, etag, () => this.render(file, revision, audiences, view));
        }
        current = await this.stat(node.path, scope, view);
        if (current.objectId !== node.objectId || !isDeepStrictEqual(current.versions, node.versions))
            fail('EXPLORATION_STALE');
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
    private async searchLiteral(entries: readonly { readonly node: ExplorationNode; readonly disk: string }[], needle: Buffer, signal: AbortSignal, emit: (m: ExplorationMatch) => boolean): Promise<boolean> {
        const text = needle.toString('utf8');
        for (const entry of entries) {
            signal.throwIfAborted();
            const bytes = await readDisk(entry.disk);
            let start = 0, line = 1;
            while (start <= bytes.length) {
                signal.throwIfAborted();
                const newline = bytes.indexOf(10, start), end = newline < 0 ? bytes.length : newline;
                const content = bytes.subarray(start, end);
                let index = content.indexOf(needle);
                if (index >= 0) {
                    const lineText = content.toString('utf8');
                    while (index >= 0) {
                        const match: ExplorationMatch = {
                            path: entry.node.path, objectId: entry.node.objectId, versions: entry.node.versions,
                            rawOffset: String(start + index), text, lineText, line, column: index + 1
                        };
                        if (!emit(match))
                            return true;
                        index = content.indexOf(needle, index + needle.length);
                    }
                }
                if (newline < 0)
                    break;
                start = newline + 1;
                line++;
            }
        }
        return false;
    }
    async search(request: { readonly root: string; readonly pattern: string; readonly mode: 'literal' | 'regex'; readonly ignoreCase?: boolean }, scope: DiscoveryScope, view: SqliteReadView, signal: AbortSignal, emit: (match: ExplorationMatch) => boolean): Promise<void> {
        if (request.root !== this.prefix && !request.root.startsWith(this.prefix + '/'))
            return fail('EXPLORATION_NOT_VISIBLE');
        const revision = await this.revision(scope, view), agent = await this.audiences(scope, view);
        const lower = request.root + '/', upper = request.root + '0', at = this.revisionValue(revision);
        const pattern = request.pattern;
        let records: PublicFile[];
        const literal = this.literalOf(pattern, request.mode);
        const [state] = await view.query({ sql: 'SELECT max(rowid) AS revision FROM public_search_state' }, { maxRows: 1, maxBytes: 1024 });
        const indexed = state?.revision === null || state?.revision === undefined ? null : BigInt(state.revision as bigint | number | string);
        const stale = indexed === null || indexed < at;
        if (stale) {
            this.host.stats.searchStale++;
            this.host.stats.fallbackScans++;
        }
                if (literal && literal.length >= 3 && this.host.searchIndex !== 'none' && !stale) {
            try {
                records = [];
                                let cursor: string | undefined;
                while (true) {
                    const bound = cursor === undefined ? '' : ' AND pr.file>?';
                    const batch = await this.boundedRows(size => view.query({
                        sql: `SELECT DISTINCT pr.file AS file FROM public_fts f JOIN public_rows pr ON pr.rowid=f.rowid WHERE public_fts MATCH ? AND pr.file>=? AND pr.file<? AND pr.rev_from<=? AND (pr.rev_to IS NULL OR pr.rev_to>?) AND (pr.audience='all' OR pr.audience IN (${agent.map(() => '?').join(',')}))${bound} ORDER BY pr.file LIMIT ?`,
                        parameters: [this.ftsPhrase(literal), lower, upper, at, at, ...agent, ...(cursor === undefined ? [] : [cursor]), size]
                    }, { maxRows: size, maxBytes: this.batchBytes }), this.batchRows);
                    if (!batch.rows.length)
                        break;
                    for (const row of batch.rows) {
                        const record = await readFile(view, String(row.file), revision);
                        if (record && intersects(record.audiences, agent))
                            records.push(record);
                    }
                    if (batch.rows.length < batch.size)
                        break;
                    cursor = String(batch.rows[batch.rows.length - 1]!.file);
                }
            }
            catch (error) {
                                if (!/fts5|MATCH/i.test(String(error)))
                    throw error;
                records = await this.allFiles(lower, upper, at, agent, view);
            }
        }
        else
            records = await this.allFiles(lower, upper, at, agent, view);
                const entries = [];
        for (const record of records) {
            if (!intersects(record.audiences, agent))
                continue;
            const audiences = effective(record.audiences, agent);
            const etag = etagOf(record.file, record.revision, audiences);
                        const cached = await this.host.cache.ensureFile(etag, () => this.render(record, revision, audiences, view));
            const node = this.fileNode(record, revision, agent, cached.bytes);
            entries.push({ node, disk: cached.path });
        }
        entries.sort((a, b) => compareCodePoints(a.node.path, b.node.path));
        const byDisk = new Map(entries.map(entry => [entry.disk, entry.node]));
        const options = this.host.searchOptions();
        let totalBytes = 0;
        for (const entry of entries)
            totalBytes += Number(entry.node.rawBytes ?? 0);
        if (totalBytes > options.maxSearchBytes)
            return fail('EXPLORATION_SEARCH_BUDGET');
        // 小候选集字面量搜索走进程内匹配，避免每次 grep spawn rg；大集合仍由 rg 做精确核对。
        const literalNeedle = request.mode === 'literal' && request.ignoreCase !== true ? Buffer.from(request.pattern, 'utf8') : undefined;
        if (literalNeedle && literalNeedle.length && entries.length <= SEARCH_BATCH && totalBytes <= options.maxSearchOutputBytes) {
            await this.searchLiteral(entries, literalNeedle, signal, emit);
            return;
        }
        for (let offset = 0; offset < entries.length; offset += SEARCH_BATCH) {
            const batch = entries.slice(offset, offset + SEARCH_BATCH);
            const search = new NativeSearch(pattern, request.mode, options, request.ignoreCase === true, batch.map(entry => entry.disk));
            let stopped = false;
            await search.runFiles(signal, (position, text, line, column, lineText, path) => {
                const node = path === undefined ? undefined : byDisk.get(path);
                if (!node)
                    return;
                const match: ExplorationMatch = { path: node.path, objectId: node.objectId, versions: node.versions, rawOffset: String(position), text, lineText, line, column };
                if (!emit(match))
                    stopped = true;
            });
            if (stopped)
                break;
        }
    }
    private ftsPhrase(literal: string): string {
        const grams = new Set<string>();
        for (let index = 0; index + 3 <= literal.length; index++)
            grams.add(literal.slice(index, index + 3));
        return [...grams].map(gram => '"' + gram.replace(/"/g, '""') + '"').join(' AND ');
    }
    private literalOf(pattern: string, mode: 'literal' | 'regex'): string | undefined {
        if (mode === 'literal')
            return pattern;
        const runs = pattern.match(/[\p{L}\p{N}_]{3,}/gu);
        return runs?.sort((a, b) => b.length - a.length)[0];
    }
    private async allFiles(lower: string, upper: string, at: bigint, agent: readonly Audience[], view: SqliteReadView): Promise<PublicFile[]> {
        const all: PublicFile[] = [];
        let cursor: string | undefined;
        while (true) {
            const bound = cursor === undefined ? '' : ' AND file>?';
            const batch = await this.boundedRows(size => view.query({
                sql: `SELECT file,parent,table_path,rows,bytes,audiences,rev_from FROM public_files WHERE file>=? AND file<? AND rev_from<=? AND (rev_to IS NULL OR rev_to>?) AND (audiences LIKE ? OR ${agent.map(() => 'audiences LIKE ?').join(' OR ')})${bound} ORDER BY file LIMIT ?`,
                parameters: [lower, upper, at, at, `%"all"%`, ...agent.map(a => `%"${a}"%`), ...(cursor === undefined ? [] : [cursor]), size]
            }, { maxRows: size, maxBytes: this.batchBytes }), this.batchRows);
            all.push(...batch.rows.map(row => ({ file: String(row.file), parent: String(row.parent), tablePath: String(row.table_path), rows: Number(row.rows), bytes: Number(row.bytes), revision: String(row.rev_from), audiences: JSON.parse(String(row.audiences)) as Audience[] })));
            if (batch.rows.length < batch.size)
                break;
            cursor = String(batch.rows[batch.rows.length - 1]!.file);
        }
        return all;
    }
    async collect(): Promise<Page<StoredValue>> { return fail('EXPLORATION_NOT_COLLECTION'); }
}
export type { ExplorationMatch };
