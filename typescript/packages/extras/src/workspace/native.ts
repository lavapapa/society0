import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import type { Readable } from 'node:stream';
export interface NativeProcessOptions {
    readonly cwd: string;
    readonly env?: NodeJS.ProcessEnv;
    readonly signal: AbortSignal;
    readonly maxOutputBytes: number;
    readonly killAfterMs: number;
    stdout(source: AsyncIterable<Uint8Array>): Promise<unknown>;
    stderr(source: AsyncIterable<Uint8Array>): Promise<unknown>;
}
export interface NativeProcessReceipt {
    readonly pid?: number;
    readonly started: boolean;
    readonly exitCode: number | null;
    readonly exitSignal: string | null;
    readonly cancelRequested: boolean;
    readonly cancelRequestedAt?: number;
    readonly exitedAt?: number;
    readonly closedAt: number;
    readonly stdoutBytes: string;
    readonly stderrBytes: string;
    readonly deliveredBytes: string;
    readonly truncated: boolean;
    readonly spawnError?: string;
    readonly outputError?: string;
}
interface ProcessState {
    started: boolean;
    exitCode: number | null;
    exitSignal: NodeJS.Signals | null;
    cancelRequested: boolean;
    cancelRequestedAt?: number;
    exitedAt?: number;
    closedAt: number;
    stdoutBytes: number;
    stderrBytes: number;
    deliveredBytes: number;
    truncated: boolean;
    spawnError?: string;
    outputError?: string;
}
function killGroup(child: ChildProcessWithoutNullStreams, signal: NodeJS.Signals): void {
    if (child.pid === undefined)
        return;
    try {
        process.kill(-child.pid, signal);
    }
    catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ESRCH')
            throw error;
    }
}
async function* output(source: Readable, kind: 'stdoutBytes' | 'stderrBytes', state: ProcessState, options: NativeProcessOptions, stop: () => void): AsyncIterable<Uint8Array> {
    for await (const raw of source) {
        const chunk: Buffer = raw;
        state[kind] += chunk.length;
        const available = Math.max(0, options.maxOutputBytes - state.deliveredBytes), accepted = Math.min(available, chunk.length);
        if (accepted) {
            state.deliveredBytes += accepted;
            yield chunk.subarray(0, accepted);
        }
        if (accepted < chunk.length && !state.truncated) {
            state.truncated = true;
            stop();
        }
    }
}
async function consume(source: AsyncIterable<Uint8Array>, sink: (source: AsyncIterable<Uint8Array>) => Promise<unknown>, state: ProcessState, stop: () => void): Promise<void> {
    try {
        await sink(source);
    }
    catch (error) {
        state.outputError ??= String(error);
        stop();
    }
    finally {
        try {
            for await (const ignored of source) {
                void ignored;
            }
        }
        catch (error) {
            state.outputError ??= String(error);
            stop();
        }
    }
}
export async function runProcess(argv: readonly string[], options: NativeProcessOptions): Promise<NativeProcessReceipt> {
    options.signal.throwIfAborted();
    if (process.platform === 'win32')
        throw Error('NATIVE_SHELL_POSIX_REQUIRED');
    if (!argv.length)
        throw Error('NATIVE_SHELL_ARGV');
    for (const value of [options.maxOutputBytes, options.killAfterMs])
        if (!Number.isSafeInteger(value) || value < 1)
            throw Error('NATIVE_SHELL_BUDGET');
    const child = spawn(argv[0]!, argv.slice(1), { cwd: options.cwd, env: options.env, detached: true, stdio: ['pipe', 'pipe', 'pipe'] });
    child.stdin.end();
    const state: ProcessState = { started: false, exitCode: null, exitSignal: null, cancelRequested: false, closedAt: 0, stdoutBytes: 0, stderrBytes: 0, deliveredBytes: 0, truncated: false };
    let timer: NodeJS.Timeout | undefined;
    const stop = () => { if (timer)
        return; killGroup(child, 'SIGTERM'); timer = setTimeout(() => killGroup(child, 'SIGKILL'), options.killAfterMs); timer.unref(); };
    const cancel = () => { state.cancelRequested = true; state.cancelRequestedAt ??= Date.now(); stop(); };
    child.once('spawn', () => { state.started = true; });
    child.on('error', error => { state.spawnError = String(error); });
    child.once('exit', (code, signal) => { state.exitCode = code; state.exitSignal = signal; state.exitedAt = Date.now(); });
    const closed = new Promise<void>(resolve => child.once('close', () => { state.closedAt = Date.now(); resolve(); }));
    options.signal.addEventListener('abort', cancel, { once: true });
    if (options.signal.aborted)
        cancel();
    try {
        await Promise.all([consume(output(child.stdout, 'stdoutBytes', state, options, stop), options.stdout, state, stop), consume(output(child.stderr, 'stderrBytes', state, options, stop), options.stderr, state, stop), closed]);
        return { ...state, pid: child.pid, stdoutBytes: String(state.stdoutBytes), stderrBytes: String(state.stderrBytes), deliveredBytes: String(state.deliveredBytes) };
    }
    finally {
        options.signal.removeEventListener('abort', cancel);
        if (timer)
            clearTimeout(timer);
    }
}
