import {Context,Service} from '@society0/host';
import Schema from '@society0/schemastery';
import {isDeepStrictEqual} from 'node:util';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
import '../session-log/index.js';
import {unpackText,type SessionKey,type OperationAttempt,type SessionEvent,type PackedText} from '../session-log/store.js';
/** 某个 session 里某 operation 的第 attempt 次物理尝试。 */
export interface OperationAddress extends OperationAttempt {readonly session:SessionKey}
export type RecordedResult=SessionEvent;
export interface Divergence {readonly equal:boolean;readonly actual:unknown;readonly expected:unknown}
export const Config=Schema.object({onMissingExternalResult:Schema.const('fail').default('fail')});
export const metadata:PluginMetadata={title:'replay',summary:'Read recorded external results from session logs without network execution',configVersion:1,scope:'run',allowedScopes:['run'],hostAbi:'society0/host/1',configPolicy:[{path:'/onMissingExternalResult',mode:'boundary',affectsResults:true}]};
export const api:ApiEntry={exportPath:'./replay',provides:[{key:'replay',protocol:'replay',major:1,capabilities:[]}],requires:['sessions'].map(key=>({service:{key,protocol:key,major:1,capabilities:[]},optional:false,phase:'module'}))};
declare module '@society0/host' {interface Context {replay:Replay}}
export class Replay extends Service {
 static Config=Config;static inject=['sessions'];
 constructor(ctx:Context,readonly config:ReturnType<typeof Config>){super(ctx,'replay')}
 /** 结论事件（response、attempt/failed 或外部调用结果）；没有记录即失败，不回退到网络。 */
 async lookup(address:OperationAddress):Promise<RecordedResult>{
  const event=await this.ctx.sessions.find(address.session,address);
  if(!event)throw Error('REPLAY_MISSING '+address.operationId);
  return event;
 }
 compare(actual:unknown,expected:unknown):Divergence{return {equal:isDeepStrictEqual(actual,expected),actual,expected}}
 raw(value:PackedText){return unpackText(value)}
}
export default Replay;
