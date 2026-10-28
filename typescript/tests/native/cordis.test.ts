import assert from 'node:assert/strict'
import { test } from 'node:test'
import { Context, FiberState, Service } from '@society0/cordis'

declare module '@society0/cordis' {
  interface Context {
    labContributions: OwnedContributions
    labDependency: string
    labVersion: string
  }
}

class OwnedContributions extends Service<{ tag?: string }> {
  static inject = ['labDependency']
  readonly entries = new Map<string, number | null>()

  constructor(ctx: Context) {
    super(ctx, 'labContributions')
  }

  register(id: string) {
    const owner = this.ctx.fiber.uid
    return this.ctx.effect(() => {
      assert.equal(this.entries.has(id), false)
      this.entries.set(id, owner)
      return () => { this.entries.delete(id) }
    }, `lab.register(${id})`)
  }

  dependency() {
    return this.ctx.labDependency
  }

  tag() {
    return this[Service.resolveConfig]({ tag: 'default' }).tag
  }
}

test('C01 service registration belongs to its caller; dependencies resolve at definition site', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  await ctx.plugin((owner: Context) => { owner.provide('labDependency', 'definition') })
  const service = await ctx.plugin(OwnedContributions)
  const callerRealm = ctx.isolate('labDependency').intercept('labContributions', { tag: 'caller' })
  await callerRealm.plugin((owner: Context) => { owner.provide('labDependency', 'use-site') })
  let observedDependency: string | undefined
  let observedTag: string | undefined
  const first = await callerRealm.plugin({
    inject: ['labContributions'],
    apply(owner: Context) {
      owner.labContributions.register('first')
      observedDependency = owner.labContributions.dependency()
      observedTag = owner.labContributions.tag()
    },
  })
  const second = await ctx.plugin({
    inject: ['labContributions'],
    apply(owner: Context) { owner.labContributions.register('second') },
  })
  assert.equal(observedDependency, 'definition')
  assert.equal(observedTag, 'caller')
  assert.deepEqual([...ctx.labContributions.entries], [['first', first.uid], ['second', second.uid]])
  await first.dispose()
  assert.deepEqual([...ctx.labContributions.entries], [['second', second.uid]])
  assert.equal(service.state, FiberState.ACTIVE)
})

test('C02 multiple fibers of one plugin remain independent across isolate labels', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  const left = ctx.isolate('labVersion')
  const right = ctx.isolate('labVersion')
  const provider = (owner: Context, version: string) => { owner.provide('labVersion', version) }
  const leftProvider = await left.plugin(provider, 'left')
  const rightProvider = await right.plugin(provider, 'right')
  const seen: string[] = []
  const consumer = {
    inject: ['labVersion'],
    apply(owner: Context) { seen.push(owner.labVersion) },
  }
  const leftConsumer = await left.plugin(consumer)
  const rightConsumer = await right.plugin(consumer)
  assert.deepEqual(seen, ['left', 'right'])
  assert.equal(ctx.registry.get(provider)?.fibers.length, 2)
  assert.notEqual(leftProvider.uid, rightProvider.uid)
  assert.equal(ctx.get('labVersion'), undefined)
  await leftProvider.dispose()
  assert.equal(leftConsumer.state, FiberState.PENDING)
  assert.equal(rightConsumer.state, FiberState.ACTIVE)
  assert.equal(right.get('labVersion'), 'right')
})

test('C03 required dependencies wait, unload and bind to a replacement provider', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  const events: string[] = []
  const consumer = await ctx.plugin({
    inject: ['labVersion'],
    apply(owner: Context) {
      const version = owner.labVersion
      events.push(`start:${version}`)
      return () => { events.push(`stop:${version}`) }
    },
  })
  assert.equal(consumer.state, FiberState.PENDING)
  assert.deepEqual(events, [])
  const provider = (owner: Context, value: string) => { owner.provide('labVersion', value) }
  const first = await ctx.plugin(provider, 'one')
  await consumer.await()
  assert.deepEqual(events, ['start:one'])
  await first.dispose()
  assert.equal(consumer.state, FiberState.PENDING)
  assert.deepEqual(events, ['start:one', 'stop:one'])
  await ctx.plugin(provider, 'two')
  await consumer.await()
  assert.equal(consumer.state, FiberState.ACTIVE)
  assert.deepEqual(events, ['start:one', 'stop:one', 'start:two'])
})

test('C04 disposal awaits an owned asynchronous cleanup', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  const entered = Promise.withResolvers<void>()
  const release = Promise.withResolvers<void>()
  let cleanups = 0
  let disposed = false
  const fiber = await ctx.plugin((owner: Context) => {
    owner.effect(() => async () => {
      entered.resolve()
      await release.promise
      cleanups++
    })
  })
  const closing = Promise.resolve(fiber.dispose()).then(() => { disposed = true })
  await entered.promise
  assert.equal(disposed, false)
  assert.equal(cleanups, 0)
  release.resolve()
  await closing
  assert.equal(disposed, true)
  assert.equal(cleanups, 1)
  await fiber.dispose()
  assert.equal(cleanups, 1)
})

test('C05 optional get is an instantaneous probe; inject creates a separately owned reactive region', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  let outerStarts = 0
  let innerStarts = 0
  let innerStops = 0
  let probe: string | undefined
  const outer = await ctx.plugin((owner: Context) => {
    outerStarts++
    probe = owner.get('labVersion')
    owner.inject(['labVersion'], () => {
      innerStarts++
      return () => { innerStops++ }
    })
  })
  assert.equal(probe, undefined)
  const provider = await ctx.plugin((owner: Context) => { owner.provide('labVersion', 'present') })
  for (const runtime of ctx.registry.values()) {
    for (const fiber of runtime.fibers) await fiber.await()
  }
  assert.equal(outerStarts, 1)
  assert.equal(innerStarts, 1)
  assert.equal(probe, undefined)
  await provider.dispose()
  assert.equal(outer.state, FiberState.ACTIVE)
  assert.equal(innerStops, 1)
  await outer.dispose()
})

// These three characterization tests record authoring constraints of the pinned
// runtime. Passing them is evidence of a limitation, not a claim that it is fixed.
test('C06 known limit: an arrow service field captures provider ownership', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  class ArrowRegistration extends Service {
    readonly registrations = new Map<string, number | null>()
    constructor(owner: Context) { super(owner, 'labArrow') }
    register = (id: string) => this.ctx.effect(() => {
      this.registrations.set(id, this.ctx.fiber.uid)
      return () => { this.registrations.delete(id) }
    })
  }
  const provider = await ctx.plugin(ArrowRegistration)
  const consumer = await ctx.plugin({
    inject: ['labArrow'],
    apply(owner: Context) { owner.get('labArrow').register('entry') },
  })
  const registry = ctx.get('labArrow') as ArrowRegistration
  assert.deepEqual([...registry.registrations], [['entry', provider.uid]])
  await consumer.dispose()
  assert.equal(registry.registrations.has('entry'), true)
  await provider.dispose()
  assert.equal(registry.registrations.size, 0)
})

test('C07 known limit: ECMAScript private fields cannot be read through a traceable service receiver', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  class PrivateField extends Service {
    #value = 1
    constructor(owner: Context) { super(owner, 'labPrivate') }
    value() { return this.#value }
  }
  await ctx.plugin(PrivateField)
  assert.throws(() => { ctx.get('labPrivate').value() }, TypeError)
})

test('C08 known limit: ordinary function plugins must register cleanup through ctx.effect', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  const cleaned = { ordinaryReturn: 0, ordinaryEffect: 0, arrowReturn: 0, methodReturn: 0 }
  function ordinary(owner: Context) {
    owner.effect(() => () => { cleaned.ordinaryEffect++ })
    return () => { cleaned.ordinaryReturn++ }
  }
  const plain = await ctx.plugin(ordinary)
  const arrow = await ctx.plugin(() => () => { cleaned.arrowReturn++ })
  const method = await ctx.plugin({ apply() { return () => { cleaned.methodReturn++ } } })
  await plain.dispose()
  await arrow.dispose()
  await method.dispose()
  assert.deepEqual(cleaned, { ordinaryReturn: 0, ordinaryEffect: 1, arrowReturn: 1, methodReturn: 1 })
})
