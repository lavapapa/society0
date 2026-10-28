import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHost, Context, Service } from '../.build/host/dist/index.js';
test('PR01-T04 explicit empty boot has a native context and no domain services', async () => {
  const host = await createHost({ boot: [] });
  assert(host.ctx instanceof Context);
  assert.equal(host.ctx.registry.size, 0);
  assert.equal(host.ctx.get('database'), undefined);
  assert.equal(host.ctx.get('model'), undefined);
  await host.close(); await host.close();
  assert.equal(host.ctx.registry.size, 0);
});
test('boot seed is required', async () => {
  await assert.rejects(() => createHost({}), /boot/);
});
