/** /self 渲染缓存：键为 (path, actor, versions)，按字节 LRU 淘汰。 */
export interface SelfCacheDiagnostics {
    readonly renders: number;
    readonly hits: number;
    readonly entries: number;
    readonly bytes: number;
}
const DEFAULT_BUDGET = 64 * 1024 * 1024;

export class SelfCache {
    private readonly memory = new Map<string, Uint8Array>();
    private readonly inflight = new Map<string, Promise<Uint8Array>>();
    private stored = 0;
    private renders = 0;
    private hits = 0;
    constructor(private readonly budget = DEFAULT_BUDGET) { }
    static key(path: string, actorId: string, versions: readonly { owner: string; version: string }[]): string {
        return path + '\u0000' + actorId + '\u0000' + versions.map(item => item.owner + '\u0001' + item.version).join('\u0000');
    }
    private remember(key: string, value: Uint8Array) {
        const current = this.memory.get(key);
        if (current) {
            this.stored -= current.byteLength;
            this.memory.delete(key);
        }
        this.memory.set(key, value);
        this.stored += value.byteLength;
        while (this.stored > this.budget && this.memory.size) {
            const [oldest, bytes] = this.memory.entries().next().value as [string, Uint8Array];
            this.memory.delete(oldest);
            this.stored -= bytes.byteLength;
        }
    }
    async bytes(key: string, render: () => Promise<Uint8Array>): Promise<Uint8Array> {
        const cached = this.memory.get(key);
        if (cached) {
            this.hits++;
            this.memory.delete(key);
            this.memory.set(key, cached);
            return cached;
        }
        const pending = this.inflight.get(key);
        if (pending)
            return pending;
        const work = (async () => {
            const value = await render();
            const copy = value instanceof Uint8Array ? value : new Uint8Array(value as ArrayBufferLike);
            this.renders++;
            this.remember(key, copy);
            return copy;
        })();
        this.inflight.set(key, work);
        try {
            return await work;
        }
        finally {
            this.inflight.delete(key);
        }
    }
    diagnostics(): SelfCacheDiagnostics {
        return { renders: this.renders, hits: this.hits, entries: this.memory.size, bytes: this.stored };
    }
}
