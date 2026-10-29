import {registerSingle} from '../model-providers/lifecycle.js';
import {Context,Service} from '@society0/host';
import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
export const Config=Schema.object({});
export const metadata:PluginMetadata={"title": "agent-runtimes", "summary": "agent-runtimes", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1"};
export const api:ApiEntry={"exportPath": "./agent-runtimes", "provides": [{"key": "agentRuntimes", "protocol": "agentRuntimes", "major": 1, "capabilities": []}], "requires": [{"service": {"key": "slots", "protocol": "slots", "major": 1, "capabilities": []}, "optional": false, "phase": "module"}]};
import {slotKey} from '@society0/host/slots';import {INFERENCE_REQUEST,INFERENCE_CONTEXT,INFERENCE_TOOL_POLICY,INFERENCE_COMPLETION,INFERENCE_OBSERVER} from '../contracts/inference.js';
import type {AgentRuntime,ModelSelectionRequest,ResolvedModelSelection} from '../contracts/inference.js';
import type {Invocation,Lease} from '../contracts/common.js';
const key=(name:string)=>slotKey<AgentRuntime>('core/agent-runtimes/'+name);
declare module '@society0/host'{interface Context{agentRuntimes:AgentRuntimes}}
export class AgentRuntimes extends Service {
 static Config=Config;static inject=['slots'];constructor(ctx:Context){super(ctx,'agentRuntimes')}
 [Service.init](){for(const key of [INFERENCE_REQUEST,INFERENCE_CONTEXT,INFERENCE_TOOL_POLICY,INFERENCE_COMPLETION,INFERENCE_OBSERVER])this.ctx.slots.declare({key:key as any,strategy:key===INFERENCE_COMPLETION?'single':'ordered',required:key===INFERENCE_COMPLETION,validate:value=>value})}
 register(name:string,value:AgentRuntime){if(!name||typeof value?.run!=='function')throw Error('REGISTRATION_INVALID');return registerSingle(this.ctx,key(name),name,value)}
 async acquire(name:string,context:{generation:string;signal?:AbortSignal}={generation:'selection'}):Promise<Lease<AgentRuntime>>{const selected=await this.ctx.slots.acquire(key(name),context);return {value:selected.items[0]!.value,release:()=>selected.release()}}
}
export default AgentRuntimes;
