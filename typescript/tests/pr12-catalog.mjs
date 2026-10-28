import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
// Schemastery UIDs follow import allocation order; compare the referenced graph.
function canonical(input){
 const schema=JSON.parse(JSON.stringify(input)), ids=new Map(), refs={};
 const visit=uid=>{
  if(ids.has(uid))return ids.get(uid);
  const id=ids.size;ids.set(uid,id);const node=structuredClone(schema.refs[uid]);refs[id]=node;
  for(const key of ['inner','sKey'])if(node[key]!==undefined)node[key]=visit(node[key]);
  if(node.list)node.list=node.list.map(visit);
  if(node.dict)node.dict=Object.fromEntries(Object.entries(node.dict).map(([key,value])=>[key,visit(value)]));
  return id;
 };
 return {uid:visit(schema.uid),refs};
}
const report=[];
for(const name of process.argv.slice(2)){
 const read=async spec=>JSON.parse(await readFile(new URL(import.meta.resolve(spec)),'utf8'));
 const manifest=await read(name+'/package.json');
 const paths=manifest.society.plugin;
 const [meta,schema,api]=await Promise.all([paths.metadata,paths.configSchema,paths.api].map(p=>read(name+p.slice(1))));
 for(const entry of meta.entries){
  assert.ok(Object.hasOwn(manifest.exports,entry.exportPath));
  const module=await import(name+entry.exportPath.slice(1));
  assert.deepEqual(module.metadata,entry.metadata);
  assert.deepEqual(module.api,api.entries.find(e=>e.exportPath===entry.exportPath));
  assert.equal((module.default??module).Config,module.Config);
  assert.deepEqual(canonical(module.Config.toJSON()),canonical(schema.schema.entries.find(e=>e.exportPath===entry.exportPath).schema));
  report.push({package:name,version:manifest.version,exportPath:entry.exportPath,scope:entry.metadata.scope,configPolicy:entry.metadata.configPolicy,status:'matched'});
 }
}
console.log(JSON.stringify({entryCount:report.length,entries:report,modelRequests:0}));
