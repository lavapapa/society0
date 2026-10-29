import type { Json } from '@society0/core/contracts/common';
import { datasetFault } from '../datasets/codec.js';
export class JsonSize {
    private bytes = 0;
    private seen = new Set<object>();
    constructor(private max: number) { }
    private use(bytes: number) { this.bytes += bytes; if (this.bytes > this.max)
        datasetFault('JSONL_RECORD_BUDGET'); }
    private string(value: string) {
        this.use(2);
        if (Buffer.byteLength(value) > this.max - this.bytes)
            datasetFault('JSONL_RECORD_BUDGET');
        for (let offset = 0; offset < value.length;) {
            let end = Math.min(offset + 4096, value.length);
            const last = value.charCodeAt(end - 1);
            if (last >= 0xd800 && last <= 0xdbff && end < value.length)
                end++;
            this.use(Buffer.byteLength(JSON.stringify(value.slice(offset, end))) - 2);
            offset = end;
        }
    }
    private array(value: readonly Json[]) { this.use(2 + Math.max(0, value.length - 1)); for (const item of value)
        this.visit(item); }
    private object(value: {
        readonly [key: string]: Json;
    }) { this.use(2); let first = true; for (const key in value) {
        if (!Object.hasOwn(value, key))
            continue;
        if (!first)
            this.use(1);
        first = false;
        this.string(key);
        this.use(1);
        this.visit(value[key]!);
    } }
    private container(value: readonly Json[] | {
        readonly [key: string]: Json;
    }) { if (this.seen.has(value))
        datasetFault('JSONL_CYCLE'); this.seen.add(value); try {
        if (Array.isArray(value))
            this.array(value);
        else
            this.object(value as {
                readonly [key: string]: Json;
            });
    }
    finally {
        this.seen.delete(value);
    } }
    private scalar(value: null | boolean | number | string) { if (typeof value === 'string')
        this.string(value);
    else {
        if (typeof value === 'number' && !Number.isFinite(value))
            datasetFault('JSONL_NUMBER');
        this.use(JSON.stringify(value).length);
    } }
    visit(value: Json) { if (value !== null && typeof value === 'object')
        this.container(value);
    else
        this.scalar(value); }
}
export function measureJson(value: Json, max: number): void { new JsonSize(max).visit(value); }
