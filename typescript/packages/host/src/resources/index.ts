import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '../module-resolver/index.js';
export const Config=Schema.object({closeTimeoutMs:Schema.number().min(0).max(Number.MAX_VALUE).default(30000)});
export const metadata:PluginMetadata={"title": "resources", "summary": "管理调用者拥有的资源与实际关闭结果", "configVersion": 1, "configPolicy": [{"path": "/closeTimeoutMs", "mode": "recreate", "affectsResults": false}], "scope": "host", "hostAbi": "society0/host/1"};
export const api:ApiEntry={"exportPath": "./resources", "provides": [{"key": "resources", "protocol": "resources", "major": 1, "capabilities": []}], "requires": [{"service": {"key": "diagnostics", "protocol": "diagnostics", "major": 1, "capabilities": []}, "optional": false, "phase": "module"}]};
import { Context, Service } from '@society0/cordis';
import { errorMessage, type DiagnosticSink, type OwnerIdentity } from '../diagnostics/index.js';

export interface ResourceConfig { readonly closeTimeoutMs: number }
export interface ResourceValue<T> { readonly value: T; readonly close: () => void | Promise<void> }
export interface ResourceLease<T> { readonly value: T; close(): Promise<void> }
export interface ResourceRecord {
  readonly id: number;
  readonly label: string;
  readonly owner: OwnerIdentity;
  readonly state: 'opening' | 'open' | 'closing' | 'failed';
  readonly error?: string;
}
export interface ResourceReport {
  readonly opening: number; readonly open: number; readonly closing: number; readonly failed: number; readonly closed: number;
  readonly residual: readonly ResourceRecord[];
}
type MutableRecord = {-readonly [K in keyof ResourceRecord]: ResourceRecord[K]};
declare module '@society0/cordis' { interface Context { resources: OwnedResources } }

export class OwnedResources extends Service {
  static Config=Config;
  static inject = ['diagnostics'];
  private readonly records = new Map<number, MutableRecord>();
  private readonly diagnostics: DiagnosticSink;
  private counter = 0;
  private closed = 0;
  readonly closeTimeoutMs: number;
  constructor(ctx: Context, config: Partial<ResourceConfig> = {}) {
    super(ctx, 'resources'); this.diagnostics=ctx.diagnostics;
    this.closeTimeoutMs=config.closeTimeoutMs ?? 30000;
    if (!Number.isFinite(this.closeTimeoutMs) || this.closeTimeoutMs<0) throw new TypeError('RESOURCE_CONFIG: closeTimeoutMs must be nonnegative and finite');
  }
  private record(label: string): MutableRecord {
    const record: MutableRecord = {id:++this.counter, label, owner:this.diagnostics.owner(this.ctx.fiber), state:'open'};
    this.records.set(record.id, record);
    return record;
  }
  private async closeResource(record: MutableRecord, closer: () => void | Promise<void>): Promise<void> {
    record.state = 'closing';
    try {
      await closer();
      this.records.delete(record.id);
      this.closed++;
    } catch (error) {
      record.state = 'failed'; record.error = errorMessage(error);
      this.diagnostics.report({code:'RESOURCE_CLOSE_FAILED', message:record.error, label:record.label, phase:'cleanup', affectsClose:true}, record.owner);
      throw error;
    }
  }
  use(label: string, closer: () => void | Promise<void>): () => void | Promise<void> {
    return this.ctx.effect(() => {
      const record = this.record(label);
      return () => this.closeResource(record, closer);
    }, `resources.use(${label})`);
  }
  async open<T>(factory: () => ResourceValue<T> | Promise<ResourceValue<T>>, label = 'resource'): Promise<ResourceLease<T>> {
    let acquisition!: Promise<ResourceValue<T>>;
    const close = this.ctx.effect(() => {
      const record = this.record(label); record.state = 'opening';
      acquisition = Promise.resolve().then(factory);
      return acquisition.then(resource => {
        record.state = 'open';
        return () => this.closeResource(record, resource.close);
      }, error => {
        record.state = 'failed'; record.error = errorMessage(error);
        this.diagnostics.report({code:'RESOURCE_OPEN_FAILED', message:record.error, label, phase:'setup', affectsClose:true}, record.owner);
        throw error;
      });
    }, `resources.open(${label})`);
    const resource = await acquisition;
    return {value:resource.value, close:() => Promise.resolve(close())};
  }
  closeReport(): ResourceReport {
    const residual = [...this.records.values()].map(record=>({...record, owner:{...record.owner}}));
    return {opening:residual.filter(r=>r.state==='opening').length, open:residual.filter(r=>r.state==='open').length, closing:residual.filter(r=>r.state==='closing').length, failed:residual.filter(r=>r.state==='failed').length, closed:this.closed, residual};
  }
}
export default OwnedResources;
