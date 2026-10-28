import assert from 'node:assert/strict'
import { test } from 'node:test'
import { Context, FiberState, type Fiber, type Plugin } from '@society0/cordis'
import Loader from '@society0/loader'
import Schema from '@society0/schemastery'

async function mountEntry(ctx: Context, name: string, plugin: Plugin, config: unknown = {}): Promise<Fiber> {
  ctx.loader.builtins[name] = plugin
  const id = await ctx.loader.create({ name: `cordis:${name}`, config })
  const fiber = ctx.loader.resolve(id).fiber
  assert.ok(fiber)
  await fiber.await()
  return fiber
}

test('F01 Config supplies defaults and rejects invalid input before plugin apply', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  const Config = Schema.object({
    title: Schema.string().default('baseline'),
    count: Schema.number().min(1).default(2),
  })
  const applied: ReturnType<typeof Config>[] = []
  const plugin = {
    Config,
    apply(_owner: Context, config: ReturnType<typeof Config>) { applied.push(config) },
  }
  const good = await ctx.plugin(plugin, {})
  assert.deepEqual(applied, [{ title: 'baseline', count: 2 }])
  const bad = ctx.plugin(plugin, { count: 0 })
  await assert.rejects(Promise.resolve(bad), /invalid config|count|greater|small/i)
  assert.equal(bad.ctx.fiber.state, FiberState.FAILED)
  assert.equal(good.state, FiberState.ACTIVE)
  assert.equal(applied.length, 1)
})

test('F02 Loader volatile updates commit stable references locally without remounting', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  await ctx.plugin(Loader)
  const Config = Schema.object({
    label: Schema.string().default('one').volatile(),
    fixed: Schema.string().default('stable'),
  })
  const configs: ReturnType<typeof Config>[] = []
  const notifications: { label: string; paths: readonly (readonly string[])[] }[] = []
  let cleanups = 0
  let customUpdates = 0
  const plugin = {
    Config,
    apply(owner: Context, config: ReturnType<typeof Config>) {
      configs.push(config)
      owner.on('loader/volatile-update', paths => {
        notifications.push({ label: config.label.get(), paths })
      })
      owner.on('internal/update', (_candidate: unknown, _noSave: boolean, next: () => void) => {
        customUpdates++
        next()
      })
      return () => { cleanups++ }
    },
  }
  const first = await mountEntry(ctx, 'volatile-first', plugin)
  await mountEntry(ctx, 'volatile-second', plugin)
  const initial = configs[0]
  const second = configs[1]
  assert.ok(initial && second)
  const reference = initial.label
  assert.ok(first.entry)
  await first.entry.update({ config: { label: 'two' } })
  await first.await()
  assert.equal(configs.length, 2)
  assert.equal(cleanups, 0)
  assert.equal(customUpdates, 0)
  assert.equal(first.config, initial)
  assert.equal(initial.label, reference)
  assert.equal(initial.label.get(), 'two')
  assert.equal(second.label.get(), 'one')
  assert.deepEqual(notifications, [{ label: 'two', paths: [['label']] }])
  await first.entry.update({ config: { label: 'two' } })
  assert.equal(notifications.length, 1)
  await first.entry.update({ config: { label: 123 } })
  await first.await()
  assert.equal(first.state, FiberState.ACTIVE)
  assert.equal(initial.label.get(), 'two')
  assert.equal(notifications.length, 1)
  // Published 4.0.5 lacks the later checkout's rawConfig/effectiveConfig accessors.
  // This is an implementation-characterization assertion, not a proposed public API.
  assert.deepEqual(first._config, { label: 123 })
  await first.entry.update({ config: { label: 'three', fixed: 'changed' } })
  await first.await()
  assert.equal(configs.length, 3)
  assert.equal(cleanups, 1)
  assert.equal(customUpdates, 1)
  const changed = configs[2]
  assert.ok(changed)
  assert.equal(changed.label.get(), 'three')
  assert.notEqual(changed.label, reference)
  assert.equal(initial.label.get(), 'two')
})

test('F03 direct Fiber.update remounts, including a schema volatile field', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  const Config = Schema.object({ value: Schema.string().default('initial').volatile() })
  const configs: ReturnType<typeof Config>[] = []
  let cleanups = 0
  const fiber = await ctx.plugin({
    Config,
    apply(_owner: Context, config: ReturnType<typeof Config>) {
      configs.push(config)
      return () => { cleanups++ }
    },
  }, {})
  const old = configs[0]
  assert.ok(old)
  fiber.update({ value: 'replacement' })
  await fiber.await()
  assert.equal(configs.length, 2)
  assert.equal(cleanups, 1)
  assert.equal(old.value.get(), 'initial')
  assert.equal(configs[1]?.value.get(), 'replacement')
  assert.notEqual(configs[1]?.value, old.value)
})

test('F04 invalid active direct update retains the running value but retains invalid raw input for a later restart', async (t) => {
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose() })
  const Config = Schema.object({ count: Schema.number().min(1).default(1) })
  let starts = 0
  const fiber = await ctx.plugin({ Config, apply() { starts++ } }, {})
  const before = fiber.config
  assert.throws(() => { fiber.update({ count: 0 }) })
  assert.equal(fiber.state, FiberState.ACTIVE)
  assert.equal(fiber.config, before)
  assert.deepEqual(fiber._config, { count: 0 })
  assert.equal(starts, 1)
  fiber.update({ count: 2 })
  await fiber.await()
  assert.equal(starts, 2)
  assert.deepEqual(fiber.config, { count: 2 })
})
