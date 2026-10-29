import {registerSingle} from '../model-providers/lifecycle.js';
import {Context,Service} from '@society0/host';
import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
export const Config=Schema.object({});
export const metadata:PluginMetadata={"title": "model-providers", "summary": "model-providers", "configVersion": 1, "configPolicy": [], "scope": "run", "hostAbi": "society0/host/1"};
export const api:ApiEntry={"exportPath": "./model-providers", "provides": [{"key": "modelProviders", "protocol": "modelProviders", "major": 1, "capabilities": []}], "requires": [{"service": {"key": "slots", "protocol": "slots", "major": 1, "capabilities": []}, "optional": false, "phase": "module"}]};
import {slotKey} from '@society0/host/slots';
import type {ModelProvider,ModelSelectionRequest,ResolvedModelSelection} from '../contracts/inference.js';
import type {Invocation,Lease} from '../contracts/common.js';
const key=(name:string)=>slotKey<ModelProvider>('core/model-providers/'+name);
declare module '@society0/host'{interface Context{modelProviders:ModelProviders}}
export class ModelProviders extends Service {
 static Config=Config;static inject=['slots'];constructor(ctx:Context){super(ctx,'modelProviders')}
 register(name:string,value:ModelProvider){if(!name||typeof value?.prepare!=='function')throw Error('REGISTRATION_INVALID');return registerSingle(this.ctx,key(name),name,value)}
 async acquire(name:string,context:{generation:string;signal?:AbortSignal}={generation:'selection'}):Promise<Lease<ModelProvider>>{const selected=await this.ctx.slots.acquire(key(name),context);return {value:selected.items[0]!.value,release:()=>selected.release()}}
 async prepare(request:ModelSelectionRequest,context:Invocation):Promise<Lease<ResolvedModelSelection>>{const provider=await this.acquire(request.provider,context);try{const selected=await provider.value.prepare(request,context);let released=false;return {value:selected.value,release:async()=>{if(released)return;released=true;try{await selected.release()}finally{await provider.release()}}}}catch(error){await provider.release();throw error}}
}
export default ModelProviders;
