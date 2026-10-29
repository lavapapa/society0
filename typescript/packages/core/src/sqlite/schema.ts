import {fault,type SqliteMigration,type SqliteSchemaVersion} from './contracts.js';
export function orderDependencies<T>(entries:readonly T[],id:(entry:T)=>string,after:(entry:T)=>readonly string[]):T[] {
 const byId=new Map<string,T>();for(const entry of entries){const key=id(entry);if(!key||byId.has(key))throw fault('SCHEMA_DUPLICATE',key);byId.set(key,entry)}
 const active=new Set<string>(),done=new Set<string>(),result:T[]=[];
 const visit=(key:string)=>{if(done.has(key))return;if(active.has(key))throw fault('SCHEMA_CYCLE',key);const entry=byId.get(key);if(!entry)throw fault('SCHEMA_MISSING',key);active.add(key);for(const dependency of after(entry))visit(dependency);active.delete(key);done.add(key);result.push(entry)};
 for(const key of byId.keys())visit(key);return result;
}
function migrationStep(owner:string,m:SqliteMigration,starts:Set<number>):void {
 if(!Number.isSafeInteger(m.from)||m.from<0||!Number.isSafeInteger(m.to)||m.to<=m.from||!m.source||!Object.keys(m.source).length||starts.has(m.from))throw fault('SCHEMA_VERSION',owner);
 starts.add(m.from);
}
function migrationChain(owner:string,chain:readonly SqliteMigration[],versions:Map<string,number>):SqliteMigration[] {
 const starts=new Set<number>(),result:SqliteMigration[]=[];
 for(const m of [...chain].sort((a,b)=>a.from-b.from)){
  migrationStep(owner,m,starts);const version=versions.get(owner)??0;
  if(m.to<=version)continue;
  if(version!==m.from)throw fault('SCHEMA_VERSION',owner);
  versions.set(owner,m.to);result.push(m);
 }
 return result;
}
export function planMigrations(migrations:readonly SqliteMigration[],current:readonly SqliteSchemaVersion[]):SqliteMigration[] {
 const versions=new Map(current.map(v=>[v.owner,v.version]));
 const groups=new Map<string,SqliteMigration[]>();
 for(const migration of migrations){const group=groups.get(migration.owner)??[];group.push(migration);groups.set(migration.owner,group)}
 const ordered=orderDependencies([...groups],entry=>entry[0],entry=>[...new Set(entry[1].flatMap(m=>m.after))].filter(owner=>groups.has(owner)||!versions.has(owner)));
 return ordered.flatMap(([owner,chain])=>migrationChain(owner,chain,versions));
}
