import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ resultTag: Schema.string().default('passive') });
export const metadata: PluginMetadata = { "title": "rule-passive", "summary": "向规则驱动声明的槽贡献显式passive规则，适合空动作基线，研究行为另由策略插件替换。", "configVersion": 1, "configPolicy": [{ "path": "/resultTag", "mode": "boundary", "affectsResults": true, "secret": false }], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./rule-passive", "provides": [], "requires": [{ "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import type { Context } from '@society0/cordis';
import { ruleHandlerSlot } from '../driver-rule/index.js';
export default { Config, inject: ['slots'], apply(ctx: Context, config: {
        resultTag: string;
    }) { ctx.slots.contribute(ruleHandlerSlot, { id: 'rule/passive/v1', value: { async activate(input) { return { status: 'completed', value: { tag: config.resultTag, input }, evidence: { policy: 'rule/passive/v1' }, requiredOperations: [] }; } } }); } };
