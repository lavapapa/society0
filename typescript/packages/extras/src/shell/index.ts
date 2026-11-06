import { Context, Service } from '@society0/cordis';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { TaskScope } from '@society0/host/tasks';
import type { Invocation } from '@society0/core/contracts/common';
import type { ArtifactRef } from '@society0/core/artifacts-local';
import '@society0/host/tasks';
import '@society0/core/artifacts-local';
import '@society0/core/attempts';
import '@society0/core/sqlite';
import { runProcess, type NativeProcessReceipt } from '../workspace/native.js';
import { Receipt, parseReceipt, ShellExecutionError, ShellTimeoutError, type ShellReceipt } from './receipt.js';
export { Receipt, ShellExecutionError, ShellTimeoutError, type ShellReceipt };
export interface CommandRequest {
    readonly argv: readonly string[];
    readonly cwd: string;
    readonly env?: Readonly<Record<string, string>>;
    readonly attempt?: number;
    readonly maxOutputBytes?: number;
}
export const Config = Schema.object({ timeoutMs: Schema.natural().min(1).default(30000), maxOutputBytes: Schema.natural().min(1).default(16777216), chunkBytes: Schema.natural().min(1).default(4096), killAfterMs: Schema.natural().min(1).default(1000) });
export const metadata: PluginMetadata = { title: 'shell', summary: 'Explicit-cwd research process with immutable outputs and physical attempt settlement', configVersion: 1, configPolicy: [{ path: '/timeoutMs', mode: 'new-run', affectsResults: true }, { path: '/maxOutputBytes', mode: 'new-run', affectsResults: true }, { path: '/chunkBytes', mode: 'new-run', affectsResults: true }, { path: '/killAfterMs', mode: 'new-run', affectsResults: true }], scope: 'generation', allowedScopes: ['generation'], hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './shell', provides: [{ key: 'shell', protocol: 'shell', major: 1, capabilities: [] }], requires: ['database', 'artifacts', 'attempts', 'tasks'].map(key => ({ service: { key, protocol: key, major: 1, capabilities: [] }, optional: false, phase: 'module' })) };
function status(native: NativeProcessReceipt): ShellReceipt['status'] { if (native.spawnError)
    return 'spawn-error'; if (native.outputError)
    return 'output-error'; if (native.truncated)
    return 'output-limit'; if (native.cancelRequested)
    return 'cancelled'; return 'completed'; }
async function* chunks(source: AsyncIterable<Uint8Array>, size: number) { for await (const bytes of source)
    for (let offset = 0; offset < bytes.byteLength; offset += size)
        yield bytes.subarray(offset, offset + size); }
declare module '@society0/cordis' {
    interface Context {
        shell: Shell;
    }
}
export class Shell extends Service {
    static Config = Config;
    static inject = ['database', 'artifacts', 'attempts', 'tasks'];
    private owner!: Context;
    private tasks!: TaskScope;
    constructor(ctx: Context, private options: ReturnType<typeof Config>) { super(ctx, 'shell'); }
    [Service.init]() { this.owner = this.ctx; this.tasks = this.ctx.tasks.bind(); }
    private outputBudget(request: CommandRequest): number { const bytes = request.maxOutputBytes ?? this.options.maxOutputBytes; if (!Number.isSafeInteger(bytes) || bytes < 1 || bytes > this.options.maxOutputBytes)
        throw Error('SHELL_OUTPUT_BUDGET'); return bytes; }
    async execute(request: CommandRequest, invocation: Invocation): Promise<ShellReceipt> {
        if (invocation.runId !== this.owner.database.runId)
            throw Error('SHELL_RUN_MISMATCH');
        invocation.signal.throwIfAborted();
        const maxOutputBytes = this.outputBudget(request);
        const timeout = AbortSignal.timeout(this.options.timeoutMs);
        const rejected = (receipt: ShellReceipt) => timeout.aborted && !invocation.signal.aborted ? new ShellTimeoutError(receipt) : new ShellExecutionError(receipt);
        let receipt: ShellReceipt | undefined;
        const task = this.tasks.run('shell:' + invocation.operationId, async (signal) => { receipt = await this.perform(request, invocation, maxOutputBytes, AbortSignal.any([signal, timeout])); return receipt; }, { signal: invocation.signal, bytes: this.options.chunkBytes * 2 });
        try {
            await task;
        }
        catch (error) {
            await task.settled;
            if (receipt)
                throw rejected(receipt);
            if (timeout.aborted && !invocation.signal.aborted)
                throw Object.assign(Error('SHELL_TIMEOUT'), { code: 'SHELL_TIMEOUT', cause: error });
            throw error;
        }
        finally {
            await task.settled;
        }
        if (!receipt)
            throw Error('SHELL_RECEIPT_MISSING');
        if (receipt.status !== 'completed')
            throw rejected(receipt);
        return receipt;
    }
    private async perform(request: CommandRequest, invocation: Invocation, maxOutputBytes: number, signal: AbortSignal): Promise<ShellReceipt> {
        const attempt = { lineage: this.owner.attempts.authority().lineage, operationId: invocation.operationId, attempt: request.attempt ?? 0 };
        const claim = await this.owner.attempts.claim(attempt, { kind: 'native-shell/v1', runId: invocation.runId, generation: invocation.generation, argv: request.argv, cwd: request.cwd, env: request.env ?? null, maxOutputBytes });
        if (claim.status === 'unknown')
            throw Error('SHELL_ATTEMPT_UNKNOWN');
        if (claim.status === 'existing') {
            if (claim.record.status !== 'settled')
                throw Error('SHELL_ATTEMPT_PENDING');
            return parseReceipt(claim.record.result);
        }
        let stdout: ArtifactRef | undefined, stderr: ArtifactRef | undefined;
        try {
            const native = await runProcess(request.argv, { cwd: request.cwd, env: request.env, signal, maxOutputBytes, killAfterMs: this.options.killAfterMs, stdout: async (source) => { stdout = await this.owner.artifacts.stage(chunks(source, this.options.chunkBytes), 'application/octet-stream'); }, stderr: async (source) => { stderr = await this.owner.artifacts.stage(chunks(source, this.options.chunkBytes), 'application/octet-stream'); } });
            const receipt: ShellReceipt = { kind: 'native-shell/v1', runId: invocation.runId, operationId: invocation.operationId, attempt, physicalRequestId: claim.physicalRequestId, status: status(native), native, stdout, stderr };
            await this.owner.attempts.settle(attempt, receipt, [stdout, stderr].filter((value): value is ArtifactRef => value !== undefined));
            return receipt;
        }
        catch (error) {
            await this.owner.attempts.markUnknown(attempt, String(error));
            throw error;
        }
    }
    probe(operationId: string, attempt = 0) { return this.owner.attempts.probe({ lineage: this.owner.attempts.authority().lineage, operationId, attempt }); }
}
export default Shell;
