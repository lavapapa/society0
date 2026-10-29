import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "clock-event", "summary": "clock-event", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./clock-event", "provides": [{ "key": "logicalClock", "protocol": "logicalClock", "major": 1, "capabilities": [] }], "requires": [] };
import type { Json, LogicalClock as Clock } from '../contracts/runtime.js';
import { integer } from '../clock-ticks/index.js';
function event(v: Json) {
    if (!v || typeof v !== 'object' || Array.isArray(v))
        throw Error('CLOCK_EVENT');
    const x = v as {
        time: Json;
        offset: Json;
    };
    return { time: integer(x.time), offset: integer(x.offset) };
}
export class LogicalClock extends Service implements Clock {
    static Config = Config;
    readonly codec = 'event-time/1';
    constructor(ctx: Context) { super(ctx, 'logicalClock'); }
    initial() { return { time: '0', offset: '0' }; }
    compare(a: Json, b: Json) { const x = event(a), y = event(b); return x.time < y.time ? -1 : x.time > y.time ? 1 : x.offset < y.offset ? -1 : x.offset > y.offset ? 1 : 0; }
    advance(previous: Json, trigger: Json) {
        event(trigger);
        if (this.compare(previous, trigger) > 0)
            throw Error('CLOCK_REGRESSION');
        return trigger;
    }
}
export default LogicalClock;
