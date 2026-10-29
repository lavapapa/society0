import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ every: Schema.natural().min(1).default(1) });
export const metadata: PluginMetadata = { "title": "checkpoint-policy", "summary": "checkpoint-policy", "configVersion": 1, "configPolicy": [{ "path": "/every", "mode": "new-run", "affectsResults": true }], "scope": "generation", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./checkpoint-policy", "provides": [{ "key": "checkpointPolicy", "protocol": "checkpointPolicy", "major": 1, "capabilities": [] }], "requires": [] };
import type { AdvanceResult, TerminationInput, CheckpointPolicy as Policy } from '../contracts/runtime.js';
declare module '@society0/host' {
    interface Context {
        checkpointPolicy: Policy;
    }
}
export class CheckpointPolicy extends Service {
    static Config = Config;
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input = {}) { super(ctx, 'checkpointPolicy'); this.config = Config(input); }
    choose(input: {
        advancesSinceCut: string;
        completion: {
            complete: boolean;
        };
    }): 'publish-cut' | 'continue' { return input.completion.complete && BigInt(input.advancesSinceCut) >= BigInt(this.config.every) ? 'publish-cut' : 'continue'; }
}
export default CheckpointPolicy;
