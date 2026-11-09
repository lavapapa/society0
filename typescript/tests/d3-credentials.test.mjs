import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

test('D3 L2 driver keeps gateway credentials in memory and never writes a temp file', async () => {
  const source = await readFile(new URL('./live/l2-run.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /gateway-credentials/);
  assert.doesNotMatch(source, /FileCredentialStore/);
  assert.match(source, /class MemoryCredentialStore/);
  assert.match(source, /ctx\.modelCredentials\.register\('authorized-local', credentials\)/);
});
