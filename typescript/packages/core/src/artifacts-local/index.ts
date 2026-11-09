import {Config} from './config.js';
export {Config,metadata,api} from './config.js';
import {Context,Service} from '@society0/cordis';
import '@society0/host/resources';
import {LocalArtifacts,artifactsMigration,type ArtifactOptions,type ArtifactRef} from './store.js';
import type {SqliteTransaction} from '../sqlite/contracts.js';
export * from './store.js';
export const migrations = [artifactsMigration];
declare module '@society0/cordis' {interface Context {artifacts:ArtifactsLocal}}
export class ArtifactsLocal extends Service {
 static Config=Config;static inject=['resources'];private store!:LocalArtifacts;private closeOwned!:()=>Promise<void>;
 constructor(ctx:Context,private config:ArtifactOptions){super(ctx,'artifacts')}
 async [Service.init](){const resource=await this.ctx.resources.open(async()=>{const value=await LocalArtifacts.open(this.config);return {value,close:()=>value.close()}},'artifacts.local');this.store=resource.value;this.closeOwned=resource.close}
 get directory(){return this.store.directory}
 get chunkBytes(){return this.store.chunkBytes}
 get maxObjectBytes(){return this.store.maxObjectBytes}
 stage(source:AsyncIterable<Uint8Array>,mediaType:string,options?:{signal?:AbortSignal}){return this.store.stage(source,mediaType,options)}
 read(ref:ArtifactRef,range?:{offset:string;length:number},options?:{signal?:AbortSignal}){return this.store.read(ref,range,options)}
 localFile(ref:ArtifactRef){return this.store.localFile(ref)}
 inspect(id:string){return this.store.inspect(id)}
 reference(tx:SqliteTransaction,ref:ArtifactRef){return this.store.reference(tx,ref)}
 copyTo(ref:ArtifactRef,target:LocalArtifacts,options?:{signal?:AbortSignal}){return this.store.copyTo(ref,target,options)}
 materializeFrom(ref:ArtifactRef,source:LocalArtifacts){return this.store.materializeFrom(ref,source)}
 diagnostics(){return this.store.diagnostics()}
 close(){return this.closeOwned()}
}
export default ArtifactsLocal;
