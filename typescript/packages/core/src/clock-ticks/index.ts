import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "clock-ticks", "summary": "clock-ticks", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./clock-ticks", "provides": [{ "key": "logicalClock", "protocol": "logicalClock", "major": 1, "capabilities": [] }], "requires": [] };
import type { Json, LogicalClock as Clock } from '../contracts/runtime.js';
export function integer(value: Json): bigint {
    if (typeof value !== 'string' || !/^(0|[1-9][0-9]*)$/.test(value))
        throw Error('CLOCK_INTEGER');
    return BigInt(value);
}
declare module '@society0/host' {
    interface Context {
        logicalClock: Clock;
    }
}
export class LogicalClock extends Service implements Clock {
    static Config = Config;
    readonly codec = 'ticks/1';
    constructor(ctx: Context) { super(ctx, 'logicalClock'); }
    initial() { return '0'; }
    compare(a: Json, b: Json) { const x = integer(a), y = integer(b); return x < y ? -1 : x > y ? 1 : 0; }
    advance(previous: Json, _trigger: Json) { return String(integer(previous) + 1n); }
}
export default LogicalClock;
