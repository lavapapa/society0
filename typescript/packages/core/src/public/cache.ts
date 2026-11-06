/** 渲染缓存：内存 LRU + 磁盘 etag 文件；缓存只依赖 etag，可删可重建。 */
import { mkdir, open, readFile, rename, writeFile } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { dirname, join } from 'node:path';
import type { PublicCacheDiagnostics } from '../contracts/public.js';

export class RenderCache {
    private readonly memory = new Map<string, Uint8Array>();
    private readonly inflight = new Map<string, Promise<Uint8Array>>();
    private stored = 0;
    private renders = 0;
    private memoryHits = 0;
    private diskHits = 0;
    private misses = 0;
    constructor(private readonly directory: string, private readonly budget: number) { }
    private key(etag: string) { return Buffer.from(etag, 'utf8').toString('base64url'); }
    pathOf(etag: string) { const name = this.key(etag); return join(this.directory, name.slice(0, 2), name); }
    private remember(etag: string, value: Uint8Array) {
        const current = this.memory.get(etag);
        if (current) {
            this.stored -= current.byteLength;
            this.memory.delete(etag);
        }
        this.memory.set(etag, value);
        this.stored += value.byteLength;
        while (this.stored > this.budget && this.memory.size) {
            const [key, oldest] = this.memory.entries().next().value as [string, Uint8Array];
            this.memory.delete(key);
            this.stored -= oldest.byteLength;
        }
    }
    async bytes(etag: string, render: () => Promise<Uint8Array>): Promise<Uint8Array> {
        const cached = this.memory.get(etag);
        if (cached) {
            this.memoryHits++;
            this.memory.delete(etag);
            this.memory.set(etag, cached);
            return cached;
        }
        const pending = this.inflight.get(etag);
        if (pending)
            return pending;
        const work = (async () => {
            try {
                const disk = await readFile(this.pathOf(etag));
                this.diskHits++;
                const value = new Uint8Array(disk);
                this.remember(etag, value);
                return value;
            }
            catch (error) {
                if ((error as { code?: string }).code !== 'ENOENT')
                    throw error;
            }
            this.misses++;
            this.renders++;
            const value = await render();
            const copy = value instanceof Uint8Array ? value : new Uint8Array(value as ArrayBufferLike);
            this.remember(etag, copy);
            const path = this.pathOf(etag);
            await mkdir(dirname(path), { recursive: true });
            const temporary = path + '.' + randomUUID() + '.tmp';
            await writeFile(temporary, copy);
            await rename(temporary, path);
            return copy;
        })();
        this.inflight.set(etag, work);
        try {
            return await work;
        }
        finally {
            this.inflight.delete(etag);
        }
    }
    /** 确保磁盘缓存存在，打开只读句柄供范围读取。 */
    async open(etag: string, render: () => Promise<Uint8Array>): Promise<FileHandle> {
        await this.bytes(etag, render);
        return open(this.pathOf(etag), 'r');
    }
    diagnostics(): PublicCacheDiagnostics {
        return { renders: this.renders, memoryHits: this.memoryHits, diskHits: this.diskHits, misses: this.misses, entries: this.memory.size, bytes: this.stored };
    }
}
