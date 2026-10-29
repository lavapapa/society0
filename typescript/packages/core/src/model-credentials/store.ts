import type {CredentialStore,Credential,CredentialInfo,AuthOperationOptions} from '@earendil-works/pi-ai';
import {readFile,open,mkdir,rename} from 'node:fs/promises';import {dirname,resolve} from 'node:path';import {randomUUID} from 'node:crypto';
/** 单宿主的机器存储；互斥覆盖SDK传入的完整refresh回调。 */
export class FileCredentialStore implements CredentialStore {
 private tail:Promise<unknown>=Promise.resolve();readonly path:string;private closing=false;
 constructor(path:string){this.path=resolve(path)}
 private async load():Promise<Record<string,Credential>>{let text:string;try{text=await readFile(this.path,'utf8')}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return {};throw error}try{const value=JSON.parse(text);if(!value||Array.isArray(value)||typeof value!=='object')throw Error();return value}catch{throw Error('CREDENTIAL_STORE_FORMAT')}}
 private async save(value:Record<string,Credential>){const directory=dirname(this.path);await mkdir(directory,{recursive:true,mode:0o700});const path=this.path+'.'+randomUUID()+'.next';const file=await open(path,'wx',0o600);try{await file.writeFile(JSON.stringify(value));await file.sync()}finally{await file.close()}await rename(path,this.path);const dir=await open(directory,'r');try{await dir.sync()}finally{await dir.close()}}
 private serial<T>(body:()=>Promise<T>,options?:AuthOperationOptions):Promise<T>{if(this.closing)return Promise.reject(Error('CREDENTIAL_STORE_CLOSED'));const work=this.tail.catch(()=>{}).then(()=>{options?.signal?.throwIfAborted();return body()});this.tail=work;void work.catch(()=>{});return work}
 read(providerId:string,options?:AuthOperationOptions){return this.serial(async()=>structuredClone((await this.load())[providerId]),options)}
 list(options?:AuthOperationOptions):Promise<readonly CredentialInfo[]>{return this.serial(async()=>Object.entries(await this.load()).map(([providerId,value])=>({providerId,type:value.type})),options)}
 modify(providerId:string,fn:(current:Credential|undefined)=>Promise<Credential|undefined>,options?:AuthOperationOptions){return this.serial(async()=>{const values=await this.load();const next=await fn(structuredClone(values[providerId]));if(next!==undefined){values[providerId]=next;await this.save(values)}return structuredClone(values[providerId])},options)}
 delete(providerId:string,options?:AuthOperationOptions){return this.serial(async()=>{const values=await this.load();delete values[providerId];await this.save(values)},options)}
 async close(){this.closing=true;await this.tail}
}
