import {Context,Service} from '@society0/host';
import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
export const Config=Schema.object({});
export const metadata:PluginMetadata={"title": "inference-defaults", "summary": "inference-defaults", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1"};
export const api:ApiEntry={"exportPath": "./inference-defaults", "provides": [{"key":"inference.defaults","protocol":"inference.defaults","major":1,"capabilities":[]}], "requires": [{"service":{"key":"agentRuntimes","protocol":"agentRuntimes","major":1,"capabilities":[]},"optional":false,"phase":"module"},{"service": {"key": "slots", "protocol": "slots", "major": 1, "capabilities": []}, "optional": false, "phase": "module"}]};
import {INFERENCE_COMPLETION,type InferenceCompletionPolicy} from '../contracts/inference.js';
import {completedActions} from '../agent-runtime-pi/runtime.js';
export const completion:InferenceCompletionPolicy={async evaluate(facts){if(facts.stopReason!=='success')return {status:'incomplete',reason:facts.stopReason};const result=await completedActions(facts.requiredActions,facts.actionReceipts);return result.satisfied?{status:'completed',reason:'success'}:{status:'incomplete',reason:'required_action_failed'}}};
export default class InferenceDefaults extends Service {static Config=Config;static inject=['slots','agentRuntimes'];constructor(ctx:Context){super(ctx,'inference.defaults')}[Service.init](){this.ctx.slots.contribute(INFERENCE_COMPLETION,{id:'inference/default/v1',value:completion})}}
