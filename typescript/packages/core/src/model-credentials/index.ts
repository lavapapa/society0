import {Context,Service} from '@society0/host';import Schema from '@society0/schemastery';import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';import {slotKey} from '@society0/host/slots';import '@society0/host/resources';import type {CredentialStore,Credential,CredentialInfo,AuthOperationOptions} from '@earendil-works/pi-ai';import type {Lease} from '../contracts/common.js';import {FileCredentialStore} from './store.js';
export const Config=Schema.object({stores:Schema.array(Schema.object({reference:Schema.string().required(),path:Schema.string().required()})).default([])});
export const metadata:PluginMetadata={title:'model-credentials',summary:'机器凭据与世界资料分离，修改租约覆盖原生SDK刷新',configVersion:1,configPolicy:[{path:'/stores',mode:'recreate',affectsResults:false,secret:false}],scope:'host',hostAbi:'society0/host/1'};
export const api:ApiEntry={exportPath:'./model-credentials',provides:[{key:'modelCredentials',protocol:'modelCredentials',major:1,capabilities:[]}],requires:['slots','resources'].map(key=>({service:{key,protocol:key,major:1,capabilities:[]},optional:false,phase:'module' as const}))};
const key=(reference:string)=>slotKey<CredentialStore>('core/model-credentials/'+reference);
declare module '@society0/host'{interface Context{modelCredentials:ModelCredentials}}
export class ModelCredentials extends Service {
 static Config=Config;static inject=['slots','resources'];private closing=false;private leases=new Set<Promise<void>>();private stores:FileCredentialStore[]=[];constructor(ctx:Context,private config:{stores?:{reference:string;path:string}[]}={}){super(ctx,'modelCredentials')}
 [Service.init](){this.ctx.resources.use('model-credentials',async()=>{this.closing=true;await Promise.all([...this.leases]);await Promise.all(this.stores.map(store=>store.close()))});const paths=new Set<string>();for(const item of this.config.stores??[]){const store=new FileCredentialStore(item.path);if(paths.has(store.path))throw Error('CREDENTIAL_STORE_PATH_DUPLICATE');paths.add(store.path);this.stores.push(store);this.register(item.reference,store)}}
 register(reference:string,store:CredentialStore){if(this.closing)throw Error('CREDENTIALS_CLOSED');if(!reference)throw Error('CREDENTIAL_REFERENCE');const slot=key(reference),undeclare=this.ctx.slots.declare({key:slot,strategy:'single',required:true,validate:value=>value as CredentialStore});let withdraw:()=>void|Promise<void>;try{withdraw=this.ctx.slots.contribute(slot,{id:reference,value:store})}catch(error){void undeclare();throw error}return this.ctx.effect(()=>async()=>{await Promise.all([withdraw(),undeclare()])})}
 async acquire(reference:string):Promise<Lease<CredentialStore>>{if(this.closing)throw Error('CREDENTIALS_CLOSED');const done=Promise.withResolvers<void>();this.leases.add(done.promise);const finish=()=>{this.leases.delete(done.promise);done.resolve()};try{const selected=await this.ctx.slots.acquire(key(reference),{generation:'machine-credentials'});let released=false;return {value:selected.items[0]!.value,release:async()=>{if(released)return;released=true;try{await selected.release()}finally{finish()}}}}catch(error){finish();throw error}}

}
export class MemoryCredentialStore implements CredentialStore {
    private readonly values: Record<string, Credential>;
    private closing = false;
    constructor(values: Readonly<Record<string, Credential>> = {}) { this.values = structuredClone(values) as Record<string, Credential> }
    private assert() { if (this.closing) throw Error('CREDENTIAL_STORE_CLOSED') }
    async read(providerId: string, options?: AuthOperationOptions) { this.assert(); options?.signal?.throwIfAborted(); return structuredClone(this.values[providerId]) }
    async list(options?: AuthOperationOptions): Promise<readonly CredentialInfo[]> { this.assert(); options?.signal?.throwIfAborted(); return Object.entries(this.values).map(([providerId, value]) => ({ providerId, type: value.type })) }
    async modify(providerId: string, fn: (current: Credential | undefined) => Promise<Credential | undefined>, options?: AuthOperationOptions) { this.assert(); options?.signal?.throwIfAborted(); const next = await fn(structuredClone(this.values[providerId])); if (next !== undefined) this.values[providerId] = next; return structuredClone(this.values[providerId]) }
    async delete(providerId: string, options?: AuthOperationOptions) { this.assert(); options?.signal?.throwIfAborted(); delete this.values[providerId] }
    async close() { this.closing = true }
}
export default ModelCredentials;
