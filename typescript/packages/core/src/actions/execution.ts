import type { ArtifactRef } from '../artifacts-local/store.js';
import type { Context } from '@society0/cordis';
import type { ValidateFunction } from 'ajv';
import type { ActionDefinition, ActionExecutionContext, ActionReceipt } from '../contracts/domain.js';
import type { ActionRef, AvailabilitySummary, OfferIdentity, TargetRef } from '../contracts/affordance.js';
import type { ActivationContext } from '../contracts/runtime.js';
import type { Json } from '../contracts/common.js';
import type { DiscoveryScope, StoredValue } from '../contracts/data.js';
import type { SqliteReadView, SqliteTransaction, TransactionRequest } from '../sqlite/contracts.js';
import { targetText, canonicalJson, availabilityDetails, availabilityText } from '../contracts/affordance.js';
import { fail, bytes } from '../information/page.js';
import { prepareValue, attachValue, readValue } from '../results/payload.js';
import { isActionRejection, type ActionRejectionError } from './rejection.js';
import '../attempts/index.js';
import '../artifacts-local/index.js';

/**
 * 行动事务的框架语句独立计数：execution.ts 在插件 body 之前/之后执行 qualify（authority.state）、
 * assertUncommitted、attachValue 与 action_receipts 回执写入，这些语句记在框架计数里，不占插件在
 * TransactionRequest.maxStatements 声明的额度。实测标准 provider 形状（每个 provider 一条
 * versions 查询 + 一条 offers 查询）下框架语句 = 4 + 2 × provider 数：
 * 0 个 provider 为 4 条（当前 offer 资格、ambient 门槛、uncommitted 查询、回执 INSERT 各 1），
 * 6 个为 16 条，12 个为 28 条；D26 最小世界实测 14 条。下面两个常量与公式只用于守住实测形状，
 * 不参与额度计算。
 */
export const ACTION_FRAMEWORK_FIXED_STATEMENTS = 4;
export const ACTION_FRAMEWORK_STATEMENTS_PER_PROVIDER = 2;
export function actionFrameworkStatements(providers: number): number {
    return ACTION_FRAMEWORK_FIXED_STATEMENTS + ACTION_FRAMEWORK_STATEMENTS_PER_PROVIDER * providers;
}
/**
 * 框架计数的宽松上限，只防失控，不约束插件 body。实测最大 28 条（12 个 provider），
 * 4096 条相当于 2000 个 provider 的实测斜率，远高于实际世界（run 32 为 6 个 provider）。
 */
export const ACTION_FRAMEWORK_STATEMENTS_CEILING = 4096;

export interface ActionRegistration {
    definition: ActionDefinition;
    registrationId: string;
    input: ValidateFunction;
    output: ValidateFunction;
}
type Reader = SqliteReadView | SqliteTransaction;
export type OfferState = 'valid' | 'ineligible' | 'absent';
/** 当前可用候选与同行动替代项，供 ACTION_NOT_OFFERED 回执说明。 */
export interface ActionAvailability extends AvailabilitySummary {
    readonly alternatives: readonly ActionRef[];
}
/** 执行期对 offer 的复查、拒绝原因与替代项；由 Actions 绑定当前注册表、provider 与快照。 */
export interface ActionAuthority {
    readonly key: string;
    readonly target?: TargetRef;
    readonly offer?: OfferIdentity;
    readonly delivered?: ReadonlySet<string>;
    state(reader: Reader): Promise<OfferState>;
    findings(reader: Reader): Promise<readonly { ruleId: string; reason: string }[]>;
    available(reader: Reader): Promise<ActionAvailability>;
}
function references(value: StoredValue) { return value.kind === 'reference' ? [value.ref.object] : []; }
function failureCode(cause: unknown, code?: string): string {
    if (code) return code;
    const value = (cause as { code?: string } | null)?.code;
    return value ?? 'ACTION_ERROR';
}
function failureArtifacts(cause: unknown): readonly ArtifactRef[] {
    const artifacts = (cause as { artifacts?: readonly ArtifactRef[] } | null)?.artifacts;
    return Array.isArray(artifacts) ? artifacts : [];
}
const typeLabels: Readonly<Record<string, string>> = { integer: '整数', string: '字符串', number: '数字', boolean: '布尔', array: '数组', object: '对象' };
function limitBytes(text: string, max: number): string {
    const buffer = Buffer.from(text);
    return buffer.length <= max ? text : buffer.subarray(0, max).toString('utf8').replace(/\uFFFD+$/u, '');
}
/** 该行动 input schema 的必填字段与类型摘要，用于 ACTION_INPUT 的 expected（≤1KB）。 */
function expectedSummary(schema: Json): string {
    if (schema === null || typeof schema !== 'object' || Array.isArray(schema))
        return '无 schema';
    const object = schema as Readonly<Record<string, Json>>;
    const properties = object.properties;
    const required = Array.isArray(object.required) ? object.required.filter((value): value is string => typeof value === 'string') : [];
    if (!required.length)
        return '无必填字段';
    const fields = required.map((name) => {
        const property = properties !== null && typeof properties === 'object' && !Array.isArray(properties) ? (properties as Readonly<Record<string, Json>>)[name] : undefined;
        if (property === null || typeof property !== 'object' || Array.isArray(property))
            return `${name}: 任意`;
        const type = (property as Readonly<Record<string, Json>>).type;
        return `${name}: ${typeof type === 'string' ? typeLabels[type] ?? type : '任意'}`;
    });
    return limitBytes(fields.join('; '), 1024);
}
/** 输入校验失败是业务拒绝：回执带字段路径、错误计数和 input schema 摘要，不 claim attempt。 */
export function validateInput(entry: ActionRegistration, input: Json, operationId: string): ActionReceipt | undefined {
    if (entry.input(input))
        return undefined;
    const all = entry.input.errors ?? [];
    const errors = all.slice(0, 5).map((error) => ({ path: typeof error.instancePath === 'string' ? error.instancePath : '', message: String(error.message ?? '') }));
    return { operationId, status: 'rejected', value: { code: 'ACTION_INPUT', errors, errorCount: all.length, expected: expectedSummary(entry.definition.inputSchema) }, original: null, artifacts: [], requiredOperations: [] };
}
export class ActionExecutionFailure extends Error {
    readonly code: string;
    readonly requiredOperations: readonly string[];
    readonly artifacts: readonly ArtifactRef[];
    readonly storage: boolean;
    constructor(cause: unknown, operations: Iterable<string>, code?: string) { super(String(cause), { cause }); this.name = 'ActionExecutionFailure'; this.code = failureCode(cause, code); this.requiredOperations = Object.freeze([...operations]); this.artifacts = failureArtifacts(cause); this.storage = (cause as { storage?: boolean } | null)?.storage === true; }
}
/** 一次行动持有注册租约；快照筛查、外部执行与最终事务边界分别落实。 */
export class ActionCall {
    private attempt;
    private committed = new Set<string>();
    private scope: DiscoveryScope;
    /** 最近一次提交的 revision；没有写事务时退化为逻辑时间，供 delivery.visibleRevision 使用。 */
    private revision: string;
    constructor(private ctx: Context, private entry: ActionRegistration, private input: Json, private activation: ActivationContext, private authority: ActionAuthority) { this.attempt = { lineage: ctx.attempts.authority().lineage, operationId: entry.definition.effect === 'external' ? 'action:' + activation.operationId : 'action-run:' + JSON.stringify([activation.runId, activation.operationId]), attempt: 0 }; this.scope = { actorId: activation.actorId, momentId: activation.momentId, logicalTime: activation.logicalTime, query: null }; this.revision = String(activation.logicalTime); }
    get deliveredRevision() { return this.revision; }
    private rejectedReceipt(code: string): ActionReceipt {
        return {
            operationId: this.activation.operationId,
            status: 'rejected',
            value: {
                code,
                action: this.entry.definition.name,
                ...(this.authority.target ? { target: targetText(this.authority.target) } : {}),
            },
            original: null,
            artifacts: [],
            requiredOperations: [],
        };
    }
    /** execute 显式声明的领域拒绝：保留 code/message/details，形状与 rejectedReceipt 一致。 */
    private actionRejectionReceipt(error: ActionRejectionError): ActionReceipt {
        return {
            operationId: this.activation.operationId,
            status: 'rejected',
            value: {
                code: error.code,
                message: error.message,
                ...(error.details === undefined ? {} : { details: error.details }),
                action: this.entry.definition.name,
                ...(this.authority.target ? { target: targetText(this.authority.target) } : {}),
            },
            original: null,
            artifacts: [],
            requiredOperations: [...this.committed],
        };
    }
    private async rejection(reader: Reader): Promise<ActionReceipt> {
        const state = await this.authority.state(reader);
        if (state === 'valid')
            return fail('ACTION_INELIGIBLE');
        if (state === 'ineligible') {
            const receipt = this.rejectedReceipt('ACTION_INELIGIBLE');
            return { ...receipt, value: { ...(receipt.value as Readonly<Record<string, Json>>), findings: [...await this.authority.findings(reader)] as unknown as Json } };
        }
        const code = this.authority.delivered?.has(this.authority.key) ? 'ACTION_EXPIRED' : 'ACTION_NOT_OFFERED';
        const receipt = this.rejectedReceipt(code);
        const available = await this.authority.available(reader);
        const alternatives = [...available.alternatives] as unknown as Json;
        if (code === 'ACTION_EXPIRED')
            return { ...receipt, value: { ...(receipt.value as Readonly<Record<string, Json>>), alternatives } };
        const target = this.authority.target ? ` ${targetText(this.authority.target)}` : '';
        const value = {
            ...(receipt.value as Readonly<Record<string, Json>>),
            message: `该行动当前不在可选行动里（${this.entry.definition.name}${target}）。${availabilityText(available)}`,
            details: availabilityDetails(available) as Json,
            alternatives,
        };
        return { ...receipt, value };
    }
    private async qualify(reader: Reader) {
        if (await this.authority.state(reader) !== 'valid')
            return fail('ACTION_INELIGIBLE');
    }
    private recoveryNamespaces(): readonly string[] {
        const recovery = this.activation.recovery;
        if (!recovery)
            return [];
        const names = [recovery.baseOperationId];
        for (let attempt = 1; attempt < recovery.attempt; attempt++)
            names.push(recovery.baseOperationId + '/r' + attempt);
        return names;
    }
    private async originalReceipt(reader: Reader, row: Record<string, unknown>): Promise<ActionReceipt> {
        const rows = await reader.query({
            sql: 'SELECT payload FROM actor_tool_receipts WHERE actor_id=? AND moment_id=? AND action=?',
            parameters: [this.activation.actorId, this.activation.momentId, this.entry.definition.name]
        }, { maxRows: 64, maxBytes: 1048576 });
        for (const entry of rows) {
            try {
                const stored = JSON.parse(String(entry.payload)) as { kind?: string; value?: ActionReceipt };
                if (stored?.kind === 'inline' && stored.value?.operationId === String(row.operation_id))
                    return stored.value;
            }
            catch {
                continue;
            }
        }
        const storedInput = JSON.parse(String(row.input)) as { kind?: string; value?: Json };
        return {
            operationId: String(row.operation_id),
            status: 'completed',
            value: JSON.parse(String(row.value)),
            original: storedInput?.kind === 'inline' ? storedInput.value ?? null : null,
            artifacts: [],
            requiredOperations: [String(row.operation_id)]
        };
    }
    /** 读出原回执记录里的输入，供本次输入比较；引用体按声明长度整读。 */
    private async storedInput(row: Record<string, unknown>): Promise<Json | undefined> {
        try {
            const stored = JSON.parse(String(row.input)) as StoredValue;
            if (stored.kind === 'inline')
                return stored.value;
            const size = Number(stored.ref.rawBytes);
            if (!Number.isSafeInteger(size) || size < 0)
                return undefined;
            const chunks = [];
            for await (const chunk of readValue(stored.ref, { offset: '0', length: size, maxBytes: size }, this.ctx.artifacts, this.activation.signal))
                chunks.push(chunk);
            return JSON.parse(Buffer.concat(chunks).toString('utf8'));
        }
        catch {
            return undefined;
        }
    }
    /** 自动恢复重跑：同一 actor、同一 moment、同一 action 已提交过回执时返回原回执，不再执行第二次。 */
    private async replay(reader: Reader): Promise<ActionReceipt | undefined> {
        const namespaces = this.recoveryNamespaces();
        if (!namespaces.length)
            return undefined;
        const rows = await reader.query({
            sql: 'SELECT operation_id,invocation_id,input,value FROM action_receipts WHERE actor_id=? AND moment_id=? AND action=? AND coalesce(target,\'\')=? ORDER BY rowid DESC',
            parameters: [this.activation.actorId, this.activation.momentId, this.entry.definition.name, this.authority.target ? targetText(this.authority.target) : '']
        }, { maxRows: 1000, maxBytes: 1048576 });
        const row = rows.find(item => namespaces.some(namespace => String(item.invocation_id).startsWith(namespace + '/tool/')));
        if (!row)
            return undefined;
        const receipt = await this.originalReceipt(reader, row as Record<string, unknown>);
        const previous = await this.storedInput(row as Record<string, unknown>);
        const mismatch = previous !== undefined && canonicalJson(previous) !== canonicalJson(this.input);
        return { ...receipt, replayed: true, ...(mismatch ? { inputMismatch: true } : {}) };
    }
    private async initial(): Promise<ActionReceipt | undefined> {
        const view = await this.activation.database.snapshot({ maxAgeMs: 30000 });
        try {
            const replayed = await this.replay(view);
            if (replayed)
                return replayed;
            if (await this.authority.state(view) !== 'valid')
                return await this.rejection(view);
            if (this.entry.definition.effect !== 'external')
                await this.assertUncommitted(view);
            return undefined;
        }
        finally {
            await view.close();
        }
    }
    private context(storedInput: StoredValue): ActionExecutionContext { return { ...this.activation, actionRegistrationId: this.entry.registrationId, ...(this.authority.target ? { target: this.authority.target } : {}), ...(this.authority.offer ? { offer: this.authority.offer } : {}), transaction: <T>(request: TransactionRequest, body: (tx: SqliteTransaction) => Promise<T>) => this.transaction(storedInput, request, body) }; }
    private async assertUncommitted(reader: Reader) { const receipts = await reader.query({ sql: 'SELECT operation_id FROM action_receipts WHERE invocation_id=?', parameters: [this.activation.operationId] }, { maxRows: 1000, maxBytes: 65536 }); if (receipts.some(row => !this.committed.has(String(row.operation_id))))
        return fail('ACTION_ALREADY_COMMITTED'); }
    private async transaction<T>(input: StoredValue, request: TransactionRequest, body: (tx: SqliteTransaction) => Promise<T>) { this.activation.signal.throwIfAborted(); if (this.entry.definition.effect === 'read')
        return fail('ACTION_READ_ONLY'); const framed: TransactionRequest = { ...request, maxFrameworkStatements: ACTION_FRAMEWORK_STATEMENTS_CEILING }; const result = await this.activation.database.transaction(this.activation.writeLease, framed, async (tx) => { this.activation.signal.throwIfAborted(); const framework: SqliteTransaction = { ...tx, query: (statement, bounds) => tx.query(statement, bounds, { framework: true }), execute: (statement) => tx.execute(statement, { framework: true }), executeMany: (sql, parameters, bounds) => tx.executeMany(sql, parameters, bounds, { framework: true }) }; await this.qualify(framework); await this.assertUncommitted(framework); const value = await body(tx); this.activation.signal.throwIfAborted(); await this.receipt(framework, input, request, value); return value; }); this.committed.add(result.receipt.operationId); this.revision = result.receipt.revision; return result; }
    private async receipt(tx: SqliteTransaction, input: StoredValue, request: TransactionRequest, result: unknown) {
        const value=result??null;
        const encoded=JSON.stringify(value,(_key,value)=>typeof value==='bigint'?value.toString():value);
        if (Buffer.byteLength(encoded) > request.maxInputBytes)
            return fail('ACTION_RECEIPT_BUDGET');
        await attachValue(tx, input, this.ctx.artifacts);
        await tx.execute({
            sql: 'INSERT INTO action_receipts(operation_id,invocation_id,actor_id,moment_id,action,registration_id,input,value,target,offer_key,provider) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            parameters: [request.operationId, this.activation.operationId, this.activation.actorId, this.activation.momentId, this.entry.definition.name, this.entry.registrationId, JSON.stringify(input), encoded, this.authority.target ? targetText(this.authority.target) : null, this.authority.offer?.key ?? null, this.authority.offer?.provider ?? null],
        });
    }
    private async claim(input: StoredValue) { while (true) {
        this.activation.signal.throwIfAborted();
        const claim = await this.ctx.attempts.claim(this.attempt, { name: this.entry.definition.name, registrationId: this.entry.registrationId, input }, references(input));
        if (claim.status === 'send-permitted')
            return;
        if (claim.status === 'unknown')
            throw Object.assign(new Error('ACTION_ATTEMPT_UNKNOWN'), { code: 'ACTION_ATTEMPT_UNKNOWN', storage: true, reason: claim.reason });
        if (claim.record.status === 'unknown')
            return this.unknownAttempt(claim.record, this.entry.definition.effect === 'external');
        if (this.entry.definition.effect === 'external')
            return fail('ACTION_ATTEMPT_EXISTS');
        this.attempt.attempt++;
    } }
    private unknownAttempt(record: { result?: unknown }, external: boolean): never {
        if (external) return fail('ACTION_ATTEMPT_UNKNOWN');
        const stored = record.result as { code?: string; commit?: unknown } | undefined;
        const code = stored?.code === 'SQLITE_COMMIT_UNKNOWN' || stored?.code === 'SQLITE_SEGMENT_FAILED' ? stored.code : 'ACTION_ATTEMPT_UNKNOWN';
        throw Object.assign(new Error(code), { code, storage: true, ...(stored?.commit ? { commit: stored.commit } : {}) });
    }
    private async returned(receipt: ActionReceipt) { const raw = await prepareValue(receipt as unknown as Json, 16384, this.ctx.artifacts); await this.ctx.attempts.settle(this.attempt, { status: 'returned', receipt: raw }, [...references(raw), ...receipt.artifacts]); }
    private async failed(error: ActionExecutionFailure): Promise<'external-unknown' | 'storage-unknown' | 'settled' | 'absent'> { const existing = await this.ctx.attempts.probe(this.attempt); if (existing.status !== 'found' || existing.record.status !== 'pending')
        return 'absent'; const facts = { code: error.code, message: error.message, requiredOperations: error.requiredOperations }; if (this.entry.definition.effect === 'external') {
        const raw = await prepareValue(facts as unknown as Json, 0, this.ctx.artifacts);
        await this.ctx.attempts.markUnknown(this.attempt, error.message.toWellFormed(), [...references(raw), ...error.artifacts]);
        return 'external-unknown';
    } if (error.code === 'SQLITE_COMMIT_UNKNOWN' || error.code === 'SQLITE_SEGMENT_FAILED') {
        const cause = error.cause as { committed?: boolean; receipt?: unknown } | undefined;
        const result = { ...facts, ...(error.code === 'SQLITE_SEGMENT_FAILED' ? { commit: { committed: true, receipt: cause?.receipt } } : {}) };
        await this.ctx.attempts.markUnknown(this.attempt, error.message.toWellFormed(), error.artifacts, result);
        return 'storage-unknown';
    } await this.ctx.attempts.settle(this.attempt, { status: 'failed', ...facts }, error.artifacts); return 'settled'; }
    private async rejected(error: unknown): Promise<never> { const failure = new ActionExecutionFailure(error, this.committed); let kind: 'external-unknown' | 'storage-unknown' | 'settled' | 'absent'; try {
        kind = await this.failed(failure);
    }
    catch (evidenceError) {
        throw new ActionExecutionFailure(new AggregateError([error, evidenceError], 'Action failure evidence could not be persisted'), this.committed);
    } if (kind === 'external-unknown')
        throw new ActionExecutionFailure(error, this.committed, 'ACTION_ATTEMPT_UNKNOWN'); throw failure; }
    async run() {
        const invalid = validateInput(this.entry, this.input, this.activation.operationId);
        if (invalid)
            return invalid;
        const early = await this.initial();
        if (early)
            return early;
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
            if (isActionRejection(error)) {
                const receipt = this.actionRejectionReceipt(error);
                try {
                    await this.returned(receipt);
                    return receipt;
                }
                catch (persistence) {
                    return this.rejected(persistence);
                }
            }
            return this.rejected(error);
        }
    }
}
