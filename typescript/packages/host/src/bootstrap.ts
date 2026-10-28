import { Context, FiberState, type Fiber, type Plugin } from '@society0/cordis';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { preflight } from './packages.js';

export type Literal = null | boolean | number | string | readonly Literal[] | { readonly [key: string]: Literal };
export interface BootEntry { readonly specifier: string; readonly config?: Literal }
export interface BootSeed { readonly boot: readonly BootEntry[]; readonly baseURL?: string }
export interface Host {
  readonly ctx: Context;
  readonly fibers: readonly Fiber[];
  close(): Promise<void>;
}
function applicable(value: unknown): value is Plugin {
  return typeof value === 'function' || !!value && typeof value === 'object' && typeof Reflect.get(value, 'apply') === 'function';
}
function normalize(namespace: Record<string, unknown>, specifier: string): Plugin {
  if ('default' in namespace && 'apply' in namespace) throw new Error(`PLUGIN_ENTRY_AMBIGUOUS: ${specifier} exports both default and apply`);
  const plugin = 'default' in namespace ? namespace.default : namespace;
  if (!applicable(plugin)) throw new Error(`PLUGIN_ENTRY_INVALID: ${specifier} requires a function, class, or apply object`);
  return plugin;
}
/** Create a native Cordis root from an explicit, preflighted boot seed. */
export async function createHost(seed: BootSeed): Promise<Host> {
  if (!seed || !Array.isArray(seed.boot)) throw new TypeError('createHost requires an explicit boot array');
  const baseURL = seed.baseURL ?? pathToFileURL(resolve('package.json')).href;
  const urls = preflight(seed.boot.map(entry => entry.specifier), baseURL);
  const plugins: Plugin[] = [];
  for (let index = 0; index < urls.length; index++) plugins.push(normalize(await import(urls[index]!), seed.boot[index]!.specifier));
  const ctx = new Context();
  ctx.baseUrl = baseURL;
  const fibers: Fiber[] = [];
  let closing: Promise<void> | undefined;
  const close = () => closing ??= (async () => { await ctx.fiber.dispose(); await ctx.fiber.await(); })();
  try {
    for (let index = 0; index < plugins.length; index++) fibers.push(await ctx.plugin(plugins[index]!, seed.boot[index]!.config));
    await Promise.all(fibers.map(fiber => fiber.await()));
    for (let index = 0; index < fibers.length; index++) {
      if (fibers[index]!.state !== FiberState.ACTIVE) throw new Error(`BOOT_NOT_ACTIVE: ${seed.boot[index]!.specifier}; missing ${Object.keys(fibers[index]!.inject).join(', ')}`);
    }
    return { ctx, fibers, close };
  } catch (error) {
    await close();
    throw error;
  }
}
