import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "completion-strict", "summary": "completion-strict", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./completion-strict", "provides": [{ "key": "completionPolicy", "protocol": "completionPolicy", "major": 1, "capabilities": [] }], "requires": [] };
import type { AdvanceResult, TerminationInput, CompletionPolicy as Policy } from '../contracts/runtime.js';
declare module '@society0/host' {
    interface Context {
        completionPolicy: Policy;
    }
}
export class CompletionPolicy extends Service {
    static Config = Config;
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input = {}) { super(ctx, 'completionPolicy'); this.config = Config(input); }
    evaluate(result: AdvanceResult) { return { complete: result.status === 'settled', reason: result.status }; }
}
export default CompletionPolicy;
