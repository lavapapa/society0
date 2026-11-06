import { ShellPool, type ShellInvocationSink, type WorkerSlot } from './pool.js';
import type { ExplorationFiles, FileLimits } from './filesystem.js';
export interface ShellOptions extends FileLimits { workers: number; maxCommandCount: number; maxCommandBytes: number; maxPendingRpc: number; timeoutMs: number; maxWorkerMb: number }
interface ShellResult { exitCode: number; stdout: string; stderr: string }
export class ShellInvocation implements ShellInvocationSink {
    private accepted = new Set<Promise<void>>();
    private result?: ShellResult;
    private failure?: unknown;
    private accepting = true;
    private slot?: WorkerSlot;
    private readonly settled = Promise.withResolvers<void>();
    constructor(private command: string, private files: ExplorationFiles, private config: ShellOptions, private signal: AbortSignal, private pool: ShellPool) { }
    private stop(reason: unknown) {
        this.failure ??= reason;
        this.accepting = false;
        if (this.slot) this.slot.terminate();
        else this.settled.resolve();
    }
    private abort = () => this.stop(this.signal.reason);
    private send(message: unknown) { if (this.accepting && this.slot) this.slot.post(message); }
    private rpcFailure(id: unknown, error: unknown) {
        const code = (error as { code?: string })?.code;
        if (['SQLITE_COMMIT_UNKNOWN', 'SQLITE_SEGMENT_FAILED', 'SQLITE_WORKER_LOST'].includes(code ?? '')) {
            this.stop(error);
            return;
        }
        this.send({ id, error: { message: String(error), code } });
    }
    private admit(message: any) {
        if (!this.accepting)
            return false;
        if (this.accepted.size >= this.config.maxPendingRpc || Buffer.byteLength(JSON.stringify(message)) > this.config.maxRpcBytes) {
            this.stop(Error('SHELL_RPC_BUDGET'));
            return false;
        }
        return true;
    }
    private rpc(message: any) {
        if (!this.admit(message))
            return;
        const work = this.files.dispatch(message.op, message.args).then(value => this.send({ id: message.id, value }), error => this.rpcFailure(message.id, error));
        this.accepted.add(work);
        void work.then(() => this.accepted.delete(work), error => { this.accepted.delete(work); this.stop(error); });
    }
    receive = (message: any) => {
        if (!this.accepting)
            return;
        if (message.kind === 'done') {
            this.result = message.result;
            this.accepting = false;
            this.settled.resolve();
            return;
        }
        if (message.kind === 'failure') {
            this.stop(Object.assign(Error(message.error.message), { code: message.error.code }));
            return;
        }
        this.rpc(message);
    };
    workerFailed = (error: unknown) => this.stop(error);
    async run(): Promise<ShellResult> {
        this.signal.throwIfAborted();
        this.signal.addEventListener('abort', this.abort, { once: true });
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {
            const slot = await this.pool.acquire(this.signal);
            this.slot = slot;
            if (!this.accepting || this.failure) {
                this.pool.release(slot);
                throw this.failure ?? Error('SHELL_ABORTED');
            }
            slot.attach(this);
            // 命令预算只约束命令本身：Worker 已加载就绪后才开始计时。
            timer = setTimeout(() => this.stop(Error('SHELL_TIMEOUT')), this.config.timeoutMs);
            slot.post({ kind: 'run', command: this.command });
            await Promise.race([
                this.settled.promise,
                slot.exited.then(() => {
                    // 正常 done 之后 Worker 仍驻留；只有提前退出才落到这里。
                    if (!this.result && !this.failure)
                        this.failure = Error('SHELL_WORKER_EXIT');
                    this.settled.resolve();
                })
            ]);
            await Promise.allSettled([...this.accepted]);
            if (this.failure)
                throw this.failure;
            if (!this.result)
                throw Error('SHELL_WORKER_EXIT');
            if (Buffer.byteLength(this.result.stdout) + Buffer.byteLength(this.result.stderr) > this.config.maxOutputBytes)
                throw Error('SHELL_OUTPUT_BYTES');
            return this.result;
        }
        finally {
            if (timer) clearTimeout(timer);
            this.signal.removeEventListener('abort', this.abort);
            if (this.slot) {
                this.slot.detach(this);
                if (this.failure)
                    this.pool.retire(this.slot);
                else
                    this.pool.release(this.slot);
            }
        }
    }
}
