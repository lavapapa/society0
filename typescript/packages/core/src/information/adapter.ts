/**
 * information 适配器（deprecated）：把旧 query 来源包装成拉式派生提供者。
 *
 * 新代码应注册 /public 推式表或 /self 提供者；此适配器只用于一个 generation 的过渡。
 * 渲染语义固定为 `query({})` 全量分页，保留来源返回顺序；批内分页大小由调用方 request 控制。
 */
import { compareCodePoints } from '@society0/host/order';
import type { DiscoveryScope, DomainVersion, PageRequest, StoredValue } from '../contracts/data.js';
import type { InformationSource } from '../contracts/domain.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import type { Json } from '../contracts/common.js';
import type { PublicShard } from '../contracts/public.js';

export interface InformationAdapterOptions {
    /** 派生文件基础路径，例如 '/public/legacy/feed'。 */
    readonly path: string;
    /** 可选：按行字段分片；缺省为单文件 `<path>.jsonl`。 */
    readonly shard?: PublicShard;
}

export interface InformationDerivedFile {
    readonly name: string;
    readonly kind: 'file';
}

export interface InformationDerivedProvider {
    readonly path: string;
    readonly media: 'application/jsonl';
    readonly deprecated: true;
    versions(scopeOrView: DiscoveryScope | SqliteReadView, maybeView?: SqliteReadView): Promise<readonly DomainVersion[]>;
    list(path: string, scopeOrView: DiscoveryScope | SqliteReadView, maybeView?: SqliteReadView): Promise<readonly InformationDerivedFile[]>;
    render(path: string, scopeOrView: DiscoveryScope | SqliteReadView | readonly string[], viewOrSignal?: SqliteReadView | AbortSignal, signal?: AbortSignal): AsyncIterable<Uint8Array>;
}

const DEFAULT_SCOPE: DiscoveryScope = { actorId: '', momentId: '', query: null };
const PAGE = 1000;
const BYTES = 16 * 1024 * 1024;
const MAX_PAGES = 1_000_000;

function isView(value: unknown): value is SqliteReadView {
    return !!value && typeof (value as SqliteReadView).query === 'function';
}

function scopeView(a: DiscoveryScope | SqliteReadView, b?: SqliteReadView): { scope: DiscoveryScope; view: SqliteReadView } {
    if (isView(a))
        return { scope: DEFAULT_SCOPE, view: a };
    if (!b || !isView(b))
        throw Error('INFORMATION_ADAPTER_VIEW');
    return { scope: a, view: b };
}

function fileName(options: InformationAdapterOptions, row: Json): string {
    const base = options.path.endsWith('.jsonl') ? options.path.slice(0, -'.jsonl'.length) : options.path;
    if (!options.shard || options.shard.format === 'none')
        return (options.shard?.file?.(String((row as Record<string, Json>)[options.shard.by] ?? '')) ?? base.slice(base.lastIndexOf('/') + 1)) + '.jsonl';
    const value = String((row as Record<string, Json>)[options.shard.by] ?? '');
    const name = options.shard.file?.(value)
        ?? (options.shard.format === 'day' ? value.slice(0, 10)
            : options.shard.format === 'month' ? value.slice(0, 7)
                : options.shard.format === 'year' ? value.slice(0, 4)
                    : encodeURIComponent(value));
    return name + '.jsonl';
}

function filePath(options: InformationAdapterOptions, name: string): string {
    const base = options.path.endsWith('.jsonl') ? options.path.slice(0, -'.jsonl'.length) : options.path;
    return options.shard ? base + '/' + name : base + '.jsonl';
}

function rowOf(value: StoredValue): Json {
    if (!value || value.kind !== 'inline')
        throw Error('INFORMATION_ADAPTER_REFERENCE');
    return value.value;
}

export function publicFromInformation(source: InformationSource, options: InformationAdapterOptions): InformationDerivedProvider {
    if (!source || typeof source.query !== 'function')
        throw Error('INFORMATION_ADAPTER_SOURCE');
    if (typeof options?.path !== 'string' || !options.path.startsWith('/'))
        throw Error('INFORMATION_ADAPTER_PATH');
    async function all(scope: DiscoveryScope, view: SqliteReadView): Promise<Json[]> {
        const values: Json[] = [];
        let cursor: string | undefined;
        for (let pages = 0; pages < MAX_PAGES; pages++) {
            const request: PageRequest = { maxItems: PAGE, maxResponseBytes: BYTES, ...(cursor === undefined ? {} : { cursor }) };
            const result = await source.query({}, scope, view, request);
            for (const item of result.items)
                values.push(rowOf(item));
            if (!result.next)
                return values;
            if (result.next === cursor)
                throw Error('INFORMATION_ADAPTER_CURSOR');
            cursor = result.next;
        }
        throw Error('INFORMATION_ADAPTER_PAGES');
    }
    return {
        path: options.path,
        media: 'application/jsonl',
        deprecated: true,
        async versions(scopeOrView, maybeView) {
            const { view } = scopeView(scopeOrView, maybeView);
            return [{ owner: 'information/' + source.id, version: String(view.revision) }];
        },
        async list(_path, scopeOrView, maybeView) {
            const { scope, view } = scopeView(scopeOrView, maybeView);
            const names = new Set<string>();
            if (!options.shard)
                names.add(fileName(options, {} as Json));
            else
                for (const row of await all(scope, view))
                    names.add(fileName(options, row));
            return [...names].sort(compareCodePoints).map(name => ({ name, kind: 'file' as const }));
        },
        async *render(path, scopeOrView, viewOrSignal, signal) {
            let scope: DiscoveryScope, view: SqliteReadView | undefined, actualSignal: AbortSignal | undefined = signal;
            if (Array.isArray(scopeOrView)) {
                scope = DEFAULT_SCOPE;
                view = isView(viewOrSignal) ? viewOrSignal : undefined;
            }
            else if (isView(scopeOrView)) {
                scope = DEFAULT_SCOPE;
                view = scopeOrView;
                actualSignal = viewOrSignal instanceof AbortSignal ? viewOrSignal : signal;
            }
            else {
                scope = scopeOrView as DiscoveryScope;
                view = isView(viewOrSignal) ? viewOrSignal : undefined;
            }
            if (!view)
                throw Error('INFORMATION_ADAPTER_VIEW');
            const target = path;
            for (const row of await all(scope, view)) {
                if (filePath(options, fileName(options, row)) !== target)
                    continue;
                actualSignal?.throwIfAborted();
                yield Buffer.from(JSON.stringify(row) + '\n');
            }
        }
    };
}
