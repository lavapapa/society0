import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ generation: Schema.string().required(), controlSequence: Schema.string().default('0') });
export const metadata: PluginMetadata = { "title": "generation-binding", "summary": "generation-binding", "configVersion": 1, "configPolicy": [{ "path": "/generation", "mode": "new-run", "affectsResults": true }, { "path": "/controlSequence", "mode": "new-run", "affectsResults": true }], "scope": "generation", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./generation-binding", "provides": [], "requires": [{ "service": { "key": "generationViews", "protocol": "generationViews", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "proposalSource", "protocol": "proposalSource", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "runtimeDriver", "protocol": "runtimeDriver", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "logicalClock", "protocol": "logicalClock", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "completionPolicy", "protocol": "completionPolicy", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "checkpointPolicy", "protocol": "checkpointPolicy", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "terminationPolicy", "protocol": "terminationPolicy", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import '../generation-views/index.js';
import '../proposal-sequence/index.js';
import '../runtime-staged/index.js';
import '../clock-ticks/index.js';
import '../completion-strict/index.js';
import '../checkpoint-policy/index.js';
import '../termination/index.js';
export default { Config, inject: ['generationViews', 'proposalSource', 'runtimeDriver', 'logicalClock', 'completionPolicy', 'checkpointPolicy', 'terminationPolicy'], apply(ctx: Context, input: {
        generation: string;
        controlSequence?: string;
    }) { const config = Config(input); ctx.generationViews.register({ ...config, clock: ctx.logicalClock, source: ctx.proposalSource, runtime: ctx.runtimeDriver, completion: ctx.completionPolicy, checkpoint: ctx.checkpointPolicy, termination: ctx.terminationPolicy }); } };
