import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = {
    "title": "measurement-every", "summary": "measurement-every", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": []
};
export const api: ApiEntry = {
    "exportPath": "./measurement-every", "provides": [], "requires": [{
            "service": {
                "key": "slots", "protocol": "slots", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import { samplingSlot } from '../measurements/index.js';
export default {
    Config, inject: ['slots'], apply(ctx: Context) {
        ctx.slots.contribute(samplingSlot, {
            id: 'every-advance/v1', value: {
                id: 'every-advance/v1', shouldSample: () => true
            }
        });
    }
};
