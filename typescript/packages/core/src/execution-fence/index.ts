import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import { ExecutionGate, type ExecutionGateOptions, type WriteLease, type PublicationLease } from './state.js';
export * from './state.js';
export interface FenceConfig { readonly maxAcceptedCommits?: number }
export const Config = Schema.object({
  maxAcceptedCommits: Schema.natural().min(1).max(Number.MAX_SAFE_INTEGER).default(128),
});
export const metadata: PluginMetadata = {
  title:'执行租约',summary:'隔离运行内的写接纳、实际提交许可与完整点封存',
  configVersion:1,scope:'run',hostAbi:'society0/host/1',
  configPolicy:[{path:'/maxAcceptedCommits',mode:'boundary',affectsResults:false}],
};
export const api: ApiEntry = {
  exportPath:'./execution-fence',
  provides:[{key:'executionFence',protocol:'executionFence',major:1,capabilities:[]}],
  requires:[{service:{key:'tasks',protocol:'tasks',major:1,capabilities:[]},optional:false,phase:'module'}],
};
declare module '@society0/host' { interface Context { executionFence: ExecutionFence } }
/** 每个run的数据库登记读取能力；登记生命周期由调用者的原生effect持有。 */
export class ExecutionFence extends Service {
  static inject = ['tasks'];
  static Config = Config;
  private readonly binding: { gate?: ExecutionGate } = {};
  private readonly capacity: number;
  constructor(ctx: Context, config: FenceConfig = {}) {
    super(ctx,'executionFence');this.capacity=Config(config).maxAcceptedCommits;
  }
  bind(options: Omit<ExecutionGateOptions,'maxAcceptedCommits'>): () => Promise<void> {
    if(this.binding.gate)throw new Error('FENCE_BOUND: a database is already registered in this run');
    const gate=new ExecutionGate({...options,maxAcceptedCommits:this.capacity});
    const dispose=this.ctx.effect(()=>async()=>{
      await gate.close();
      if(this.binding.gate===gate)this.binding.gate=undefined;
    });
    this.binding.gate=gate;
    return dispose;
  }
  private current(): ExecutionGate {
    if(!this.binding.gate)throw new Error('FENCE_UNBOUND: initialize the run database before admission');
    return this.binding.gate;
  }
  reserve(proposal: { readonly id: string; readonly observedRevision?: string }, signal?: AbortSignal) { return this.current().reserve(proposal, signal); }
  enter(lease: WriteLease) { return this.current().enter(lease); }
  seal(signal?: AbortSignal) { return this.current().seal(signal); }
  assertPublication(lease: PublicationLease): void { this.current().assertPublication(lease); }
  resume(lease: PublicationLease): void { this.current().resume(lease); }
  halt(reason: string): void { this.binding.gate?.halt(reason); }
  snapshot() { return this.binding.gate?.snapshot() ?? {state:'unbound' as const,acceptedCommits:0}; }
}
export default ExecutionFence;
