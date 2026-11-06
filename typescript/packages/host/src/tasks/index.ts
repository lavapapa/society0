import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '../module-resolver/index.js';
export const Config=Schema.object({concurrency:Schema.number().min(1).max(Number.MAX_SAFE_INTEGER).step(1).default(4),maxPending:Schema.number().min(0).max(Number.MAX_SAFE_INTEGER).step(1).default(1024),maxBytes:Schema.number().min(0).max(Number.MAX_SAFE_INTEGER).step(1).default(64*1024*1024),shutdownTimeoutMs:Schema.number().min(1).max(Number.MAX_SAFE_INTEGER).step(1).default(30000)});
export const metadata:PluginMetadata={"title": "tasks", "summary": "监督调用者任务、准入容量和物理排空", "configVersion": 1, "configPolicy": [{"path": "/concurrency", "mode": "recreate", "affectsResults": false}, {"path": "/maxPending", "mode": "recreate", "affectsResults": false}, {"path": "/maxBytes", "mode": "recreate", "affectsResults": false}, {"path": "/shutdownTimeoutMs", "mode": "recreate", "affectsResults": false}], "scope": "host", "hostAbi": "society0/host/1"};
export const api:ApiEntry={"exportPath": "./tasks", "provides": [{"key": "tasks", "protocol": "tasks", "major": 1, "capabilities": []}], "requires": [{"service": {"key": "resources", "protocol": "resources", "major": 1, "capabilities": []}, "optional": false, "phase": "module"}, {"service": {"key": "diagnostics", "protocol": "diagnostics", "major": 1, "capabilities": []}, "optional": false, "phase": "module"}]};
import { AsyncLocalStorage } from 'node:async_hooks';
import { Context, Service, FiberState, type Fiber } from '@society0/cordis';
import pLimit, { type LimitFunction } from 'p-limit';
import { errorMessage, type DiagnosticSink, type OwnerIdentity } from '../diagnostics/index.js';

export interface TaskConfig { readonly concurrency: number; readonly maxPending: number; readonly maxBytes: number; readonly shutdownTimeoutMs: number }
/** Resolves after actual completion or removal before dispatch, regardless of outcome. */
export type TaskPromise<T> = Promise<T> & { readonly settled: Promise<void> };
export interface TaskOptions { readonly signal?: AbortSignal; readonly bytes?: number }
export interface TaskScope {
  run<T>(label: string, task: (signal: AbortSignal) => Promise<T>, options?: TaskOptions): TaskPromise<T>;
  drain(reason: string): Promise<void>;
  requestClose(reason: string): void;
}
export interface TaskRecord { readonly id: number; readonly label: string; readonly owner: OwnerIdentity; readonly state: 'pending' | 'active'; readonly bytes: number; readonly cancelRequested: boolean }
export interface TaskSnapshot {
  readonly activeCount: number; readonly pendingCount: number;
  readonly activeBytes: number; readonly pendingBytes: number;
  readonly tasks: readonly TaskRecord[];
}
interface Job {
  id: number; label: string; bytes: number; state: 'pending' | 'active';
  controller: AbortController; physical: Promise<unknown>;
}
interface OwnerTasks {
  fiber: Fiber; identity: OwnerIdentity; limit: LimitFunction; jobs: Set<Job>;
  closing: boolean; drain?: Promise<void>;
}
declare module '@society0/cordis' { interface Context { tasks: TaskSupervisor } }
function aborted(reason: string): Error { return new DOMException(reason, 'AbortError'); }
function configOf(input: Partial<TaskConfig>): TaskConfig {
  const config = {concurrency:4,maxPending:1024,maxBytes:64*1024*1024,shutdownTimeoutMs:30000,...input};
  for (const [name,value] of Object.entries(config)) {
    const positive = name === 'concurrency' || name === 'shutdownTimeoutMs';
    if (!Number.isSafeInteger(value) || value < (positive ? 1 : 0)) throw new TypeError(`TASK_CONFIG: ${name} must be a ${positive ? 'positive' : 'nonnegative'} integer`);
  }
  return config;
}
function cancellationError(signal: AbortSignal, error: unknown): boolean {
  return signal.aborted && (error === signal.reason || error instanceof Error && error.name === 'AbortError');
}
function taskWaiter<T>(physical: Promise<T>, signal: AbortSignal): TaskPromise<T> {
  const waiter = new Promise<T>((resolve,reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener('abort',abort,{once:true});
    physical.then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));
    if (signal.aborted) abort();
  });
  // Supervised fire-and-forget callers still leave observable task failures.
  void waiter.catch(()=>{});
  return Object.assign(waiter,{settled:physical.then(()=>{},()=>{})});
}
/** Capacity is per calling Fiber; its native effect owns cancellation and physical drain. */
export class TaskSupervisor extends Service {
  static Config=Config;
  static inject = ['resources','diagnostics'];
  private readonly owners = new Map<Fiber, OwnerTasks>();
  private readonly execution = new AsyncLocalStorage<Fiber>();
  private readonly config: TaskConfig;
  private readonly diagnostics: DiagnosticSink;
  private counter = 0;
  constructor(ctx: Context, config: Partial<TaskConfig> = {}) {
    super(ctx, 'tasks'); this.config=configOf(config); this.diagnostics=ctx.diagnostics;
  }
  private owner(): OwnerTasks {
    const fiber=this.ctx.fiber;
    fiber.assertActive();
    if (fiber.state!==FiberState.ACTIVE && fiber.state!==FiberState.LOADING) throw new Error('TASK_OWNER_INACTIVE: task admission requires an active activation');
    const existing=this.owners.get(fiber);
    if (existing) return existing;
    const owner: OwnerTasks={fiber,identity:this.diagnostics.owner(fiber),limit:pLimit({concurrency:this.config.concurrency,rejectOnClear:true}),jobs:new Set(),closing:false};
    this.ctx.resources.use('tasks.drain', async()=>{
      await this.drainOwner(owner,'owner closing');
      this.owners.delete(fiber);
    });
    this.owners.set(fiber,owner);
    return owner;
  }
  /** Prepare a physical executor during setup for services whose old bindings serve cleanup. */
  bind(): TaskScope {
    const owner=this.owner();
    return {
      run:<T>(label: string, task: (signal: AbortSignal)=>Promise<T>, options: TaskOptions={})=>this.runOwned(owner,label,task,options),
      drain:reason=>this.drainBound(owner,reason),
      requestClose:reason=>this.closeBound(owner,reason),
    };
  }
  run<T>(label: string, task: (signal: AbortSignal) => Promise<T>, options: TaskOptions = {}): TaskPromise<T> {
    return this.runOwned(this.owner(),label,task,options);
  }
  private admission(owner: OwnerTasks, label: string, options: TaskOptions): number {
    const bytes = options.bytes ?? 0;
    if (owner.closing) throw new Error(`TASK_OWNER_CLOSING: ${owner.identity.name}`);
    if (!Number.isSafeInteger(bytes) || bytes < 0) throw new TypeError('TASK_BYTES: expected a nonnegative integer');
    options.signal?.throwIfAborted();
    this.checkCapacity(owner,label,bytes);
    return bytes;
  }
  private checkCapacity(owner: OwnerTasks, label: string, bytes: number): void {
    if (owner.limit.activeCount >= this.config.concurrency && owner.limit.pendingCount >= this.config.maxPending) throw new Error(`TASK_PENDING_LIMIT: ${label}`);
    const occupied = [...owner.jobs].reduce((sum,job)=>sum+job.bytes,0);
    if (occupied + bytes > this.config.maxBytes) throw new Error(`TASK_BYTE_LIMIT: ${label}; occupied=${occupied}; requested=${bytes}; limit=${this.config.maxBytes}`);
  }
  private observe(owner: OwnerTasks, job: Job, signal: AbortSignal | undefined, cancel: () => void): void {
    const cleanup = () => { owner.jobs.delete(job); signal?.removeEventListener('abort',cancel); };
    void job.physical.then(cleanup,error => {
      cleanup();
      if (!cancellationError(job.controller.signal,error)) {
        this.diagnostics.report({code:'TASK_FAILED',message:errorMessage(error),label:job.label,phase:'task'},owner.identity);
      }
    });
  }
  private runOwned<T>(owner: OwnerTasks, label: string, task: (signal: AbortSignal) => Promise<T>, options: TaskOptions): TaskPromise<T> {
    const bytes = this.admission(owner,label,options);
    const controller = new AbortController();
    const cancel = () => controller.abort(options.signal!.reason);
    options.signal?.addEventListener('abort',cancel,{once:true});
    const physical = owner.limit(async()=>{
      controller.signal.throwIfAborted();
      job.state = 'active';
      return this.execution.run(owner.fiber,()=>task(controller.signal));
    });
    const job: Job = {id:++this.counter,label,bytes,state:'pending',controller,physical};
    owner.jobs.add(job);
    // Native completion retains capacity even when the outward waiter is cancelled.
    this.observe(owner,job,options.signal,cancel);
    return taskWaiter(physical,controller.signal);
  }
  private stop(owner: OwnerTasks, reason: string): void {
    if (owner.closing) return;
    owner.closing=true;
    for (const job of owner.jobs) job.controller.abort(aborted(reason));
    owner.limit.clearQueue();
  }
  private drainOwner(owner: OwnerTasks, reason: string): Promise<void> {
    this.stop(owner,reason);
    owner.drain ??= Promise.allSettled([...owner.jobs].map(job=>job.physical)).then(()=>{});
    return this.awaitDrain(owner,reason);
  }
  /** 物理任务忽略abort时，关闭等待只到故障上限；未结束任务继续由snapshot跟踪。 */
  private async awaitDrain(owner: OwnerTasks, reason: string): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_,reject)=>{
      timer = setTimeout(()=>{
        const tasks=this.snapshot();
        this.diagnostics.report({code:'TASK_DRAIN_TIMEOUT',message:`task drain remains unfinished after ${this.config.shutdownTimeoutMs}ms (${reason})`,phase:'cleanup',affectsClose:true},owner.identity);
        reject(Object.assign(Error(`TASK_DRAIN_TIMEOUT: still active after ${this.config.shutdownTimeoutMs}ms`),{tasks}));
      },this.config.shutdownTimeoutMs);
      timer.unref();
    });
    try { await Promise.race([owner.drain,deadline]); }
    finally { if (timer) clearTimeout(timer); }
  }
  private drainBound(owner: OwnerTasks, reason: string): Promise<void> {
    if (this.execution.getStore()===owner.fiber) throw new Error('TASK_SELF_JOIN: use requestClose() inside a running task');
    return this.drainOwner(owner,reason);
  }
  drain(reason: string): Promise<void> { return this.drainBound(this.owner(),reason); }
  private closeBound(owner: OwnerTasks, reason: string): void {
    this.stop(owner,reason);
    // Cleanup is a distinct execution, even when the close request comes from a task.
    this.execution.exit(()=>{
      void owner.fiber.dispose().catch(error=>this.diagnostics.report({code:'TASK_OWNER_CLOSE_FAILED',message:errorMessage(error),phase:'cleanup',affectsClose:true},owner.identity));
    });
  }
  requestClose(reason: string): void { this.closeBound(this.owner(),reason); }
  snapshot(): TaskSnapshot {
    const tasks=[...this.owners.values()].flatMap(owner=>[...owner.jobs].map(job=>({id:job.id,label:job.label,owner:{...owner.identity},state:job.state,bytes:job.bytes,cancelRequested:job.controller.signal.aborted})));
    const active=tasks.filter(job=>job.state==='active'),pending=tasks.filter(job=>job.state==='pending');
    return {activeCount:active.length,pendingCount:pending.length,activeBytes:active.reduce((n,job)=>n+job.bytes,0),pendingBytes:pending.reduce((n,job)=>n+job.bytes,0),tasks};
  }
}
export default TaskSupervisor;
