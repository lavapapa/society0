import { Context, FiberState, type Fiber, type Plugin } from '@society0/cordis';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { preflight } from './packages.js';
import { unknownConfigPaths } from './config-resolvers/strict.js';
import type { Json } from './config-resolvers/types.js';
import { DiagnosticSink, errorMessage, type DiagnosticPage } from './diagnostics/index.js';
import type { OwnedResources, ResourceReport } from './resources/index.js';
import type { TaskSupervisor, TaskSnapshot } from './tasks/index.js';

export type Literal = null | boolean | number | string | readonly Literal[] | { readonly [key: string]: Literal };
export interface BootEntry { readonly specifier: string; readonly config?: Literal }
export interface BootSeed { readonly boot: readonly BootEntry[]; readonly baseURL?: string }
export interface HostCloseReport { readonly status: 'closed' | 'halted'; readonly drained: boolean; readonly diagnostics: DiagnosticPage; readonly resources?: ResourceReport; readonly tasks?: TaskSnapshot }
export interface HostCloseOptions { readonly timeoutMs?: number }
export class HostCloseError extends Error {
  constructor(readonly report: HostCloseReport) { super(`HOST_HALTED: ${JSON.stringify(report.diagnostics.errors)}`); this.name='HostCloseError'; }
}
export interface Host {
  readonly ctx: Context;
  readonly fibers: readonly Fiber[];
  readonly status: 'running' | 'closing' | 'closed' | 'halted';
  readonly diagnostics: DiagnosticSink;
  close(options?: HostCloseOptions): Promise<HostCloseReport>;
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
function validateTimeout(timeoutMs: number | undefined): void {
  if (timeoutMs !== undefined && (!Number.isFinite(timeoutMs) || timeoutMs < 0)) {
    throw new TypeError('HOST_CLOSE_TIMEOUT: timeoutMs must be nonnegative and finite');
  }
}
class NativeHost implements Host {
  readonly fibers: Fiber[] = [];
  readonly diagnostics: DiagnosticSink;
  status: Host['status'] = 'running';
  private closing?: Promise<HostCloseReport>;
  private resources?: OwnedResources;
  private tasks?: TaskSupervisor;
  constructor(readonly ctx: Context) {
    this.diagnostics = new DiagnosticSink(ctx);
    this.close = this.close.bind(this);
  }
  private report(drained: boolean): HostCloseReport {
    const diagnostics = this.diagnostics.snapshot();
    return {status:diagnostics.closeFailures ? 'halted' : 'closed', drained, diagnostics,
      resources:this.resources?.closeReport(), tasks:this.tasks?.snapshot()};
  }
  private async finishClose(): Promise<HostCloseReport> {
    this.status = 'closing';
    let drained = false;
    try { await this.ctx.fiber.dispose(); await this.ctx.fiber.await(); drained = true; }
    catch (error) { this.diagnostics.report({code:'HOST_CLOSE_FAILED',message:errorMessage(error),phase:'cleanup',affectsClose:true}); }
    const result = this.report(drained);
    this.status = result.status;
    if (this.status === 'halted') throw new HostCloseError(result);
    return result;
  }
  private deadline(closing: Promise<HostCloseReport>, timeoutMs: number | undefined): Promise<HostCloseReport> {
    if (timeoutMs === undefined) return closing;
    return new Promise((resolve,reject) => {
      const timer = setTimeout(() => {
        this.status = 'halted';
        this.diagnostics.report({code:'HOST_CLOSE_TIMEOUT',message:`cleanup remains unfinished after ${timeoutMs}ms`,phase:'cleanup',affectsClose:true});
        reject(new HostCloseError(this.report(false)));
      },timeoutMs);
      closing.then(value=>{clearTimeout(timer);resolve(value)},error=>{clearTimeout(timer);reject(error)});
    });
  }
  close(options: HostCloseOptions = {}): Promise<HostCloseReport> {
    validateTimeout(options.timeoutMs);
    if (!this.closing) {
      this.resources = this.ctx.get('resources');
      this.tasks = this.ctx.get('tasks');
      this.closing = this.finishClose();
    }
    return this.deadline(this.closing, options.timeoutMs ?? this.resources?.closeTimeoutMs);
  }
}
async function boot(host: NativeHost, plugins: readonly Plugin[], entries: readonly BootEntry[]): Promise<void> {
  for (let index = 0; index < plugins.length; index++) {
    const schema = (plugins[index] as { Config?: never }).Config;
    const config = entries[index]!.config;
    if (schema && config !== undefined) {
      const unknown = unknownConfigPaths(schema, config as Json);
      if (unknown.length)
        throw Object.assign(Error(`UNKNOWN_CONFIG_FIELD ${entries[index]!.specifier}${unknown[0]}`), { code: 'UNKNOWN_CONFIG_FIELD', paths: unknown });
    }
    host.fibers.push(await host.ctx.plugin(plugins[index]!, config));
  }
  await Promise.all(host.fibers.map(fiber => fiber.await()));
  for (let index = 0; index < host.fibers.length; index++) {
    const fiber = host.fibers[index]!;
    if (fiber.state !== FiberState.ACTIVE) throw new Error(`BOOT_NOT_ACTIVE: ${entries[index]!.specifier}; missing ${Object.keys(fiber.inject).join(', ')}`);
  }
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
  const host = new NativeHost(ctx);
  try { await boot(host,plugins,seed.boot); return host; }
  catch (error) { await host.close(); throw error; }
}
