import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single,migrationList} from './b4-support.mjs';

// B4：在 revision 0 上重复 applyMigrations 必须幂等。
test('revision 0 重复applyMigrations幂等',async()=>{
 const run=await setup();try{
  const before=await run.read(view=>view.schema);
  const again=await run.ctx.database.applyMigrations(migrationList());
  assert.deepEqual(again,before);
 }finally{await run.close()}
});

// B4：已有提交后 sqlite 的 migrate 明确拒绝（SCHEMA_LIVE），这是重开不能再次迁移的原因。
test('已有提交后migrate返回SCHEMA_LIVE',async()=>{
 const run=await setup();try{
  await run.tx('b4-migrate-write',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'}]));
  await assert.rejects(run.ctx.database.applyMigrations(migrationList()),error=>error.code==='SCHEMA_LIVE'||String(error).includes('SCHEMA_LIVE'));
 }finally{await run.close()}
});
