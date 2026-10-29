import {Context,Service} from '@society0/host';
import Schema from '@society0/schemastery';
import {isDeepStrictEqual} from 'node:util';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
import '../attempts/index.js';import '../sqlite-history/index.js';import '../artifacts-local/index.js';
import type {AttemptIdentity,AttemptRecord} from '../attempts/ledger.js';
export type OperationAddress=AttemptIdentity;
export type RecordedResult=AttemptRecord & {status:'settled'};
export interface Divergence {readonly equal:boolean;readonly actual:unknown;readonly expected:unknown}
export const Config=Schema.object({mode:Schema.union(['record','replay','verify']).default('record'),onMissingExternalResult:Schema.const('fail').default('fail')});
export const metadata:PluginMetadata={title:'replay',summary:'Read authoritative recorded external results without network execution',configVersion:1,scope:'run',allowedScopes:['run'],hostAbi:'society0/host/1',configPolicy:[{path:'/mode',mode:'new-run',affectsResults:true},{path:'/onMissingExternalResult',mode:'boundary',affectsResults:true}]};
export const api:ApiEntry={exportPath:'./replay',provides:[{key:'replay',protocol:'replay',major:1,capabilities:[]}],requires:['history','attempts','artifacts'].map(key=>({service:{key,protocol:key,major:1,capabilities:[]},optional:false,phase:'module'}))};
declare module '@society0/host' {interface Context {replay:Replay}}
export class Replay extends Service {
 static Config=Config;static inject=['history','attempts','artifacts'];
 constructor(ctx:Context,readonly config:ReturnType<typeof Config>){super(ctx,'replay')}
 async lookup(address:OperationAddress):Promise<RecordedResult>{
  const result=await this.ctx.attempts.probe(address);
  if(result.status==='absent')throw Error('REPLAY_MISSING '+address.operationId);
  if(result.status==='unknown')throw Error('REPLAY_AUTHORITY_UNKNOWN '+result.reason);
  if(result.record.status!=='settled')throw Error('REPLAY_'+result.record.status.toUpperCase()+' '+address.operationId);
  return structuredClone(result.record) as RecordedResult;
 }
 compare(actual:unknown,expected:unknown):Divergence{return {equal:isDeepStrictEqual(actual,expected),actual,expected}}
 readArtifact(ref:Parameters<Context['attempts']['readArtifact']>[0],range?:{offset:string;length:number}){return this.ctx.attempts.readArtifact(ref,range)}
}
export default Replay;
