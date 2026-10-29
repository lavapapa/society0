#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { createHost, type BootEntry } from '@society0/host';
import type {} from '@society0/host/commands';

interface Invocation { boot: string; command?: string; input: unknown }

function parseInvocation(argv: string[]): Invocation | number {
  const usage = () => { console.error('Usage: society0 --boot <boot.json> [--command <name> [--input <json>]]'); return 2; };
  let values;
  try { ({values} = parseArgs({args:argv, options:{boot:{type:'string'},command:{type:'string'},input:{type:'string'},help:{type:'boolean',short:'h'}}})); }
  catch { return usage(); }
  if (values.help) {
    console.log('Usage: society0 --boot <boot.json> [--command <name> [--input <json>]]\n\nOptions:\n  --boot <boot.json>  Explicit BootEntry array; package paths resolve from this file.\n  --command <name>    Execute a command registered by the boot plugins.\n  --input <json>      Command input; defaults to {}.\n  --help, -h         Show this help and exit.');
    return 0;
  }
  if (!values.boot || (values.input !== undefined && !values.command)) return usage();
  const input: unknown = values.input === undefined ? {} : JSON.parse(values.input);
  return {boot: values.boot, command: values.command, input};
}

async function main(argv = process.argv.slice(2)): Promise<number> {
  const invocation = parseInvocation(argv);
  if (typeof invocation === 'number') return invocation;
  const {input} = invocation;
  const url = pathToFileURL(resolve(invocation.boot));
  const boot: readonly BootEntry[] = JSON.parse(await readFile(url, 'utf8'));
  const host = await createHost({ boot, baseURL: url.href });
  const requested = Promise.withResolvers<void>();
  const controller = new AbortController();
  let signalCode = 0;
  let closing: ReturnType<typeof host.close> | undefined;
  const close = () => closing ??= host.close();
  const shutdown = () => { requested.resolve(); };
  const interrupt = () => { signalCode=130; controller.abort(new DOMException('SIGINT','AbortError')); shutdown(); };
  const terminate = () => { signalCode=143; controller.abort(new DOMException('SIGTERM','AbortError')); shutdown(); };
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', terminate);
  process.once('beforeExit', shutdown);
  try {
    if (invocation.command) {
      const commands=host.ctx.get('commands');
      if (!commands) throw Error('COMMANDS_NOT_AVAILABLE: --boot must install @society0/host/commands');
      const execution=commands.execute(invocation.command,input,{kind:'host',requestId:`cli:${process.pid}`,signal:controller.signal});
      const result=await execution;
      await close();
      if (signalCode) return signalCode;
      console.log(JSON.stringify(result ?? null));
      return 0;
    }
    await requested.promise;
    await close();
    return 0;
  } finally {
    try { await close(); }
    finally {
      process.off('SIGINT', interrupt);
      process.off('SIGTERM', terminate);
      process.off('beforeExit', shutdown);
    }
  }
}
// Keep cleanup in the module's awaited lifetime so unresolved cleanup retains Node's exit 13.
try {
  process.exitCode = await main();
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
