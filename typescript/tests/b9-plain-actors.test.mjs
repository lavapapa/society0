import {test} from 'node:test';
import assert from 'node:assert/strict';
import {socialRun,openMoment,readText,parseLines,read} from './b9-support.mjs';
import {initializerSlot} from '../.build/core/dist/initializers/index.js';

const actorsFile='/public/actors.jsonl';

test('B9 plain-init与plain.update推式写入/public/actors.jsonl',async()=>{
 const run=await socialRun({social:false,plain:true,plainInit:{initialActors:[{id:'x',attributes:{name:'X'}},{id:'y',attributes:{name:'Y'}}]}});
 try{
  const schema=await read(run,view=>view.schema);
  const selected=await run.ctx.slots.acquire(initializerSlot,{generation:'g',signal:run.reservation.signal});
  try{
   const initializer=selected.items.find(item=>item.id==='plain.seed').value;
   await initializer.initialize({
    ctx:run.ctx,runId:run.ctx.database.runId,generation:'g',schema,initialTime:0,input:{},
    signal:run.reservation.signal,database:run.ctx.database,writeLease:run.reservation.lease,artifacts:run.ctx.artifacts
   });
  }finally{await selected.release()}

  await openMoment(run,'m1');
  const initial=parseLines(await readText(run,actorsFile,{actorId:'x',momentId:'m1',operationId:'b9-plain-init'}));
  assert.deepEqual(initial.map(row=>row.id),['x','y']);
  assert.deepEqual(initial.map(row=>row.attributes.name),['X','Y']);
  assert.ok(initial.every(row=>row.participation==='active'&&row.version===1));
  assert.ok(initial.every(row=>row.reason===undefined),'reason不属于公开字段');

  const updated=await run.ctx.actions.execute('plain.update',{attributes:{name:'X2',role:'tester'}},{
   ctx:run.ctx,runId:run.ctx.database.runId,generation:'g',operationId:'b9-plain-update',actorId:'x',momentId:'m1',
   logicalTime:'5',signal:run.reservation.signal,database:run.ctx.database,writeLease:run.reservation.lease
  });
  assert.equal(updated.status,'completed');
  const frozen=parseLines(await readText(run,actorsFile,{actorId:'x',momentId:'m1',operationId:'b9-plain-m1'}));
  assert.equal(frozen.find(row=>row.id==='x').attributes.name,'X','m1的/public冻结视图应保留旧值');
  assert.equal(frozen.find(row=>row.id==='x').version,1);

  await openMoment(run,'m2');
  const after=parseLines(await readText(run,actorsFile,{actorId:'x',momentId:'m2',operationId:'b9-plain-m2'}));
  const x=after.find(row=>row.id==='x');
  assert.deepEqual(x.attributes,{name:'X2',role:'tester'});
  assert.equal(x.version,2);
  assert.equal(x.participation,'active');
 }finally{await run.reservation.release();await run.host.close()}
});
