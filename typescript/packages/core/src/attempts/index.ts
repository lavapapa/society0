import '../sqlite-history/index.js';
import {Context,Service} from '@society0/cordis';import '@society0/host/resources';import '../artifacts-local/index.js';
import {AttemptLedger,type AttemptOptions,type AttemptIdentity} from './ledger.js';import {LocalArtifacts,type ArtifactRef} from '../artifacts-local/store.js';import {Config} from './config.js';
export {Config,metadata,api} from './config.js';export * from './ledger.js';
declare module '@society0/cordis' {interface Context {attempts:Attempts}}
export class Attempts extends Service {static Config=Config;static inject=['resources','artifacts','history'];private ledger!:AttemptLedger;private closeOwned!:()=>Promise<void>;constructor(ctx:Context,private options:AttemptOptions){super(ctx,'attempts')}
 async [Service.init](){const resource=await this.ctx.resources.open(async()=>{const value=await AttemptLedger.open(this.options,this.ctx.artifacts);return {value,close:()=>value.close()}},'attempts.authority');this.ledger=resource.value;this.closeOwned=resource.close;this.ctx.history.bindAttempts(resource.value)}
 initialize(){return this.ledger.initialize()}
 verifyAuthority(){return this.ledger.verifyAuthority()}
 authority(){return this.ledger.authority()}
 claim(identity:AttemptIdentity,request:unknown,artifacts:readonly ArtifactRef[]=[]){return this.ledger.claim(identity,request,artifacts)}
 settle(identity:AttemptIdentity,result:unknown,artifacts:readonly ArtifactRef[]=[]){return this.ledger.settle(identity,result,artifacts)}
 markUnknown(identity:AttemptIdentity,reason:string,artifacts:readonly ArtifactRef[]=[],result?:unknown){return this.ledger.markUnknown(identity,reason,artifacts,result)}
 probe(identity:AttemptIdentity){return this.ledger.probe(identity)}
 get maxRecordBytes(){return this.ledger.maxRecordBytes}
 get evidenceChunkBytes(){return this.ledger.evidenceChunkBytes}
 stageEvidence(source:AsyncIterable<Uint8Array>,mediaType:string,options?:{signal?:AbortSignal}){return this.ledger.stageEvidence(source,mediaType,options)}
 readArtifact(ref:ArtifactRef,range?:{offset:string;length:number}){return this.ledger.readArtifact(ref,range)}
 materialize(ref:ArtifactRef){return this.ledger.materialize(ref)}
 snapshot(target:string,artifacts:LocalArtifacts){return this.ledger.snapshot(target,artifacts)}
 diagnostics(){return this.ledger.diagnostics()}
 close(){return this.closeOwned()}
}
export default Attempts;
