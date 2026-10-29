import type { ArtifactRef } from '../artifacts-local/store.js';
import type { Context } from '@society0/cordis';
import type { ValidateFunction } from 'ajv';
import type { ActionDefinition, ActionExecutionContext, ActionReceipt } from '../contracts/domain.js';
import type { ActivationContext } from '../contracts/runtime.js';
import type { Json } from '../contracts/common.js';
import type { DiscoveryScope, StoredValue } from '../contracts/data.js';
import type { SqliteReadView, SqliteTransaction, TransactionRequest } from '../sqlite/contracts.js';
import { fail, bytes } from '../information/page.js';
import { prepareValue, attachValue } from '../results/payload.js';
import '../attempts/index.js';
import '../artifacts-local/index.js';
export interface ActionRegistration {
    definition: ActionDefinition;
    registrationId: string;
    input: ValidateFunction;
    output: ValidateFunction;
}
type Reader = SqliteReadView | SqliteTransaction;
function references(value: StoredValue) { return value.kind === 'reference' ? [value.ref.object] : []; }
export class ActionExecutionFailure extends Error {
    readonly code: string;
    readonly requiredOperations: readonly string[];
    readonly artifacts: readonly ArtifactRef[];
    constructor(cause: unknown, operations: Iterable<string>, code?: string) { super(String(cause), { cause }); this.name = 'ActionExecutionFailure'; this.code = code ?? (cause as {
        code?: string;
    } | null)?.code ?? 'ACTION_ERROR'; this.requiredOperations = Object.freeze([...operations]); const artifacts = (cause as {
        artifacts?: readonly ArtifactRef[];
    } | null)?.artifacts; this.artifacts = Array.isArray(artifacts) ? artifacts : []; }
}
/** 一次行动持有注册租约；快照筛查、外部执行与最终事务边界分别落实。 */
export class ActionCall {
    private attempt;
    private committed = new Set<string>();
    private scope: DiscoveryScope;
    constructor(private ctx: Context, private entry: ActionRegistration, private input: Json, private activation: ActivationContext, private allowed: (reader: Reader) => Promise<boolean>) { this.attempt = { lineage: ctx.attempts.authority().lineage, operationId: entry.definition.effect === 'external' ? 'action:' + activation.operationId : 'action-run:' + JSON.stringify([activation.runId, activation.operationId]), attempt: 0 }; this.scope = { actorId: activation.actorId, momentId: activation.momentId, logicalTime: activation.logicalTime, query: null }; }
    private async qualify(reader: Reader) {
        if (!await this.allowed(reader) || !await this.entry.definition.available(this.scope, reader))
            return fail('ACTION_INELIGIBLE');
    }
    private async initial() {
        if (!this.entry.input(this.input))
            return fail('ACTION_INPUT');
        const view = await this.activation.database.snapshot({ maxAgeMs: 30000 });
        try {
            await this.qualify(view);
            if (this.entry.definition.effect !== 'external')
                await this.assertUncommitted(view);
        }
        finally {
            await view.close();
        }
    }
    private context(storedInput: StoredValue): ActionExecutionContext { return { ...this.activation, actionRegistrationId: this.entry.registrationId, transaction: <T>(request: TransactionRequest, body: (tx: SqliteTransaction) => Promise<T>) => this.transaction(storedInput, request, body) }; }
    private async assertUncommitted(reader: Reader) { const receipts = await reader.query({ sql: 'SELECT operation_id FROM action_receipts WHERE invocation_id=?', parameters: [this.activation.operationId] }, { maxRows: 1000, maxBytes: 65536 }); if (receipts.some(row => !this.committed.has(String(row.operation_id))))
        return fail('ACTION_ALREADY_COMMITTED'); }
    private async transaction<T>(input: StoredValue, request: TransactionRequest, body: (tx: SqliteTransaction) => Promise<T>) { this.activation.signal.throwIfAborted(); if (this.entry.definition.effect === 'read')
        return fail('ACTION_READ_ONLY'); const result = await this.activation.database.transaction(this.activation.writeLease, request, async (tx) => { this.activation.signal.throwIfAborted(); await this.qualify(tx); await this.assertUncommitted(tx); const value = await body(tx); this.activation.signal.throwIfAborted(); await this.receipt(tx, input, request, value); return value; }); this.committed.add(result.receipt.operationId); return result; }
    private async receipt(tx: SqliteTransaction, input: StoredValue, request: TransactionRequest, result: unknown) {
        const value=result??null;
        const encoded=JSON.stringify(value,(_key,value)=>typeof value==='bigint'?value.toString():value);
        if (Buffer.byteLength(encoded) > request.maxInputBytes)
            return fail('ACTION_RECEIPT_BUDGET');
        await attachValue(tx, input, this.ctx.artifacts);
        await tx.execute({ sql: 'INSERT INTO action_receipts VALUES(?,?,?,?,?,?,?,?)', parameters: [request.operationId, this.activation.operationId, this.activation.actorId, this.activation.momentId, this.entry.definition.name, this.entry.registrationId, JSON.stringify(input), encoded] });
    }
    private async claim(input: StoredValue) { while (true) {
        this.activation.signal.throwIfAborted();
        const claim = await this.ctx.attempts.claim(this.attempt, { name: this.entry.definition.name, registrationId: this.entry.registrationId, input }, references(input));
        if (claim.status === 'send-permitted')
            return;
        if (claim.status === 'unknown')
            return fail('ACTION_ATTEMPT_UNKNOWN');
        if (this.entry.definition.effect === 'external')
            return fail('ACTION_ATTEMPT_EXISTS');
        this.attempt.attempt++;
    } }
    private async returned(receipt: ActionReceipt) { const raw = await prepareValue(receipt as unknown as Json, 16384, this.ctx.artifacts); await this.ctx.attempts.settle(this.attempt, { status: 'returned', receipt: raw }, [...references(raw), ...receipt.artifacts]); }
    private async failed(error: ActionExecutionFailure) { const existing = await this.ctx.attempts.probe(this.attempt); if (existing.status !== 'found' || existing.record.status !== 'pending')
        return false; const facts = { code: error.code, message: error.message, requiredOperations: error.requiredOperations }; if (this.entry.definition.effect === 'external') {
        const raw = await prepareValue(facts as unknown as Json, 0, this.ctx.artifacts);
        await this.ctx.attempts.markUnknown(this.attempt, error.message, [...references(raw), ...error.artifacts]);
        return true;
    } await this.ctx.attempts.settle(this.attempt, { status: 'failed', ...facts }, error.artifacts); return false; }
    private async rejected(error: unknown): Promise<never> { const failure = new ActionExecutionFailure(error, this.committed); let unknown: boolean; try {
        unknown = await this.failed(failure);
    }
    catch (evidenceError) {
        throw new ActionExecutionFailure(new AggregateError([error, evidenceError], 'Action failure evidence could not be persisted'), this.committed);
    } throw unknown ? new ActionExecutionFailure(error, this.committed, 'ACTION_ATTEMPT_UNKNOWN') : failure; }
    async run() {
        await this.initial();
        const input = await prepareValue(this.input, 16384, this.ctx.artifacts, this.activation.signal);
        await this.claim(input);
        try {
            const returned = await this.entry.definition.execute(this.input, this.context(input));
            const receipt: ActionReceipt = { ...returned, requiredOperations: [...this.committed] };
            await this.returned(receipt);
            if (!this.entry.output(receipt.value))
                return fail('ACTION_OUTPUT');
            this.activation.signal.throwIfAborted();
            return receipt;
        }
        catch (error) {
            return this.rejected(error);
        }
    }
}
