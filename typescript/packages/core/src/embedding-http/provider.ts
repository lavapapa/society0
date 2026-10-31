import {chunks} from '../results/payload.js';
import type {CredentialStore} from '@earendil-works/pi-ai';
import type {EmbeddingProvider,EmbeddingIdentity} from '../contracts/cognition.js';
import type {AttemptIdentity,AttemptLedger} from '../attempts/ledger.js';
import {TransportObserver,type PhysicalStatus} from '../attempts/transport.js';
export interface HttpEmbeddingOptions {identity:EmbeddingIdentity;url:string;credentialProvider:string;timeoutMs:number;maxResponseBytes:number;maxRequestBytes:number;fetch?:typeof fetch}
export class HttpEmbeddingProvider implements EmbeddingProvider {
 readonly identity:EmbeddingIdentity;private closed=false;
 constructor(private config:HttpEmbeddingOptions,private credentials:CredentialStore|undefined,private ledger:Pick<AttemptLedger,'stageEvidence'|'evidenceChunkBytes'>){this.identity=Object.freeze({...config.identity});const url=new URL(config.url);if(!['http:','https:'].includes(url.protocol))throw Error('EMBEDDING_URL')}
 private async headers(signal:AbortSignal){return this.authorized(await this.credential(signal))}
 private async credential(signal:AbortSignal){const credential=await this.credentials?.read(this.config.credentialProvider,{signal});this.validCredential(credential);return credential}
 private validCredential(credential:Awaited<ReturnType<CredentialStore['read']>>){if(!this.credentials)return;if(credential?.type!=='api_key')throw Error('EMBEDDING_CREDENTIAL')}
 private authorized(credential:Awaited<ReturnType<CredentialStore['read']>>):Record<string,string>{const key=this.apiKey(credential),headers:Record<string,string>={'content-type':'application/json'};if(key)headers.authorization='Bearer '+key;return headers}
 private apiKey(credential:Awaited<ReturnType<CredentialStore['read']>>){return credential?.type==='api_key'?credential.key:undefined}
 private body(texts:readonly string[]){const body=JSON.stringify({model:this.identity.model,input:texts,dimensions:this.identity.dimensions});if(Buffer.byteLength(body)>this.config.maxRequestBytes)throw Error('EMBEDDING_REQUEST_BYTES');return body}
 private archive(bytes:Uint8Array){return this.ledger.stageEvidence(chunks(bytes,this.ledger.evidenceChunkBytes),'application/json')}
 private async capture(response:Response){const chunks:Uint8Array[]=[];try{await this.drain(response,chunks)}catch(error){throw await this.partial(error,chunks)}return Buffer.concat(chunks)}
 private async drain(response:Response,chunks:Uint8Array[]){let size=0;if(!response.body)return;for await(const chunk of response.body){size+=chunk.byteLength;if(size>this.config.maxResponseBytes)throw Error('EMBEDDING_RESPONSE_BYTES');chunks.push(chunk)}}
 private async partial(error:unknown,chunks:Uint8Array[]){const original=await this.archive(Buffer.concat(chunks));return Object.assign(error as Error,{artifacts:[original]})}
 private decode(response:Response,bytes:Buffer){if(!response.ok)throw Error('EMBEDDING_HTTP_STATUS '+response.status);const data=JSON.parse(bytes.toString('utf8'));if(!Array.isArray(data.data))throw Error('EMBEDDING_HTTP_SHAPE');return {items:data.data.map((item:any)=>({itemIndex:item.index,vector:item.embedding})),usage:data.usage??null}}
 async embed(texts:readonly string[],attempt:AttemptIdentity,signal:AbortSignal){if(this.closed)throw Error('EMBEDDING_PROVIDER_CLOSED');const observer=new TransportObserver(this.config.url);
  try{const headers=await this.headers(signal),body=this.body(texts);const response=await observer.fetch(this.config.fetch??fetch,this.config.maxResponseBytes,'EMBEDDING_RESPONSE_BYTES')(this.config.url,{method:'POST',body,headers,signal:AbortSignal.any([signal,AbortSignal.timeout(this.config.timeoutMs)])}),bytes=await this.capture(response),original=await this.archive(bytes);
   try{return {...this.decode(response,bytes),original}}catch(error){throw Object.assign(error as Error,{artifacts:[original],physical:response.ok?'response':observer.classify(false)})}
  }catch(error){throw this.failed(error,observer)}}
 private failed(error:unknown,observer:TransportObserver){const physical=(error as {physical?:PhysicalStatus}).physical??(observer.calls===0?'not_dispatched':observer.classify(false));return Object.assign(error as Error,{physical,httpStatus:observer.httpStatus})}
 async close(){this.closed=true}
}
