import assert from 'node:assert/strict'
import { test } from 'node:test'
import { Context } from '@society0/cordis'
import Loader from '@society0/loader'

test('real Loader transfers exact binding label and withdraws migrated publication', async()=>{
  const root=new Context();let starts=0
  await root.plugin(Loader)
  root.loader.builtins.probe=(ctx:Context)=>{starts++;ctx.provide('version','value')}
  const id=await root.loader.create({name:'cordis:probe',isolate:{version:'old'}})
  const entry=root.loader.resolve(id);await entry.fiber!.await()
  const oldLabel=entry.ctx[Context.isolate].version
  const impl=root.reflect.store[oldLabel]
  try {
    await entry.update({isolate:{version:'new'}});await entry.fiber!.await()
    const newLabel=entry.ctx[Context.isolate].version
    assert.notEqual(newLabel,oldLabel);assert.equal(root.reflect.store[newLabel],impl)
    assert.equal(impl.isolate,newLabel);assert.equal(starts,1)
    assert.equal(root.reflect.store[oldLabel],undefined)
    await entry.fiber!.dispose()
    assert.equal(root.reflect.store[newLabel],undefined)
  } finally {await root.fiber.dispose()}
})
