import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ onIncomplete: Schema.union(['pause', 'fail']).default('pause') });
export const metadata: PluginMetadata = { "title": "termination", "summary": "termination", "configVersion": 1, "configPolicy": [{ path: '/onIncomplete', mode: 'boundary', affectsResults: true }], "scope": "generation", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./termination", "provides": [{ "key": "terminationPolicy", "protocol": "terminationPolicy", "major": 1, "capabilities": [] }], "requires": [] };
import type { AdvanceResult, TerminationInput, TerminationPolicy as Policy } from '../contracts/runtime.js';
declare module '@society0/host' {
    interface Context {
        terminationPolicy: Policy;
    }
}
export class TerminationPolicy extends Service {
    static Config = Config;
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input = {}) { super(ctx, 'terminationPolicy'); this.config = Config(input); }
    evaluate(input: TerminationInput): {
        action: 'continue' | 'pause' | 'stop' | 'fail';
        reason: string;
    } {
        if (input.kind === 'source-end')
            return { action: 'stop', reason: input.reason };
        if (input.kind === 'stop-request')
            return { action: input.mode === 'abort' ? 'fail' : 'stop', reason: input.reason };
        if (input.kind === 'source-idle')
            return { action: 'pause', reason: 'idle' };
        return input.result.status === 'incomplete' ? { action: this.config.onIncomplete, reason: 'incomplete' } : { action: 'continue', reason: input.result.status };
    }
}
export default TerminationPolicy;
