import {fixture,activation,request} from './pr10-support.mjs';
import {Exploration} from '../.build/core/dist/exploration/index.js';
import Public,{publicMigration} from '../.build/core/dist/public/index.js';
import MomentViews,{momentViewsMigration} from '../.build/core/dist/moment-views/index.js';
import PublicSelf from '../.build/core/dist/public-self/index.js';
import Plain from '../.build/core/dist/plain/index.js';
import PlainInit from '../.build/core/dist/plain-init/index.js';

const source={consumer:'b9-social'};
export const publicMigrations=[publicMigration(source),momentViewsMigration(source)];
export const page={maxItems:1000,maxResponseBytes:1<<20};

async function provide(run,{plain=false,plainInit}={}){
 const ctx=run.ctx;
 await ctx.plugin(Exploration,{maxInlineFileBytes:16777216,maxSearchBytes:67108864,maxSearchOutputBytes:16777216,maxSearchMs:30000});
 await ctx.plugin(Public,{});
 await ctx.plugin(MomentViews,{freeze:'clock'});
 await ctx.plugin(PublicSelf,{});
 if(plainInit)await ctx.plugin(PlainInit,plainInit);
 if(plain)await ctx.plugin(Plain,{});
 const reservation=await ctx.executionFence.reserve({id:'b9'});
 return {...run,reservation};
}

export async function socialRun(options={}){
 const run=await fixture({...options,database:{maxQueryBytes:16777216,maxQueryRows:100000,maxInlineBytes:16777216,...(options.database??{})},migrations:[...publicMigrations,...(options.migrations??[])]});
 try{return await provide(run,{plain:options.plain,plainInit:options.plainInit})}catch(error){await run.host.close();throw error}
}

export function context(run,{actorId='a',momentId='m1',operationId='b9-op',logicalTime='1'}={}){
 return {...activation(run,run.reservation,operationId,actorId),momentId,logicalTime};
}

export async function openMoment(run,momentId,operationId='b9-open-'+momentId){
 return run.ctx.database.transaction(run.reservation.lease,request(operationId),tx=>run.ctx.momentViews.open(tx,momentId));
}

export async function read(run,body){
 const view=await run.ctx.database.snapshot({maxAgeMs:30000});
 try{return await body(view)}finally{await view.close()}
}

export async function session(run,{actorId='a',momentId='m1',operationId='b9-read'}={}){
 const scope={actorId,momentId,query:null};
 const acquisition={runId:run.runId,generation:'g',operationId,signal:run.reservation.signal,writeLease:run.reservation.lease,database:run.ctx.database,actorId,momentId};
 return run.ctx.exploration.acquire(scope,acquisition);
}

export async function readText(run,path,{actorId='a',momentId='m1',operationId='b9-read'}={}){
 const lease=await session(run,{actorId,momentId,operationId});
 try{
  return await lease.value.withView(async current=>{
   const node=await current.stat(path);
   const handle=await current.open(node);
   const chunks=[];
   try{
    for await(const chunk of handle.value.read({offset:'0',length:Number(node.rawBytes),maxBytes:32*1024*1024},new AbortController().signal))chunks.push(Buffer.from(chunk));
   }finally{await handle.release()}
   return Buffer.concat(chunks).toString('utf8');
  });
 }finally{await lease.release()}
}

export async function grep(run,pattern,{actorId='a',momentId='m1',root='/public',operationId='b9-grep'}={}){
 const lease=await session(run,{actorId,momentId,operationId});
 try{
  return await lease.value.withView(current=>current.grep({root,pattern,mode:'literal',page}));
 }finally{await lease.release()}
}

export function parseLines(text){
 return text.split('\n').filter(line=>line.length>0).map(line=>JSON.parse(line));
}
