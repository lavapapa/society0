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
    /** Worker 模块加载完成、可以接收命令。池在命令计时前等它。 */
    readonly ready: Promise<void>;
    private resolveExit!: () => void;
    private resolveReady!: () => void;
    private rejectReady!: (error: unknown) => void;
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
        this.ready = new Promise<void>((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
        // 预热阶段的 Worker 可能还没被 acquire 就退出；这里先吃掉拒绝，避免未处理拒绝。
        void this.ready.catch(() => { });
        this.worker.once('exit', () => { this.rejectReady(Error('SHELL_WORKER_EXIT')); this.resolveExit(); });
        this.worker.on('message', message => {
            if (message?.kind === 'ready') {
                this.resolveReady();
                return;
            }
            this.sink?.receive(message);
        });
        this.worker.once('error', error => { this.rejectReady(error); this.sink?.workerFailed(error); });
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
    constructor(private readonly options: ShellOptions) {
        // 预热：插件启动时就建满池并开始加载 Worker 模块，冷启动不落在第一条命令的关键路径上。
        for (let index = 0; index < options.workers; index++)
            this.offer(this.create());
    }
    async acquire(signal: AbortSignal): Promise<WorkerSlot> {
        for (;;) {
            if (this.closing)
                throw Error('SHELL_CLOSED');
            const idle = this.idle.pop();
            if (idle) {
                if (await this.waitReady(idle, signal))
                    return idle;
                continue;
            }
            if (this.slots.length < this.options.workers) {
                const slot = this.create();
                if (await this.waitReady(slot, signal))
                    return slot;
                continue;
            }
            const slot = await this.wait(signal);
            if (await this.waitReady(slot, signal))
                return slot;
        }
    }
    release(slot: WorkerSlot) {
        if (this.closing) {
            slot.terminate();
            return;
        }
        this.offer(slot);
    }
    retire(slot: WorkerSlot) {
        this.drop(slot);
    }
    async close() {
        this.closing = true;
        for (const waiter of this.waiters.splice(0))
            waiter.reject(Error('SHELL_CLOSED'));
        this.idle.length = 0;
        await Promise.all(this.slots.splice(0).map(slot => slot.worker.terminate().catch(() => { })));
    }
    /** 等 Worker 模块就绪；就绪前被中止时把 slot 交还池，就绪失败则退役。 */
    private async waitReady(slot: WorkerSlot, signal: AbortSignal): Promise<boolean> {
        if (signal.aborted) {
            this.offer(slot);
            throw signal.reason;
        }
        const abort = Promise.withResolvers<never>();
        const onAbort = () => abort.reject(signal.reason);
        signal.addEventListener('abort', onAbort, { once: true });
        try {
            await Promise.race([slot.ready, abort.promise]);
            return true;
        }
        catch (error) {
            if (signal.aborted) {
                this.offer(slot);
                throw signal.reason ?? error;
            }
            this.drop(slot);
            return false;
        }
        finally {
            signal.removeEventListener('abort', onAbort);
        }
    }
    private wait(signal: AbortSignal): Promise<WorkerSlot> {
        return new Promise<WorkerSlot>((resolve, reject) => {
            const waiter = {
                resolve: (slot: WorkerSlot) => { cleanup(); resolve(slot); },
                reject: (error: unknown) => { cleanup(); reject(error); },
            };
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
    /** 把 slot 交给等待者，否则放回空闲队列。 */
    private offer(slot: WorkerSlot) {
        const waiter = this.waiters.shift();
        if (waiter)
            waiter.resolve(slot);
        else
            this.idle.push(slot);
    }
    /** 退役 slot 并立即补建，保持池容量不依赖下一条命令。 */
    private drop(slot: WorkerSlot) {
        const index = this.slots.indexOf(slot);
        if (index >= 0)
            this.slots.splice(index, 1);
        slot.terminate();
        this.refill();
    }
    private refill() {
        if (this.closing)
            return;
        while (this.slots.length < this.options.workers)
            this.offer(this.create());
    }
    private create() {
        const slot = new WorkerSlot(this.options);
        this.slots.push(slot);
        return slot;
    }
}
