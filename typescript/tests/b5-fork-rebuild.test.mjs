import {test} from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {readdir} from 'node:fs/promises';
import {setup,single,frontier} from './b4-support.mjs';
import {openConnection} from '../.build/sqlite-native/src/index.mjs';

const page={maxItems:100,maxResponseBytes:1<<20};

test('真实changeset fork：grep一致，fallback后rebuild不再退化',async()=>{
 const source=await setup();let branch;try{
  await source.tx('b5-fork-seed',tx=>source.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'fork needle'},{id:'b',role:'all',name:'other'}]));
  await source.openMoment('m');
  const search=async (run)=>run.read(async view=>{
   const session=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b5-fork-search'));
   try{return (await session.value.withView(current=>current.grep({root:'/public',pattern:'needle',mode:'literal',page}))).items.map(item=>({path:item.path,line:item.line,column:item.column,lineText:item.lineText}))}finally{await session.release()}
  });
  const before=await search(source);
  assert.equal(before.length,1);
  await source.tx('b5-fork-point',tx=>source.ctx.database.metadata.complete(tx,frontier(),[]));
  const point=await source.seal();

  const target=await setup();const directory=target.directory;await target.close();
  const names=(await readdir(join(source.directory,'segments'))).filter(name=>/^\d+\.changeset$/.test(name)).sort((a,b)=>Number(a.split('.')[0])-Number(b.split('.')[0]));
  const conn=openConnection({path:join(directory,'world.sqlite'),synchronous:'normal'});
  try{for(const name of names)conn.applySegment(join(source.directory,'segments',name));}finally{conn.close()}

  branch=await setup({directory,resume:point});
  const after=await search(branch);
  assert.deepEqual(after,before,'fork 后逐条一致');
  assert.ok(branch.ctx.public.diagnostics().fallbackScans>0,'fork 后派生索引为空必须走 fallback');
  const stale=branch.ctx.public.diagnostics();
  await branch.tx('b5-fork-rebuild',tx=>branch.ctx.public.rebuildSearch(tx));
  assert.deepEqual(await search(branch),before);
  assert.equal(branch.ctx.public.diagnostics().fallbackScans,stale.fallbackScans,'rebuild 后不再退化');
  assert.equal(branch.ctx.public.diagnostics().searchStale,stale.searchStale);
 }finally{await branch?.close();await source.close()}
});
