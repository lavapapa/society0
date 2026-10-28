#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHost, type BootEntry } from '@society0/host';

async function main(argv = process.argv.slice(2)): Promise<number> {
  if (argv.length !== 2 || argv[0] !== '--boot') {
    console.error('Usage: society0 --boot <boot.json>');
    return 2;
  }
  const url = pathToFileURL(resolve(argv[1]!));
  const boot: readonly BootEntry[] = JSON.parse(await readFile(url, 'utf8'));
  const host = await createHost({ boot, baseURL: url.href });
  const requested = Promise.withResolvers<void>();
  const shutdown = () => { requested.resolve(); };
  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);
  process.once('beforeExit', shutdown);
  try {
    await requested.promise;
    await host.close();
    return 0;
  } finally {
    process.off('SIGINT', shutdown);
    process.off('SIGTERM', shutdown);
    process.off('beforeExit', shutdown);
  }
}
// Keep cleanup in the module's awaited lifetime: an empty event loop with
// unsettled cleanup then follows Node's non-successful top-level-await exit.
try {
  process.exitCode = await main();
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
