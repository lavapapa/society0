import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = {
    "title": "propagation-neighbors", "summary": "propagation-neighbors", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": []
};
export const api: ApiEntry = {
    "exportPath": "./propagation-neighbors", "provides": [], "slots": { "provides": ["society/social-propagation"] }, "requires": [{
            "service": {
                "key": "slots", "protocol": "slots", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import { propagationSlot } from '../social/index.js';
export default {
    Config, inject: ['slots'], apply(ctx: Context) {
        ctx.slots.contribute(propagationSlot, {
            id: 'neighbor-broadcast/v1', value: {
                id: 'neighbor-broadcast/v1', async recipients(input, view) {
                    return view.followers(input.actorId);
                }
            }
        });
    }
};
