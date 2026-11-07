import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setup} from './pr07-support.mjs';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ContextMount} from '../.build/core/dist/mount-context/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import {ActorTools,actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';

const profiles=['event-sqlite','rule-sqlite','social-codex-sqlite','social-sqlite'];
const flatten=entries=>entries.flatMap(entry=>entry.group?flatten(entry.config):[entry]);

async function profileEntries(name){
 const profile=JSON.parse(await readFile(new URL('../profiles/'+name+'.json',import.meta.url),'utf8'));
 return flatten(profile.patches[0].insert);
}

async function harness(t,config={}){
 const run=await setup({migrations:[actorToolsMigration({test:'b9-profile-tools'})]});
 t.after(()=>run.close());
 for(const plugin of [Prompts,ContextMount,ExplorationShell,ActorTools])await run.host.ctx.plugin(plugin,config);
 let ctx;await run.host.ctx.plugin({inject:['actorTools'],apply(c){ctx=c}});
 return {run,ctx};
}

async function names(t,config){
 const {run,ctx}=await harness(t,config);
 const binding=await ctx.actorTools.bind(run.context('b9-tools','a','m'));
 try{return binding.value.tools.map(item=>item.name).sort()}finally{await binding.release()}
}

test('B9 every profile that ships an agent tool surface requests exactly bash/read/find/grep/act',async t=>{
 let shipped=0;
 for(const name of profiles){
  const entries=await profileEntries(name);
  const entry=entries.find(item=>item.name==='@society0/core/actor-tools');
  if(!entry){
   assert.equal(entries.some(item=>item.name==='@society0/core/driver-llm'),false,name+' has no agent runtime without actor-tools');
   continue;
  }
  shipped++;
  const config=entry.config??{};
  assert.equal(config.legacyTools??undefined,undefined,name+' must not enable legacy tools by default');
  assert.deepEqual(await names(t,config),['act','bash','find','grep','read'],name+' default tool surface');
 }
 assert.equal(shipped,2,'both social profiles ship actor-tools');
});

test('B9 legacyTools re-adds information_* and exploration_* and nothing else',async t=>{
 for(const name of ['social-codex-sqlite','social-sqlite']){
  const actual=await names(t,{legacyTools:['information','exploration']});
  assert.deepEqual(actual,['act','bash','exploration_collect','exploration_list','find','grep','information_discover','information_query','read'],name+' legacy tool surface');
  assert.equal(actual.includes('actions_list'),false,name+' does not resurrect actions_list');
 }
});

test('B9 legacyTools defaults to an empty list in the committed profiles',async()=>{
 for(const name of profiles){
  const entries=await profileEntries(name);
  for(const entry of entries){
   if(entry.name!=='@society0/core/actor-tools')continue;
   const legacy=entry.config?.legacyTools;
   assert.ok(legacy===undefined||(Array.isArray(legacy)&&legacy.length===0),name+' must keep legacyTools empty');
  }
 }
});
