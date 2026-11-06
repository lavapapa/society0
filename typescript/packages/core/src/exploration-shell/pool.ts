import { Worker } from 'node:worker_threads';
import type { ShellOptions } from './invocation.js';

/** 池与 Worker 创建计数：测试和基准读取，不参与行为。 */
export const shellPoolMetrics = { workersCreated: 0 };

/** 一次命令在 Worker 上的接收端。池只把消息路由给当前命令。 */
export interface ShellInvocationSink {
    receive(message: any): void;
    workerFailed(error: unknown): void;
}

export class WorkerSlot {
    readonly worker: Worker;
    readonly exited: Promise<void>;
    private resolveExit!: () => void;
    private sink?: ShellInvocationSink;
    constructor(private readonly options: ShellOptions) {
        shellPoolMetrics.workersCreated++;
        this.worker = new Worker(new URL('./worker.js', import.meta.url), {
            workerData: {
                maxCommandCount: options.maxCommandCount, maxCommandBytes: options.maxCommandBytes,
                maxOutputBytes: options.maxOutputBytes, maxFileBytes: options.maxFileBytes,
                maxDirectoryEntries: options.maxDirectoryEntries, maxRpcBytes: options.maxRpcBytes,
                maxPendingRpc: options.maxPendingRpc, timeoutMs: options.timeoutMs, maxWorkerMb: options.maxWorkerMb
            },
            env: {}, execArgv: [], resourceLimits: { maxOldGenerationSizeMb: options.maxWorkerMb }
        });
        this.exited = new Promise<void>(resolve => { this.resolveExit = resolve; });
        this.worker.once('exit', () => this.resolveExit());
        this.worker.on('message', message => this.sink?.receive(message));
        this.worker.once('error', error => this.sink?.workerFailed(error));
    }
    attach(sink: ShellInvocationSink) { this.sink = sink; }
    detach(sink: ShellInvocationSink) { if (this.sink === sink) this.sink = undefined; }
    post(message: unknown) { this.worker.postMessage(message); }
    terminate() { void this.worker.terminate(); }
}

/** 常驻 worker 池；每条命令独占一个 slot，超时/失败只退役所在 slot。 */
export class ShellPool {
    private readonly slots: WorkerSlot[] = [];
    private readonly idle: WorkerSlot[] = [];
    private readonly waiters: Array<{ resolve: (slot: WorkerSlot) => void; reject: (error: unknown) => void }> = [];
    private closing = false;
    constructor(private readonly options: ShellOptions) { }
    async acquire(signal: AbortSignal): Promise<WorkerSlot> {
        if (this.closing)
            throw Error('SHELL_CLOSED');
        const idle = this.idle.pop();
        if (idle)
            return idle;
        if (this.slots.length < this.options.workers)
            return this.create();
        return new Promise<WorkerSlot>((resolve, reject) => {
            const waiter = { resolve: (slot: WorkerSlot) => { cleanup(); resolve(slot); }, reject: (error: unknown) => { cleanup(); reject(error); } };
            const abort = () => waiter.reject(signal.reason);
            const cleanup = () => {
                signal.removeEventListener('abort', abort);
                const index = this.waiters.indexOf(waiter);
                if (index >= 0)
                    this.waiters.splice(index, 1);
            };
            this.waiters.push(waiter);
            signal.addEventListener('abort', abort, { once: true });
        });
    }
    release(slot: WorkerSlot) {
        if (this.closing) {
            slot.terminate();
            return;
        }
        const waiter = this.waiters.shift();
        if (waiter)
            waiter.resolve(slot);
        else
            this.idle.push(slot);
    }
    retire(slot: WorkerSlot) {
        const index = this.slots.indexOf(slot);
        if (index >= 0)
            this.slots.splice(index, 1);
        slot.terminate();
        if (this.closing)
            return;
        const waiter = this.waiters.shift();
        if (!waiter)
            return;
        try { waiter.resolve(this.create()); }
        catch (error) { waiter.reject(error); }
    }
    private create() {
        const slot = new WorkerSlot(this.options);
        this.slots.push(slot);
        return slot;
    }
    async close() {
        this.closing = true;
        for (const waiter of this.waiters.splice(0))
            waiter.reject(Error('SHELL_CLOSED'));
        await Promise.all(this.slots.splice(0).map(slot => slot.worker.terminate().catch(() => { })));
    }
}
