import assert from 'node:assert/strict'
import { test, afterEach } from 'node:test'
import { inspect } from 'node:util'
import { setImmediate as tick } from 'node:timers/promises'
import { Context, FiberState } from '@society0/cordis'

const roots: Context[] = []
function createRoot() { const root = new Context(); roots.push(root); return root }
afterEach(() => {
  for (const root of roots.splice(0)) {
    const errors = root.logger.buffer.filter(message => message.type === 'error').map(message => inspect(message.args, {depth: 8}))
    for (const error of errors) {
      console.log('LIFECYCLE_DIAGNOSTIC', error)
      assert.match(error, /expected (cleanup|setup|observer|service observer)/, 'unexpected lifecycle failure was logged')
    }
  }
})


for (const kind of ['required', 'removed', 'child']) {
  test(`retains provider until ${kind} cleanup really finishes`, { timeout: 3000 }, async () => {
    const root = createRoot(), entered = Promise.withResolvers<void>(), release = Promise.withResolvers<void>()
    let closed = 0, result: unknown, consumer: any, closing: any, removed: any, finished = false
    const setup = (ctx: Context) => () => (async () => {
      entered.resolve(); await release.promise
      try { result = ctx.resource.read() } catch (e) { result = e }
    })()
    try {
      const provider = await root.plugin((ctx: Context) => {
        ctx.provide('resource', { read: () => closed ? 'closed' : 'open' })
        ctx.effect(() => () => { closed++ })
        if (kind === 'child') consumer = ctx.plugin(setup)
      })
      if (kind !== 'child') consumer = root.inject(['resource'], setup)
      await consumer.await()
      if (kind === 'removed') { removed = consumer.dispose(); await entered.promise }
      closing = provider.dispose().then(() => { finished = true })
      await entered.promise; await tick()
      assert.equal(closed, 0); assert.equal(finished, false)
      const late = await root.inject(['resource'], () => { throw new Error('retired binding admitted') })
      assert.equal(late.state, FiberState.PENDING)
      release.resolve(); await closing; await removed
      assert.equal(result, 'open'); assert.equal(closed, 1)
    } finally { release.resolve(); await closing; await removed; await root.fiber.dispose() }
  })
}

test('repeated dispose joins in-flight cleanup', {timeout:3000}, async () => {
  const root = createRoot(), entered = Promise.withResolvers<void>(), release = Promise.withResolvers<void>()
  const fiber = await root.plugin((ctx: Context) => { ctx.effect(() => async () => { entered.resolve(); await release.promise }) })
  let joined = false
  const first = fiber.dispose()
  try { await entered.promise; const second = Promise.resolve(fiber.dispose()).then(() => { joined = true }); await tick(); assert.equal(joined,false); release.resolve(); await second }
  finally { release.resolve(); await first; await root.fiber.dispose() }
})

for (const asyncFailure of [false, true]) test(`composed cleanup collects remaining disposers after ${asyncFailure ? 'async' : 'sync'} failure`, async () => {
  const root = createRoot(), seen: string[] = []
  const fiber = await root.plugin((ctx: Context) => {
    ctx.effect(function* () {
      yield () => { seen.push('last') }
      yield asyncFailure ? async () => { seen.push('failure'); throw new Error('expected cleanup fault') } : () => { seen.push('failure'); throw new Error('expected cleanup fault') }
      yield () => { seen.push('first') }
    })
  })
  try { await fiber.dispose(); assert.deepEqual(seen,['first','failure','last']) }
  finally { await root.fiber.dispose() }
})


test('withdrawal waits old commitment and preserves replacement', {timeout:3000}, async () => {
  const root = createRoot(), entered = Promise.withResolvers<void>(), release = Promise.withResolvers<void>(), nextEntered = Promise.withResolvers<void>(), nextRelease = Promise.withResolvers<void>()
  let withdraw: any, providerCtx: Context, oldRead: any, closing: any
  const provider = await root.plugin((ctx: Context) => { providerCtx=ctx; withdraw=ctx.provide('version','old'); ctx.provide('other','unaffected') })
  const other = await root.inject(['other'], () => {})
  const consumer = await root.inject(['version'], (ctx: Context) => {
    if (ctx.version === 'new') return (async () => { nextEntered.resolve(); await nextRelease.promise; return () => {} })()
    return async () => { entered.resolve(); await release.promise; oldRead=ctx.version }
  })
  try {
    closing = Promise.resolve(withdraw())
    await entered.promise
    providerCtx!.provide('version','new')
    assert.equal(other.state,FiberState.ACTIVE)
    release.resolve(); await closing; await nextEntered.promise
    assert.equal(oldRead,'old'); assert.equal(root.get('version'),'new')
  } finally { release.resolve(); nextRelease.resolve(); await closing; await consumer.await(); await root.fiber.dispose() }
})

test('provider final listener survives consumer cleanup', {timeout:3000}, async () => {
  const root = createRoot(); let observed=0
  const provider=await root.plugin((ctx: Context) => { ctx.provide('resource',1); ctx.on('final',()=>{observed++}) })
  await root.inject(['resource'],(ctx: Context)=>()=>{ctx.emit('final')})
  try { await provider.dispose(); assert.equal(observed,1) } finally { await root.fiber.dispose() }
})

test('independent providers drain concurrently', {timeout:3000}, async () => {
  const root=createRoot(), entered=[Promise.withResolvers<void>(),Promise.withResolvers<void>()], release=[Promise.withResolvers<void>(),Promise.withResolvers<void>()]
  const providers=[]
  for(let i=0;i<2;i++) {
    providers.push(await root.plugin((ctx:Context)=>{ctx.provide(`r${i}`,i)}))
    await root.inject([`r${i}`],()=>async()=>{entered[i].resolve(); await release[i].promise})
  }
  const closing=providers.map(p=>p.dispose())
  try { await Promise.all(entered.map(x=>x.promise)); release[0].resolve(); await closing[0]; release[1].resolve(); await closing[1] }
  finally { release.forEach(x=>x.resolve()); await Promise.all(closing); await root.fiber.dispose() }
})

test('throwing status and service observers do not strand transitions', {timeout:3000}, async () => {
  const root=createRoot(); let stops=0, observed=0
  root.on('internal/status',()=>{throw new Error('expected observer sync fault')})
  root.on('internal/status',async()=>{throw new Error('expected observer async fault')})
  root.on('internal/service',()=>{throw new Error('expected service observer fault')})
  root.on('internal/service',()=>{observed++})
  try {
    const provider=await root.plugin((ctx:Context)=>{ctx.provide('resource',1)})
    await root.inject(['resource'],()=>()=>{stops++})
    await provider.dispose(); assert.equal(stops,1); assert.ok(observed>0)
  } finally { await root.fiber.dispose() }
})


test('parent waits child async setup rollback', {timeout:3000}, async () => {
  const root=createRoot(), entered=Promise.withResolvers<void>(), release=Promise.withResolvers<void>()
  let closed=0, cleaned=0, child: any, closing: any
  const provider=await root.plugin((ctx:Context)=>{
    ctx.provide('resource',1); ctx.effect(()=>()=>{closed++})
    child=ctx.plugin(async (owner:Context)=>{ owner.effect(()=>()=>{cleaned++}); entered.resolve(); await release.promise; return ()=>{assert.equal(closed,0)} })
  })
  try { await entered.promise; closing=provider.dispose(); await tick(); assert.equal(closed,0); release.resolve(); await closing; assert.equal(cleaned,1); assert.equal(closed,1); assert.equal(child.state,FiberState.DISPOSED) }
  finally {release.resolve(); await closing; await root.fiber.dispose()}
})

test('reentrant plugin publication owns child before parent teardown', {timeout:3000}, async()=>{
  const root=createRoot(), entered=Promise.withResolvers<void>(), release=Promise.withResolvers<void>()
  let parentClosing:any, child:any, closed=0
  const parent=await root.plugin((ctx:Context)=>{ctx.effect(()=>()=>{closed++})})
  root.on('internal/plugin',(fiber:any)=>{
    if(fiber.parent.fiber!==parent || fiber.uid===null) return
    fiber.ctx.effect(()=>async()=>{entered.resolve(); await release.promise; assert.equal(closed,0)})
    parentClosing=parent.dispose()
  })
  try { child=parent.ctx.plugin(()=>{}); await entered.promise; await tick(); assert.equal(closed,0); assert.throws(()=>parent.ctx.plugin(()=>{})); release.resolve(); await parentClosing; assert.equal(child.state,FiberState.DISPOSED); assert.equal(closed,1) }
  finally {release.resolve(); await parentClosing; await root.fiber.dispose()}
})

test('pending required cycle remains disposable without activation', {timeout:3000}, async()=>{
  const root=createRoot(); let starts=0
  const a=await root.plugin({inject:['b'],apply(ctx:Context){starts++;ctx.provide('a',1)}})
  const b=await root.plugin({inject:['a'],apply(ctx:Context){starts++;ctx.provide('b',1)}})
  assert.equal(a.state,FiberState.PENDING);assert.equal(b.state,FiberState.PENDING)
  await root.fiber.dispose();assert.equal(starts,0);assert.equal(a.state,FiberState.DISPOSED)
})

test('external effect cleanup remains joined by owner', {timeout:3000}, async()=>{
  const root=createRoot(), entered=Promise.withResolvers<void>(),release=Promise.withResolvers<void>()
  let dispose:any, done=false
  const fiber=await root.plugin((ctx:Context)=>{dispose=ctx.effect(()=>async()=>{entered.resolve();await release.promise})})
  const external=dispose();await entered.promise
  const closing=fiber.dispose().then(()=>{done=true})
  try {await tick();assert.equal(done,false);release.resolve();await closing;await external}
  finally {release.resolve();await closing;await root.fiber.dispose()}
})

for(const trigger of ['provider','root','restart']) test(`transitive dependencies drain on ${trigger}`, {timeout:3000}, async()=>{
  const root=createRoot(), sequence:string[]=[]
  const a=await root.plugin((ctx:Context)=>{ctx.provide('a',1);ctx.effect(()=>()=>{sequence.push('a')})})
  await root.plugin({inject:['a'],apply(ctx:Context){ctx.provide('b',1);ctx.effect(()=>async()=>{await tick();assert.equal(ctx.a,1);sequence.push('b')})}})
  await root.inject(['b'],(ctx:Context)=>async()=>{await tick();assert.equal(ctx.b,1);sequence.push('c')})
  try { if(trigger==='root') await root.fiber.dispose(); else if(trigger==='restart') await a.restart(); else await a.dispose(); assert.deepEqual(sequence.slice(0,3),['c','b','a']) }
  finally {await root.fiber.dispose()}
})


test('same fiber same-name provisions retain distinct isolation identity',async()=>{
  const root=createRoot(), left=Symbol('left'),right=Symbol('right');let seen:any[]=[]
  const provider=await root.plugin((ctx:Context)=>{
    const a=ctx.isolate('version',left),b=ctx.isolate('version',right)
    a.provide('version','left');b.provide('version','right')
    seen=[a.version,b.version]
  })
  try {assert.deepEqual(seen,['left','right']);assert.equal(root.isolate('version',left).get('version'),'left');assert.equal(root.isolate('version',right).get('version'),'right');await provider.dispose();assert.equal(root.isolate('version',left).get('version'),undefined);assert.equal(root.isolate('version',right).get('version'),undefined)}
  finally {await root.fiber.dispose()}
})

test('unreleased cleanup retains provider and rejects new admission', {timeout:3000}, async()=>{
  const root=createRoot(), entered=Promise.withResolvers<void>(), release=Promise.withResolvers<void>();let closed=0,done=false
  const provider=await root.plugin((ctx:Context)=>{ctx.provide('resource',1);ctx.effect(()=>()=>{closed++})})
  await root.inject(['resource'],()=>async()=>{entered.resolve();await release.promise})
  const closing=provider.dispose().then(()=>{done=true})
  try {await entered.promise;await tick();assert.equal(done,false);assert.equal(closed,0);assert.equal(root.get('resource'),undefined);release.resolve();await closing;assert.equal(closed,1)}
  finally {release.resolve();await closing;await root.fiber.dispose()}
})


test('status observers see dependency invalidation already propagated', async()=>{
  const root=createRoot(),observed:boolean[]=[]
  const provider=await root.plugin((ctx:Context)=>{ctx.provide('resource',1)})
  const consumer=await root.inject(['resource'],()=>()=>{})
  root.on('internal/status',(fiber:any)=>{if(fiber===provider && fiber.state===FiberState.UNLOADING) observed.push(consumer.state!==FiberState.ACTIVE)})
  try {await provider.dispose();assert.deepEqual(observed,[true])}finally{await root.fiber.dispose()}
})


for (const failure of ['setup','cleanup']) test(`committed binding released after ${failure} failure`,async()=>{
  const root=createRoot();let closed=0,cleaned=0
  const provider=await root.plugin((ctx:Context)=>{ctx.provide('resource',1);ctx.effect(()=>()=>{closed++})})
  const impl=[...provider.provisions][0]
  const consumer=root.inject(['resource'],(ctx:Context)=>{
    ctx.effect(()=>()=>{cleaned++})
    if(failure==='setup')throw new Error('expected setup fault')
    return async()=>{throw new Error('expected cleanup fault')}
  })
  try {
    if(failure==='setup') await assert.rejects(consumer.await(),/expected setup fault/)
    else await consumer.await()
    await provider.dispose()
    assert.equal(cleaned,1);assert.equal(closed,1);assert.equal(impl.dependents.size,0)
    assert.ok(root.logger.buffer.some(message=>message.type==='error'))
  }finally{await root.fiber.dispose()}
})
