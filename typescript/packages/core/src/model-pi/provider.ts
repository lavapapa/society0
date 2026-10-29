import type {Models} from '@earendil-works/pi-ai';
import type {AttemptLedger,AttemptIdentity} from '../attempts/ledger.js';
import type {ArtifactRef} from '../artifacts-local/store.js';
import type {Invocation,Lease} from '../contracts/common.js';
import type {ModelProvider,ModelSelectionRequest,ResolvedModelSelection} from '../contracts/inference.js';
import {PreparedPiSelection} from './selection.js';
export type Ledger=Pick<AttemptLedger,'claim'|'settle'|'stageEvidence'|'authority'|'evidenceChunkBytes'>&{markUnknown(identity:AttemptIdentity,reason:string,artifacts?:readonly ArtifactRef[]):Promise<void>};
export interface PiProviderOptions {runId:string;providerId:string;registrationId?:string;maxEventBytes:number;maxRawBytes:number;maxRequestBytes:number;maxPendingEvents?:number;maxPendingBytes?:number;options:ResolvedModelSelection['options'];fetch?:typeof fetch;timeoutMs?:number;runPhysical?:(body:(signal:AbortSignal)=>Promise<void>,bytes:number)=>Promise<void>}
export class PiModelProvider implements ModelProvider {
 private active=new Set<Promise<unknown>>();private closing=false;private peak={events:0,bytes:0};
 constructor(private models:Models,private ledger:Ledger,private options:PiProviderOptions){}
 private track(work:Promise<unknown>){this.active.add(work);void work.then(()=>this.active.delete(work),()=>this.active.delete(work))}
 private measure(peak:{events:number;bytes:number}){this.peak.events=Math.max(this.peak.events,peak.events);this.peak.bytes=Math.max(this.peak.bytes,peak.bytes)}
 async prepare(request:ModelSelectionRequest,context:Invocation):Promise<Lease<ResolvedModelSelection>>{if(this.closing)throw Error('MODEL_PROVIDER_CLOSED');if(context.runId!==this.options.runId)throw Error('MODEL_RUN_MISMATCH');const model=this.models.getModel(this.options.providerId,request.model);if(!model)throw Error('MODEL_UNAVAILABLE');return new PreparedPiSelection(this.models,this.ledger,this.options,request,context,model,work=>this.track(work),()=>this.closing,peak=>this.measure(peak)).lease()}
 diagnostics(){return {activeWork:this.active.size,peakEvents:this.peak.events,peakBytes:this.peak.bytes}}
 async close(){this.closing=true;while(this.active.size)await Promise.allSettled([...this.active])}
}
