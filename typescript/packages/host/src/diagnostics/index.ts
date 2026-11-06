import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '../module-resolver/index.js';
export const Config=Schema.object({level:Schema.union(['debug','info','warn','error']).default('info'),historyLimit:Schema.natural().min(1).default(1000)});
export const metadata:PluginMetadata={"title": "diagnostics", "summary": "记录原生所有者、错误和关闭残留", "configVersion": 1, "configPolicy": [{path:'/level',mode:'live',affectsResults:false},{path:'/historyLimit',mode:'live',affectsResults:false}], "scope": "host", "hostAbi": "society0/host/1"};
export const api:ApiEntry={"exportPath": "./diagnostics", "provides": [{"key": "diagnostics", "protocol": "diagnostics", "major": 1, "capabilities": []}], "requires": []};
import { Context, Service, type Fiber } from '@society0/cordis';

export interface OwnerIdentity { readonly uid: number | null; readonly name: string }
export interface ErrorRecord {
  readonly code: string;
  readonly message: string;
  readonly phase: 'setup' | 'task' | 'cleanup' | 'lifecycle';
  readonly affectsClose?: boolean;
  readonly label?: string;
}
export interface DiagnosticRecord extends ErrorRecord { readonly sequence: number; readonly owner: OwnerIdentity }
export interface OwnerNode extends OwnerIdentity { readonly state: number; readonly effects: readonly string[]; readonly children: readonly OwnerNode[] }
export interface DiagnosticPage {
  readonly errors: readonly DiagnosticRecord[];
  readonly omitted: number;
  readonly closeFailures: number;
  readonly owner: OwnerNode;
}
declare module '@society0/cordis' { interface Context { diagnostics: DiagnosticSink } }
export function errorMessage(error: unknown): string {
  if (error instanceof AggregateError) return `${error.message}: ${error.errors.map(errorMessage).join('; ')}`;
  return error instanceof Error ? error.message : String(error);
}
/** Bootstrap-owned native service; diagnostics remain available throughout child teardown. */
export class DiagnosticSink extends Service {
  static Config=Config;
  private readonly identities = new WeakMap<Fiber, OwnerIdentity>();
  private readonly records: DiagnosticRecord[] = [];
  private sequence = 0;
  private failures = 0;
  private readonly root: Context;
  private readonly options: ReturnType<typeof Config>;
  constructor(ctx: Context, input: Record<string, unknown> = {}) {
    super(ctx, 'diagnostics');
    this.options = Config(input);
    this.root = ctx.root;
    this.owner(ctx.fiber);
    ctx.on('internal/plugin', fiber => { this.owner(fiber); });
    ctx.logger.exporter({export: message => {
      const ranks: Record<string, number> = {debug:10, info:20, warn:30, error:40};
      if ((ranks[message.type] ?? 40) < ranks[this.options.level]) return;
      const fiber = message.fiber?.deref() ?? ctx.fiber;
      this.report({code:'CORDIS_LIFECYCLE_ERROR', message:message.args.map(errorMessage).join(' '), phase:'lifecycle', affectsClose:true}, this.owner(fiber));
    }});
  }
  owner(fiber: Fiber): OwnerIdentity {
    let owner = this.identities.get(fiber);
    if (!owner) { owner = {uid:fiber.uid, name:fiber.name}; this.identities.set(fiber, owner); }
    return owner;
  }
  report(error: ErrorRecord, owner: OwnerIdentity = this.owner(this.ctx.fiber)): void {
    this.records.push({...error, owner:{...owner}, sequence:++this.sequence});
    if (error.affectsClose) this.failures++;
    if (this.records.length > this.options.historyLimit) this.records.shift();
  }
  snapshot(): DiagnosticPage {
    const node = (fiber: Fiber): OwnerNode => ({...this.owner(fiber), state:fiber.state, effects:fiber.getEffects().map(effect=>effect.label), children:[...fiber.children].map(node)});
    return {errors:this.records.map(record=>({...record, owner:{...record.owner}})), omitted:this.sequence-this.records.length, closeFailures:this.failures, owner:node(this.root.fiber)};
  }
}

export default DiagnosticSink;
