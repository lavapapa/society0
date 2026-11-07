import {setup as base, table} from './b4-support.mjs';
import PublicSelf,{canonicalLine} from '../.build/core/dist/public-self/index.js';

export {table};

// 模拟 act 写入的领域表：actor 级库存；version 由写入事务的 revision 提供。
export const selfMigration={owner:'b7-self',from:0,to:1,after:[],source:{consumer:'b7-self'},statements:[{
 sql:`CREATE TABLE b7_stock(actor_id TEXT NOT NULL,id TEXT NOT NULL,qty INTEGER NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(actor_id,id))`
}]};

export const inventoryDecl={
 path:'/self/inventory',title:'库存',description:'本主体库存',
 schema:{type:'object',properties:{id:{type:'string'},qty:{type:'integer'}}},
 key:['id'],sort:[{field:'id'}],shard:{by:'',format:'none'}
};

export async function setup(options={}){
 const run=await base({...options,extra:[selfMigration,...(options.extra??[])]});
 try{
  await run.host.ctx.plugin(PublicSelf,{...options.self});
  return run;
 }catch(error){await run.close();throw error}
}

export function inventoryProvider(){
 return {
  path:'/self/inventory.jsonl',media:'application/jsonl',
  async versions(actorId,view){
   const [row]=await view.query({sql:'SELECT max(version) AS v FROM b7_stock WHERE actor_id=?',parameters:[actorId]},{maxRows:1,maxBytes:1024});
   return [{owner:'b7-stock',version:String(row?.v ?? 0)}];
  },
  async *render(actorId,view,signal){
   const rows=await view.query({sql:'SELECT id,qty FROM b7_stock WHERE actor_id=? ORDER BY id',parameters:[actorId]},{maxRows:1000,maxBytes:1<<20});
   for(const row of rows){
    signal.throwIfAborted();
    yield Buffer.from(canonicalLine(inventoryDecl,{id:String(row.id),qty:Number(row.qty)})+'\n');
   }
  }
 };
}

export function secretProvider(){
 return {
  path:'/self/secret.jsonl',media:'text/plain',
  async visible(actorId){return actorId==='B'},
  async versions(actorId){return [{owner:'b7-secret',version:actorId}]},
  async *render(actorId){yield Buffer.from('secret:'+actorId+'\n')}
 };
}

export async function acquire(run,actorId,momentId,operationId='b7-read'){
 return run.ctx.exploration.acquire(run.scope(actorId,momentId),run.acquisition(actorId,momentId,operationId));
}

export async function readFile(session,path){
 const node=await session.stat(path);
 const handle=await session.open(node);
 const chunks=[];
 try{
  const length=Number(node.rawBytes);
  for await(const chunk of handle.value.read({offset:'0',length,maxBytes:32*1024*1024},new AbortController().signal))chunks.push(Buffer.from(chunk));
 }finally{await handle.release()}
 return {node,text:Buffer.concat(chunks).toString('utf8')};
}

export async function act(run,{actorId,id,qty,publicRow}){
 return run.tx('b7-act-'+actorId+'-'+id,async tx=>{
  const version=BigInt(tx.baseRevision)+1n;
  await tx.execute({sql:`INSERT INTO b7_stock(actor_id,id,qty,version) VALUES(?,?,?,?)
   ON CONFLICT(actor_id,id) DO UPDATE SET qty=excluded.qty,version=excluded.version`,parameters:[actorId,id,qty,version]});
  if(publicRow)await run.ctx.public.writer(tx).upsert(table.path,[publicRow]);
 });
}
