import {chunks} from '../results/payload.js';
import type {CredentialStore} from '@earendil-works/pi-ai';
import type {EmbeddingProvider,EmbeddingIdentity} from '../contracts/cognition.js';
import type {AttemptIdentity,AttemptLedger} from '../attempts/ledger.js';
export interface HttpEmbeddingOptions {identity:EmbeddingIdentity;url:string;credentialProvider:string;timeoutMs:number;maxResponseBytes:number;maxRequestBytes:number;fetch?:typeof fetch}
export class HttpEmbeddingProvider implements EmbeddingProvider {
 readonly identity:EmbeddingIdentity;private closed=false;
 constructor(private config:HttpEmbeddingOptions,private credentials:CredentialStore|undefined,private ledger:Pick<AttemptLedger,'stageEvidence'|'evidenceChunkBytes'>){this.identity=Object.freeze({...config.identity});const url=new URL(config.url);if(!['http:','https:'].includes(url.protocol))throw Error('EMBEDDING_URL')}
 private async headers(signal:AbortSignal){const credential=await this.credentials?.read(this.config.credentialProvider,{signal});if(this.credentials&&credential?.type!=='api_key')throw Error('EMBEDDING_CREDENTIAL');const headers:Record<string,string>={'content-type':'application/json'};if(credential?.type==='api_key'&&credential.key)headers.authorization='Bearer '+credential.key;return headers}
 private body(texts:readonly string[]){const body=JSON.stringify({model:this.identity.model,input:texts,dimensions:this.identity.dimensions});if(Buffer.byteLength(body)>this.config.maxRequestBytes)throw Error('EMBEDDING_REQUEST_BYTES');return body}
 private archive(bytes:Uint8Array){return this.ledger.stageEvidence(chunks(bytes,this.ledger.evidenceChunkBytes),'application/json')}
 private async capture(response:Response){let size=0;const chunks:Uint8Array[]=[];try{if(response.body)for await(const chunk of response.body){size+=chunk.byteLength;if(size>this.config.maxResponseBytes)throw Error('EMBEDDING_RESPONSE_BYTES');chunks.push(chunk)}}catch(error){const original=await this.archive(Buffer.concat(chunks));throw Object.assign(error as Error,{artifacts:[original]})}return Buffer.concat(chunks)}
 private decode(response:Response,bytes:Buffer){if(!response.ok)throw Error('EMBEDDING_HTTP_STATUS '+response.status);const data=JSON.parse(bytes.toString('utf8'));if(!Array.isArray(data.data))throw Error('EMBEDDING_HTTP_SHAPE');return {items:data.data.map((item:any)=>({itemIndex:item.index,vector:item.embedding})),usage:data.usage??null}}
 async embed(texts:readonly string[],attempt:AttemptIdentity,signal:AbortSignal){if(this.closed)throw Error('EMBEDDING_PROVIDER_CLOSED');const headers=await this.headers(signal),body=this.body(texts);const response=await(this.config.fetch??fetch)(this.config.url,{method:'POST',body,headers,signal:AbortSignal.any([signal,AbortSignal.timeout(this.config.timeoutMs)]),redirect:'error'}),bytes=await this.capture(response),original=await this.archive(bytes);try{return {...this.decode(response,bytes),original}}catch(error){throw Object.assign(error as Error,{artifacts:[original]})}}
 async close(){this.closed=true}
}
