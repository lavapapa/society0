import Schema from '@society0/schemastery';
const artifact = Schema.object({ id: Schema.string().required(), bytes: Schema.string().required(), mediaType: Schema.string().required() });
export const Receipt = Schema.object({ kind: Schema.const('native-shell/v1').required(), runId: Schema.string().required(), operationId: Schema.string().required(), attempt: Schema.object({ lineage: Schema.string().required(), operationId: Schema.string().required(), attempt: Schema.natural().required() }).required(), physicalRequestId: Schema.string().required(), status: Schema.union(['completed', 'cancelled', 'output-limit', 'spawn-error', 'output-error']).required(), stdout: artifact, stderr: artifact, native: Schema.object({ pid: Schema.natural(), started: Schema.boolean().required(), exitCode: Schema.number(), exitSignal: Schema.string(), cancelRequested: Schema.boolean().required(), cancelRequestedAt: Schema.number(), exitedAt: Schema.number(), closedAt: Schema.number().required(), stdoutBytes: Schema.string().required(), stderrBytes: Schema.string().required(), deliveredBytes: Schema.string().required(), truncated: Schema.boolean().required(), spawnError: Schema.string(), outputError: Schema.string() }).required() });
import type { ArtifactRef } from '@society0/core/artifacts-local';
import type { AttemptIdentity } from '@society0/core/attempts';
import type { NativeProcessReceipt } from '../workspace/native.js';
export interface ShellReceipt {
    readonly kind: 'native-shell/v1';
    readonly runId: string;
    readonly operationId: string;
    readonly attempt: AttemptIdentity;
    readonly physicalRequestId: string;
    readonly status: 'completed' | 'cancelled' | 'output-limit' | 'spawn-error' | 'output-error';
    readonly stdout?: ArtifactRef;
    readonly stderr?: ArtifactRef;
    readonly native: NativeProcessReceipt;
}
export class ShellExecutionError extends Error {
    code: string;
    constructor(readonly receipt: ShellReceipt) { super('SHELL_' + receipt.status.toUpperCase().replaceAll('-', '_')); this.code = this.message; if (receipt.status === 'cancelled')
        this.name = 'AbortError'; }
}
export class ShellTimeoutError extends ShellExecutionError {
    constructor(receipt: ShellReceipt) { super(receipt); this.name = 'ShellTimeoutError'; this.code = 'SHELL_TIMEOUT'; this.message = 'SHELL_TIMEOUT'; }
}
export function parseReceipt(value: unknown): ShellReceipt { const result = Schema.resolve(value, Receipt, {})[0]; if (result.native.exitCode === undefined || result.native.exitSignal === undefined)
    throw Error('SHELL_RECEIPT_EXIT_MISSING'); return result; }
