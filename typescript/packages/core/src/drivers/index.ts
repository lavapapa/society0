import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "drivers", "summary": "drivers", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./drivers", "provides": [{ "key": "drivers", "protocol": "drivers", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import { slotKey } from '@society0/host/slots';
import type { Driver, Invocation, Lease } from '../contracts/runtime.js';
export type { Driver } from '../contracts/runtime.js';
function key(name: string) {
    if (!name)
        throw Error('DRIVER_NAME');
    return slotKey<Driver>('core/drivers/' + name);
}
declare module '@society0/host' {
    interface Context {
        drivers: DriverRegistry;
    }
}
export class DriverRegistry extends Service {
    static Config = Config;
    static inject = ['slots'];
    constructor(ctx: Context) { super(ctx, 'drivers'); }
    register(name: string, driver: Driver) {
        const slot = key(name);
        const undeclare = this.ctx.slots.declare({ key: slot, strategy: 'single', required: true, validate: (value: unknown) => {
                if (typeof (value as Driver)?.activate !== 'function')
                    throw Error('DRIVER_INVALID');
                return value as Driver;
            } });
        let withdraw: () => void | Promise<void>;
        try {
            withdraw = this.ctx.slots.contribute(slot, { id: name, value: driver });
        }
        catch (error) {
            void undeclare();
            throw error;
        }
        return this.ctx.effect(() => async () => { await Promise.all([withdraw(), undeclare()]); });
    }
    async acquire(name: string, context: Invocation): Promise<Lease<Driver>> { const selected = await this.ctx.slots.acquire(key(name), context); return { value: selected.items[0]!.value, release: () => selected.release() }; }
}
export default DriverRegistry;
