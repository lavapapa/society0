import {appendFile,opendir,readFile,stat} from 'node:fs/promises';
import {join,resolve,sep} from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import type {CompletePoint} from '../sqlite-bootstrap/index.js';
import type {AuthorityRef} from '../attempts/ledger.js';
import type {ArtifactRef} from '../artifacts-local/store.js';
import {exists,fault,id,jsonFile} from '../artifacts-local/files.js';
export interface FileRef {path:string;bytes:string}
export interface Segment extends FileRef {fromRevision:string;toRevision:string;rawBytes:string;format?:string;changesets?:number}
export interface PointRecord extends CompletePoint {version:1;operationId:string;planId:string;parentId?:string;root?:FileRef;segments:Segment[];artifacts:ArtifactRef[];requiredOperations:readonly string[];lineage?:{sourceRunId:string;sourcePointId:string};evidence?:{authority:AuthorityRef;snapshot?:FileRef;artifacts?:ArtifactRef[]}}
interface Materialization {pointId:string;root:FileRef;artifacts:ArtifactRef[]}
interface ReadOptions {directory:string;runId:string;maxManifestBytes:number;maxDependencies:number}
/** 完整点文件读取与依赖核实，供live history和目标probe共同使用。 */
export class PointFiles {
 private indexed=false;private rebuild=false;private indexing?:Promise<void>;private byOperation=new Map<string,string>();private latestPoint?:PointRecord;private recordsRead=0;private readonly indexPath:string;
 async assertCapacity(){await this.ensureIndex()}
 async appendIndex(point:PointRecord){try{await appendFile(this.indexPath,JSON.stringify({operationId:point.operationId,id:point.id,sequence:point.sequence})+'\n')}catch{this.indexed=false;this.rebuild=true}}
 invalidate(){this.indexed=false;this.rebuild=true}
 published(point:PointRecord){this.byOperation.set(point.operationId,point.id);if(!this.latestPoint||BigInt(point.sequence)>BigInt(this.latestPoint.sequence))this.latestPoint=point}
 diagnostics(){return {pointRecordsRead:this.recordsRead,indexEntries:this.byOperation.size}}
 private ensureIndex(){if(this.indexed)return Promise.resolve();return this.indexing??=(async()=>{this.byOperation.clear();this.latestPoint=undefined;let indexed=false;try{if(this.rebuild)throw Error('rebuild');const lines=(await readFile(this.indexPath,'utf8')).split('\n');let last:{operationId:string;id:string;sequence:string}|undefined;for(const line of lines){if(!line)continue;try{last=JSON.parse(line)}catch{last=undefined;break}if(!last||typeof last.operationId!=='string'||typeof last.id!=='string'||typeof last.sequence!=='string'){last=undefined;break}this.byOperation.set(last.operationId,last.id)}if(last){const tail=await this.record(last.id);if(tail.sequence===last.sequence){this.latestPoint=tail;indexed=true}}}catch{}if(!indexed){this.byOperation.clear();this.latestPoint=undefined;for await(const point of this.records())this.published(point)}this.rebuild=false;this.indexed=true})().finally(()=>{this.indexing=undefined})}
 constructor(readonly options:ReadOptions,private inspectArtifact:(id:string)=>Promise<ArtifactRef>){this.indexPath=join(options.directory,'history','points.index')}
 file(ref:FileRef){const base=resolve(this.options.directory),path=resolve(base,ref.path);if(!path.startsWith(base+sep))throw fault('HISTORY_PATH');return path}
 pointPath(pointId:string){return join(this.options.directory,'history','points',id(pointId)+'.json')}
 async record(pointId:string){this.recordsRead++;const point=await jsonFile<PointRecord>(this.pointPath(pointId),this.options.maxManifestBytes);this.identity(point,pointId);this.shape(point);return point}
 private identity(point:PointRecord,pointId:string){if(point.version!==1||point.id!==pointId||point.runId!==this.options.runId)throw fault('HISTORY_DESCRIPTOR')}
 private shape(point:PointRecord){if(!Array.isArray(point.segments)||!Array.isArray(point.artifacts))throw fault('HISTORY_DESCRIPTOR');if(!/^(0|[1-9][0-9]*)$/.test(point.revision)||!/^\d+$/.test(point.sequence))throw fault('HISTORY_DESCRIPTOR')}
 async* records(){for await(const entry of await opendir(join(this.options.directory,'history','points'))){if(!entry.isFile()||!entry.name.endsWith('.json'))continue;yield await this.record(entry.name.slice(0,-5))}}
 async latest(){await this.ensureIndex();return this.latestPoint}
 async findOperation(operationId:string){await this.ensureIndex();const pointId=this.byOperation.get(operationId);if(pointId)return this.record(pointId)}
 async checkFile(ref:FileRef){if((await stat(this.file(ref))).size!==Number(ref.bytes))throw fault('HISTORY_FILE_SIZE')}
 private async base(point:PointRecord):Promise<Materialization|undefined>{const path=join(this.options.directory,'history','materializations',id(point.id)+'.json');if(await exists(path)){const material=await jsonFile<Materialization>(path,this.options.maxManifestBytes);if(material.pointId!==point.id)throw fault('HISTORY_MATERIALIZATION');return material}if(point.root)return {pointId:point.id,root:point.root,artifacts:point.artifacts}}
 private async ancestry(pointId:string){const after:PointRecord[]=[],seen=new Set<string>();let point=await this.record(pointId),bytes=0;while(true){if(seen.has(point.id)||seen.size>=this.options.maxDependencies)throw fault('HISTORY_CHAIN');seen.add(point.id);bytes+=Buffer.byteLength(JSON.stringify(point));if(bytes>16*this.options.maxManifestBytes)throw fault('HISTORY_CHAIN_BUDGET');const base=await this.base(point);if(base)return {base,baseRevision:point.revision,after:after.reverse(),pointIds:[...seen],basePoint:point};if(!point.parentId)throw fault('HISTORY_ROOT_MISSING');after.push(point);point=await this.record(point.parentId)}}
 private async appendSegments(point:PointRecord,revision:string,segments:Segment[]){for(const segment of point.segments){const merged=segment.format==='society0/changeset-container/1';if(segment.fromRevision!==revision||(!merged&&BigInt(segment.toRevision)!==BigInt(revision)+1n)||(merged&&BigInt(segment.toRevision)<=BigInt(revision)))throw fault('HISTORY_SEGMENT_ORDER');await this.checkFile(segment);segments.push(segment);revision=segment.toRevision}if(point.revision!==revision)throw fault('HISTORY_REVISION');return revision}
 private addArtifacts(objects:Map<string,ArtifactRef>,refs:readonly ArtifactRef[]){for(const ref of refs)objects.set(ref.id,ref);if(objects.size>this.options.maxDependencies)throw fault('HISTORY_DEPENDENCY_BUDGET')}
 async checkArtifacts(refs:readonly ArtifactRef[]){for(const ref of refs){const actual=await this.inspectArtifact(ref.id);if(!isDeepStrictEqual(actual,ref))throw fault('HISTORY_ARTIFACT_REF')}}
 private async checkEvidence(point:PointRecord){if(!point.evidence)return;if(point.evidence.snapshot){await this.checkFile(point.evidence.snapshot);await this.checkArtifacts(point.evidence.artifacts??[]);return}if(!await exists(join(point.evidence.authority.directory,'attempts.sqlite')))throw fault('HISTORY_EVIDENCE_UNAVAILABLE')}
 async dependencies(pointId:string){const chain=await this.ancestry(pointId),objects=new Map<string,ArtifactRef>(),segments:Segment[]=[];await this.checkFile(chain.base.root);this.addArtifacts(objects,chain.base.artifacts);let revision=chain.baseRevision;for(const point of chain.after){revision=await this.appendSegments(point,revision,segments);this.addArtifacts(objects,point.artifacts)}if(segments.length>this.options.maxDependencies)throw fault('HISTORY_DEPENDENCY_BUDGET');const artifacts=[...objects.values()];await this.checkArtifacts(artifacts);const point=await this.record(pointId);await this.checkEvidence(point);return {root:chain.base.root,baseRevision:chain.baseRevision,segments,artifacts,point,pointIds:chain.pointIds,points:chain.after}}
}
