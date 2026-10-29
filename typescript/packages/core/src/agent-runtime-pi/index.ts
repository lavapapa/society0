import {Context,Service} from '@society0/host';
import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
export const Config=Schema.object({});
export const metadata:PluginMetadata={"title": "agent-runtime-pi", "summary": "agent-runtime-pi", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1"};
export const api:ApiEntry={"exportPath": "./agent-runtime-pi", "provides": [{"key":"agentRuntime.pi","protocol":"agentRuntime.pi","major":1,"capabilities":[]}], "requires": [{"service": {"key": "agentRuntimes", "protocol": "agentRuntimes", "major": 1, "capabilities": []}, "optional": false, "phase": "module"}, {"service": {"key": "slots", "protocol": "slots", "major": 1, "capabilities": []}, "optional": false, "phase": "module"}, {"service": {"key": "resources", "protocol": "resources", "major": 1, "capabilities": []}, "optional": false, "phase": "module"}]};
import '../agent-runtimes/index.js';
import '@society0/host/resources';
import {PiRuntime} from './runtime.js';
import {INFERENCE_REQUEST,INFERENCE_CONTEXT,INFERENCE_TOOL_POLICY,INFERENCE_COMPLETION,INFERENCE_OBSERVER,type InferenceExtensions} from '../contracts/inference.js';
import type {Invocation} from '../contracts/common.js';
export {PiRuntime};export {OrderedInferenceSink} from './sink.js';
export async function acquireExtensions(ctx:Context,context:Invocation):Promise<InferenceExtensions & {release():Promise<void>}>{const keys=[INFERENCE_REQUEST,INFERENCE_CONTEXT,INFERENCE_TOOL_POLICY,INFERENCE_COMPLETION,INFERENCE_OBSERVER];const leases:any[]=[];try{for(const key of keys)leases.push(await ctx.slots.acquire(key as any,context));return {request:leases[0],context:leases[1],toolPolicy:leases[2],completion:leases[3],observer:leases[4],release:async()=>{await Promise.all(leases.map(lease=>lease.release()))}}}catch(error){await Promise.all(leases.map(lease=>lease.release()));throw error}}
export default class AgentRuntimePi extends Service {
 static Config=Config;static inject=['agentRuntimes','slots','resources'];constructor(ctx:Context){super(ctx,'agentRuntime.pi')}
 [Service.init](){const runtime=new PiRuntime();this.ctx.resources.use('agent-runtime.pi',()=>runtime.drain('provider closing'));this.ctx.agentRuntimes.register('pi/v1',runtime)}
}
