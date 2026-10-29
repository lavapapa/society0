/** 运行内的执行身份；数据库与完整点消费者仍检查各自的持久事实。 */
export interface WriteLease {
  readonly kind: 'write'; readonly id: string; readonly runId: string;
  readonly generation: string; readonly epoch: string;
}
export interface PublicationLease {
  readonly kind: 'publication'; readonly id: string; readonly runId: string;
  readonly generation: string; readonly sealedRevision: string;
}
export interface CommitPermit {
  readonly signal: AbortSignal;
  assertActive(): void;
  release(): void;
}
export interface ExecutionReservation {
  readonly lease: WriteLease;
  readonly signal: AbortSignal;
  cancel(reason: string): void;
  release(): Promise<void>;
}
export interface ExecutionGateOptions {
  readonly runId: string; readonly generation: string;
  readonly readRevision: () => Promise<string>;
  readonly maxAcceptedCommits: number;
}
type State = { readonly kind: 'idle' | 'admitting' | 'executing' | 'sealing' }
  | { readonly kind: 'sealed'; readonly lease: PublicationLease }
  | { readonly kind: 'halted'; readonly reason: string };
interface ActiveExecution {
  lease: WriteLease; controller: AbortController; closing: boolean;
  permits: Set<CommitPermit>; empty: ReturnType<typeof Promise.withResolvers<void>>;
  release?: Promise<void>;
}
function requireExecution(active: ActiveExecution | undefined, lease: WriteLease, state: State): asserts active is ActiveExecution {
  if (!active || active.lease!==lease || active.closing || active.controller.signal.aborted || state.kind!=='executing') {
    throw new Error('FENCE_LEASE: execution is not active in this run');
  }
}
interface GateControl { state: State; active?: ActiveExecution; }
function requireIdle(state: State): void {
  if (state.kind !== 'idle') throw new Error(`FENCE_BUSY: ${state.kind}`);
}
async function readRevision(options: ExecutionGateOptions): Promise<string> {
  const revision=await options.readRevision();
  if (typeof revision !== 'string' || !/^(0|[1-9][0-9]*)$/.test(revision)) throw new TypeError('FENCE_REVISION: expected canonical unsigned decimal');
  return revision;
}
/** 排空只变更实例私有控制单元；终止原因在物理结算后继续保留。 */
function releaseExecution(control: GateControl, active: ActiveExecution): Promise<void> {
  if(active.release)return active.release;
  active.closing=true;
  if(active.permits.size===0)active.empty.resolve();
  return active.release=active.empty.promise.then(()=>{
    if(control.active===active)control.active=undefined;
    if(control.state.kind==='executing')control.state={kind:'idle'};
  });
}
export class ExecutionGate {
  readonly #options: ExecutionGateOptions;
  readonly #control: GateControl = {state:{kind:'idle'}};
  #epoch = 0n;
  constructor(options: ExecutionGateOptions) {
    if (!options.runId || !options.generation) throw new TypeError('FENCE_IDENTITY: runId and generation are required');
    if (!Number.isSafeInteger(options.maxAcceptedCommits) || options.maxAcceptedCommits < 1) throw new TypeError('FENCE_CAPACITY: expected a positive integer');
    this.#options={...options};
  }
  async reserve(proposal: { readonly id: string; readonly observedRevision?: string }): Promise<ExecutionReservation> {
    requireIdle(this.#control.state);
    const admitting: State={kind:'admitting'};
    this.#control.state=admitting;
    try {
      const revision=await readRevision(this.#options);
      if (this.#control.state !== admitting) throw new Error('FENCE_HALTED: admission ended while reading revision');
      if (proposal.observedRevision !== undefined && proposal.observedRevision !== revision) throw new Error(`FENCE_STALE: proposal ${proposal.id}; observed ${proposal.observedRevision}; current ${revision}`);
      const epoch=String(++this.#epoch);
      const lease: WriteLease=Object.freeze({kind:'write',id:`${this.#options.runId}/${epoch}`,runId:this.#options.runId,generation:this.#options.generation,epoch});
      const active: ActiveExecution={lease,controller:new AbortController(),closing:false,permits:new Set(),empty:Promise.withResolvers<void>()};
      this.#control.active=active;this.#control.state={kind:'executing'};
      return Object.freeze({lease,signal:active.controller.signal,cancel:(reason:string)=>active.controller.abort(new Error(reason)),release:()=>releaseExecution(this.#control,active)});
    } catch (error) {
      if(this.#control.state===admitting)this.#control.state={kind:'idle'};
      throw error;
    }
  }
  enter(lease: WriteLease): CommitPermit {
    const active=this.#control.active;
    requireExecution(active,lease,this.#control.state);
    if(active.permits.size>=this.#options.maxAcceptedCommits)throw new Error('FENCE_CAPACITY: accepted commit limit reached');
    const permit: CommitPermit=Object.freeze({
      signal:active.controller.signal,
      assertActive:()=>{
        if(!active.permits.has(permit))throw new Error('FENCE_PERMIT: commit already settled');
        active.controller.signal.throwIfAborted();
      },
      release:()=>{
        if(active.permits.delete(permit) && active.closing && active.permits.size===0)active.empty.resolve();
      },
    });
    active.permits.add(permit);
    return permit;
  }
  /** 调用方先结算任务与reservation；丢失发布回执时保持封存，等待probe。 */
  async seal(): Promise<PublicationLease> {
    requireIdle(this.#control.state);
    const sealing: State={kind:'sealing'};
    this.#control.state=sealing;
    try {
      const sealedRevision=await readRevision(this.#options);
      if(this.#control.state!==sealing)throw new Error('FENCE_HALTED: sealing ended while reading revision');
      const lease: PublicationLease=Object.freeze({kind:'publication',id:`${this.#options.runId}/publication/${++this.#epoch}`,runId:this.#options.runId,generation:this.#options.generation,sealedRevision});
      this.#control.state={kind:'sealed',lease};return lease;
    } catch(error) {
      this.halt(error instanceof Error ? error.message : String(error));throw error;
    }
  }
  assertPublication(lease: PublicationLease): void {
    if(this.#control.state.kind!=='sealed' || this.#control.state.lease!==lease)throw new Error('FENCE_PUBLICATION: publication lease is not active in this run');
  }
  /** 由确认发布或明确撤销发布的控制器调用；资源dispose没有隐式恢复语义。 */
  resume(lease: PublicationLease): void { this.assertPublication(lease);this.#control.state={kind:'idle'}; }
  halt(reason: string): void {
    if(this.#control.state.kind==='halted')return;
    this.#control.state={kind:'halted',reason};this.#control.active?.controller.abort(new Error(reason));
  }
  close(reason = 'execution owner closed'): Promise<void> {
    this.halt(reason);
    return this.#control.active ? releaseExecution(this.#control,this.#control.active) : Promise.resolve();
  }
  snapshot(): { readonly state: State['kind']; readonly acceptedCommits: number; readonly reason?: string } {
    return {state:this.#control.state.kind,acceptedCommits:this.#control.active?.permits.size??0,...(this.#control.state.kind==='halted'?{reason:this.#control.state.reason}:{})};
  }
}
