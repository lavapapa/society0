import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { AdvanceResult, GenerationView, ExecutionContext, CompletePoint, Proposal, Json, BoundaryLease } from '../contracts/runtime.js';
import type { DeferredWork } from '../contracts/deferred-work.js';
import { metricsOf } from '../contracts/metrics.js';
import type { RuntimeFrontier } from '../sqlite/contracts.js';
import '../generation-views/index.js';
import '../sqlite-history/index.js';
export const Config = Schema.object({
    maxTransactionMs: Schema.natural().min(1).default(15000), maxPendingAcknowledgements: Schema.natural().min(1).default(1024), pauseTimeoutMs: Schema.natural().min(1).default(30000),
    maxTicks: Schema.natural().min(1),
    /** 失败激活在进程内重跑的次数；用完后降级为该主体本步无行动，本步照常完成。 */
    autoRecover: Schema.object({
        maxAttempts: Schema.natural().min(0).default(3),
        backoffMs: Schema.natural().min(0).default(1000)
    }).default({})
});
export const metadata: PluginMetadata = {
    title: 'run-controller', summary: '固定代视图、真实排空与SQL完整点推进', configVersion: 1, configPolicy: [{
            path: '/maxTransactionMs', mode: 'new-run', affectsResults: false
        }, {
            path: '/maxPendingAcknowledgements', mode: 'new-run', affectsResults: true
        }, {
            path: '/pauseTimeoutMs', mode: 'boundary', affectsResults: false
        }, {
            path: '/maxTicks', mode: 'new-run', affectsResults: true
        }, {
            path: '/autoRecover', mode: 'boundary', affectsResults: true
        }], scope: 'run', hostAbi: 'society0/host/1'
};
export const api: ApiEntry = {
    exportPath: './run-controller', provides: [{
            key: 'runs', protocol: 'runs', major: 1, capabilities: []
        }], requires: ['database', 'history', 'executionFence', 'generationViews'].map(key => ({
        service: {
            key, protocol: key, major: 1, capabilities: []
        }, optional: false, phase: 'module'
    }))
};
import type { RunResult, ProposalResult, RunActivationSummary, RunActivationSummaries } from '../contracts/runtime.js';
export type { RunResult } from '../contracts/runtime.js';
type ViewLease = Awaited<ReturnType<Context['generationViews']['acquire']>>;
type ExecutionReservation = Awaited<ReturnType<Context['executionFence']['reserve']>>;
type SealLease = Awaited<ReturnType<Context['executionFence']['seal']>>;
interface RunSession {
    cut: CompletePoint;
    frontier: RuntimeFrontier;
    required: Set<string>;
    held?: ViewLease;
    proposal?: Proposal;
}
declare module '@society0/host' {
    interface Context {
        runs: RunController;
    }
}
export class RunController extends Service {
    static Config = Config;
    static inject = ['database', 'history', 'executionFence', 'generationViews'];
    private config: ReturnType<typeof Config>;
    private running?: Promise<RunResult>;
    private wait?: AbortController;
    private activeWork?: Promise<unknown>;
    private view?: GenerationView;
    private closing = false;
    private stop = false;
    private lastFailure?: string;
    private lastAdvance?: AdvanceResult;
    private latestCut?: CompletePoint;
    private complete = true;
    private pending: Proposal[] = [];
    private activationEntries: RunActivationSummary[] = [];
    private activationIndex = new Map<string, number>();
    private activationTruncated = false;
    private recovery?: { proposalId: string; attempts: number; completed: Set<string>; operations: Set<string> };
    constructor(ctx: Context, input = {}) {
        super(ctx, 'runs');
        this.config = Config(input);
        ctx.history.bindQuiescence(async () => {
            await this.activeWork;
        });
        ctx.effect(() => () => this.close());
    }
    run(signal: AbortSignal): Promise<RunResult> {
        if (this.running)
            throw Error('RUN_ALREADY_ACTIVE');
        if (this.closing)
            throw Error('RUN_CLOSED');
        if (this.lastFailure)
            throw Error('RUN_FAILED ' + this.lastFailure);
        this.stop = false;
        const task = this.loop(signal).then(result => this.withActivations(result));
        this.running = task;
        void task.finally(() => {
            this.running = undefined;
        }).catch(() => {
        });
        return task;
    }
    private tickText(value: Json) {
        return typeof value === 'string' ? value : JSON.stringify(value);
    }
    private withActivations(result: RunResult): RunResult {
        return { ...result, activations: this.activationSummaries() };
    }
    private activationSummaries(): RunActivationSummaries {
        return { entries: [...this.activationEntries], truncated: this.activationTruncated };
    }
    /** 从 runtime 的激活证据提取摘要；同一 activationId 保留最后一次结果，超过 run 级上限就截断。 */
    private recordActivations(result: AdvanceResult, logicalTime: Json) {
        const evidence = result.evidence as { activations?: unknown } | undefined;
        const list = evidence?.activations;
        if (!Array.isArray(list))
            return;
        for (const raw of list) {
            if (!raw || typeof raw !== 'object' || Array.isArray(raw))
                continue;
            const entry = raw as { activationId?: unknown; actorId?: unknown; status?: unknown; value?: unknown; evidence?: unknown };
            const key = typeof entry.activationId === 'string' ? entry.activationId : undefined;
            const index = key ? this.activationIndex.get(key) : undefined;
            if (index !== undefined) {
                this.activationEntries[index] = this.activationSummary(entry, logicalTime);
                continue;
            }
            if (this.activationEntries.length >= this.config.maxPendingAcknowledgements) {
                this.activationTruncated = true;
                continue;
            }
            this.activationEntries.push(this.activationSummary(entry, logicalTime));
            if (key !== undefined)
                this.activationIndex.set(key, this.activationEntries.length - 1);
        }
    }
    private activationSummary(entry: { activationId?: unknown; actorId?: unknown; momentId?: unknown; status?: unknown; value?: unknown; evidence?: unknown }, logicalTime: Json) {
        const value = entry.value && typeof entry.value === 'object' && !Array.isArray(entry.value) ? entry.value as Record<string, unknown> : {};
        const activationEvidence = entry.evidence && typeof entry.evidence === 'object' && !Array.isArray(entry.evidence) ? entry.evidence as Record<string, unknown> : {};
        const reason = typeof value.reason === 'string' ? value.reason : typeof activationEvidence.reason === 'string' ? activationEvidence.reason : String(entry.status ?? 'incomplete');
        const count = (name: string) => typeof value[name] === 'number' ? value[name] as number : typeof activationEvidence[name] === 'number' ? activationEvidence[name] as number : 0;
        const warnings = Array.isArray(value.warnings) ? value.warnings.filter(item => typeof item === 'string') : [];
        const limit = typeof value.limit === 'string' ? value.limit : typeof activationEvidence.limit === 'string' ? activationEvidence.limit : undefined;
        const completed = entry.status === 'completed' || (value.requiredSatisfied === true && (reason === 'success' || reason === 'completed'));
        const errorText = typeof activationEvidence.error === 'string' ? activationEvidence.error : '';
        const code = /[A-Z][A-Z0-9_]+/.exec(errorText)?.[0] ?? (typeof activationEvidence.code === 'string' ? activationEvidence.code : undefined);
        const stage = typeof activationEvidence.stage === 'string' ? activationEvidence.stage : undefined;
        const slot = /SLOT_(?:REQUIRED|PENDING|PROVIDER_MISSING)\s+(\S+)/.exec(errorText)?.[1];
        return {
            actorId: typeof entry.actorId === 'string' ? entry.actorId : '',
            tick: this.tickText(logicalTime),
            reason,
            requests: count('requests'),
            toolCalls: count('toolCalls'),
            explorationErrors: count('explorationErrors'),
            actRecoverable: count('actRecoverable'),
            rejected: count('actRejected'),
            actFailed: count('actFailed'),
            requiredSatisfied: typeof value.requiredSatisfied === 'boolean' ? value.requiredSatisfied : entry.status === 'completed',
            ...(limit ? { limit: limit as 'requests' | 'toolCalls' | 'turns' } : {}),
            ...(!completed && typeof entry.momentId === 'string' ? { momentId: entry.momentId } : {}),
            ...(!completed && code ? { code } : {}),
            ...(!completed && stage ? { stage } : {}),
            ...(!completed && slot ? { slot } : {}),
            ...(warnings.length ? { warnings } : {}),
            ...(entry.status === 'failed' ? { degraded: true as const } : {})
        };
    }
    private async frontier() {
        const snapshot = await this.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        try {
            return await this.ctx.database.metadata.read(snapshot);
        }
        finally {
            await snapshot.close();
        }
    }
    private async busy<T>(action: (signal?: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
        const deadline = performance.now() + this.config.maxTransactionMs;
        while (true) {
            signal?.throwIfAborted();
            try { return await action(signal); }
            catch (error) {
                const message = error instanceof Error ? error.message : String(error);
                if (!/FENCE_BUSY/.test(message))
                    throw error;
                if (performance.now() >= deadline)
                    throw new Error(`FENCE_BUSY: retry timeout after ${this.config.maxTransactionMs}ms: ${message}`);
                await new Promise(resolve => setTimeout(resolve, 10));
            }
        }
    }
    private async publish(cut: CompletePoint, requiredOperations: readonly string[], view: GenerationView, signal?: AbortSignal) {
        const revision = await this.ctx.database.readRevision();
        if (revision === cut.revision)
            return cut;
        if (!this.complete)
            throw Error('RUN_INCOMPLETE');
        const lease = await this.busy(signal => this.ctx.executionFence.seal(signal), signal);
        const sealedRevision = lease.sealedRevision;
        if (sealedRevision === cut.revision) {
            this.ctx.executionFence.resume(lease);
            return cut;
        }
        const operationId = 'cut-' + randomUUID();
        const next: CompletePoint = await this.ctx.history.publish(lease, {
            operationId, id: operationId, parentId: cut.id, expectedRevision: sealedRevision, requiredOperations
        });
        this.latestCut = next;
        this.ctx.executionFence.resume(lease);
        for (const proposal of this.pending)
            await view.source.acknowledge(proposal, next);
        this.pending = [];
        return next;
    }
    private async loop(signal: AbortSignal): Promise<RunResult> {
        let cut: CompletePoint | undefined, session: RunSession | undefined;
        try {
            cut = await this.ctx.history.current();
            session = {
                cut, frontier: await this.frontier(), required: new Set()
            };
            if (await this.ctx.database.readRevision() !== cut.revision)
                throw Error('RUN_DIRTY_REQUIRES_RECOVERY');
            const view = await this.acquireView(session);
            await this.recoverAcknowledgements(session, view);
            return await this.drive(session, signal);
        }
        catch (error) {
            return await this.failRun(error, session, cut);
        }
        finally {
            await this.releaseSession(session);
        }
    }
    private async acquireView(session: RunSession) {
        if (!session.held) {
            const current = this.ctx.generationViews.current();
            session.held = await this.ctx.generationViews.acquire(current.generation);
        }
        const view = this.view = session.held.value;
        if (view.generation !== session.cut.generation)
            throw Error('RUN_GENERATION');
        return view;
    }
    private async recoverAcknowledgements(session: RunSession, view: GenerationView) {
        for (const pending of session.cut.frontier.pendingAcknowledgements) {
            await view.source.acknowledge({
                id: pending.proposalId, cursor: session.frontier.value as Json, planKind: 'events/1', payload: null, reservation: {
                    source: pending.source, id: pending.reservationId, operationId: pending.proposalId
                }
            }, session.cut);
        }
    }
    private interrupted(signal: AbortSignal) {
        return this.stop || signal.aborted;
    }
    private async pauseSession(session: RunSession, view: GenerationView, signal: AbortSignal): Promise<RunResult> {
        session.cut = await this.publish(session.cut, [...session.required], view, signal);
        return {
            status: 'paused', cut: session.cut
        };
    }
    private async waitSource(session: RunSession, view: GenerationView, signal: AbortSignal) {
        this.wait = new AbortController();
        try {
            return await view.source.waitProposal(session.frontier.value as Json, AbortSignal.any([signal, this.wait.signal]));
        }
        catch (error) {
            if (this.interrupted(signal))
                return undefined;
            throw error;
        }
        finally {
            this.wait = undefined;
        }
    }
    private async drive(session: RunSession, signal: AbortSignal): Promise<RunResult> {
        while (true) {
            const view = await this.acquireView(session);
            if (this.interrupted(signal))
                return this.pauseSession(session, view, signal);
            const capped = await this.maxTicksBoundary(session, view, signal);
            if (capped)
                return capped;
            const source = await this.waitSource(session, view, signal);
            if (!source)
                return this.pauseSession(session, view, signal);
            if (source.kind !== 'proposal') {
                const result = await this.sourceBoundary(session, view, source, signal);
                if (result)
                    return result;
                continue;
            }
            session.proposal = source.proposal;
            const proposal = source.proposal;
            const advance = await this.executeProposal(session, view, signal);
            if (advance.kind === 'failed') {
                this.recovery = undefined;
                return advance.result;
            }
            if (advance.kind === 'paused') {
                if (await this.recover(session, proposal, advance)) continue;
                this.recovery = undefined;
                return advance.result;
            }
            this.recovery = undefined;
            const boundary = await this.advanceBoundary(session, view, advance.result, signal);
            if (boundary)
                return boundary;
            await session.held!.release();
            session.held = undefined;
        }
    }
    private async maxTicksBoundary(session: RunSession, view: GenerationView, signal: AbortSignal): Promise<RunResult | undefined> {
        const maxTicks = this.config.maxTicks;
        if (maxTicks === undefined || BigInt(session.frontier.completedCount) < BigInt(maxTicks))
            return;
        session.cut = await this.publish(session.cut, [...session.required], view, signal);
        return { status: 'completed', cut: session.cut, reason: `max ticks reached (${maxTicks})` };
    }
    private async sourceBoundary(session: RunSession, view: GenerationView, source: Exclude<ProposalResult, {
        kind: 'proposal';
    }>, signal: AbortSignal): Promise<RunResult | undefined> {
        const liveRevision = await this.ctx.database.readRevision();
        const decision = view.termination.evaluate(source.kind === 'end' ? {
            kind: 'source-end', reason: source.reason, currentCut: session.cut, liveRevision, logicalTime: session.frontier.logicalTime as Json
        } : {
            kind: 'source-idle', resume: source.resume, currentCut: session.cut, liveRevision
        });
        if (decision.action === 'continue')
            return;
        if (decision.action === 'fail')
            throw Error(decision.reason);
        session.cut = await this.publish(session.cut, [...session.required], view, signal);
        return {
            status: decision.action === 'stop' ? 'completed' : 'paused', cut: session.cut, reason: decision.reason
        };
    }
    private completedActivations(result: AdvanceResult): string[] {
        const activations = (result.evidence as { activations?: readonly { activationId?: unknown; status?: unknown }[] })?.activations;
        if (!Array.isArray(activations)) return [];
        return activations.filter(item => item?.status === 'completed' && typeof item.activationId === 'string').map(item => String(item.activationId));
    }
    private completedOperations(result: AdvanceResult): string[] {
        const activations = (result.evidence as { activations?: readonly { status?: unknown; requiredOperations?: readonly unknown[] }[] })?.activations;
        if (!Array.isArray(activations)) return [];
        return activations.filter(item => item?.status === 'completed').flatMap(item => (item.requiredOperations ?? []).map(String));
    }
    /** 恢复时要保留上一轮已提交的全部写入，包括失败激活在线程里已经落盘的事务。 */
    private async settledOperations(result: AdvanceResult): Promise<string[]> {
        const settled: string[] = [];
        for (const operation of new Set((result.requiredOperations ?? []).map(String))) {
            const receipt = await this.ctx.database.probe(operation);
            if (receipt.status === 'committed') settled.push(operation);
        }
        return settled;
    }
    /**
     * 本步只因激活失败而未结算时返回这些失败激活；准入被拒或仍在等待的步不算。
     * 只认声明 rerunnable 的 runtime：events/1 重跑会重复提交事件事务，且失败事件之后的事件尚未执行，不能降级跳过。
     */
    private failedActivations(result: AdvanceResult, runtime: GenerationView['runtime']): Record<string, unknown>[] {
        if (result.status !== 'incomplete' || !runtime.rerunnable) return [];
        const evidence = result.evidence as { admissions?: readonly { kind?: unknown; reason?: unknown }[]; activations?: readonly Record<string, unknown>[] };
        if (evidence?.admissions?.some(item => item?.kind === 'rejected' && item.reason !== 'ACTIVATION_DUPLICATE') || !Array.isArray(evidence?.activations)) return [];
        if (evidence.activations.some(item => item?.status === 'waiting')) return [];
        return evidence.activations.filter(item => item?.status === 'incomplete');
    }
    private failureReason(item: Record<string, unknown>): string {
        const value = item.value as { reason?: unknown } | undefined, evidence = item.evidence as { reason?: unknown; kind?: unknown } | undefined;
        return String(value?.reason ?? evidence?.reason ?? evidence?.kind ?? 'incomplete');
    }
    private report(code: string, detail: Record<string, unknown>) {
        try {
            const diagnostics = this.ctx.get('diagnostics', false) as { report(record: { code: string; message: string; phase: 'task' }): void } | undefined;
            diagnostics?.report({ code, message: JSON.stringify(detail), phase: 'task' });
        } catch {
        }
    }
    /** 重试用完：失败激活结算为 failed（该主体本步无行动），本步以已提交的写入照常结算。 */
    private async degrade(result: AdvanceResult, failed: Record<string, unknown>[], logicalTime: Json): Promise<AdvanceResult> {
        const attempts = this.config.autoRecover.maxAttempts, metrics = metricsOf(this.ctx);
        const failedSet = new Set(failed);
        for (const item of failed) {
            this.report('RUN_ACTIVATION_DEGRADED', { actorId: item.actorId, activationId: item.activationId, reason: this.failureReason(item), attempts, tick: this.tickText(logicalTime) });
            metrics?.count('activations.degraded');
        }
        const evidence = result.evidence as { activations: readonly Record<string, unknown>[] };
        return {
            ...result, status: 'settled', requiredOperations: await this.settledOperations(result),
            evidence: { ...evidence, activations: evidence.activations.map(item => failedSet.has(item) ? { ...item, status: 'failed', degraded: { reason: this.failureReason(item), attempts } } : item) } as unknown as Json
        };
    }
    private async recover(session: RunSession, proposal: Proposal, advance: { advance: AdvanceResult; completed: string[]; failed?: Record<string, unknown>[] }): Promise<boolean> {
        const failed = advance.failed ?? [];
        if (!failed.length) return false;
        if (this.recovery?.proposalId !== proposal.id) this.recovery = { proposalId: proposal.id, attempts: 0, completed: new Set(), operations: new Set() };
        for (const id of advance.completed) this.recovery.completed.add(id);
        for (const operation of this.completedOperations(advance.advance)) this.recovery.operations.add(operation);
        for (const operation of await this.settledOperations(advance.advance)) this.recovery.operations.add(operation);
        this.recovery.attempts++;
        const attempt = this.recovery.attempts, delay = this.config.autoRecover.backoffMs * attempt;
        this.report('RUN_AUTO_RECOVER', { attempt, frontier: session.frontier.value, actors: failed.map(item => ({ actorId: item.actorId, reason: this.failureReason(item) })) });
        if (delay) await new Promise(resolve => setTimeout(resolve, delay));
        return true;
    }
    private async executeProposal(session: RunSession, view: GenerationView, signal: AbortSignal): Promise<{
        kind: 'paused';
        result: RunResult;
        advance: AdvanceResult;
        completed: string[];
        failed?: Record<string, unknown>[];
    } | {
        kind: 'advanced';
        result: AdvanceResult;
    } | {
        kind: 'failed';
        result: RunResult;
    }> {
        const proposal = session.proposal!;
        // 同一提案的重试不清队列：已完成的激活按 id 跳过、不再重跑，它们入队的延后任务要留到成点时一起提交。
        if (this.recovery?.proposalId !== proposal.id) this.deferred()?.begin();
        if (!view.runtime.planKinds.includes(proposal.planKind))
            throw Error('RUN_PLAN_KIND');
        if (proposal.reservation && this.pending.length >= this.config.maxPendingAcknowledgements)
            session.cut = await this.publish(session.cut, [...session.required], view, signal);
        const reservation = await this.busy(signal => this.ctx.executionFence.reserve(proposal, signal), signal);
        this.complete = false;
        try {
            const trigger = (proposal.payload as {
                trigger?: Json;
            })?.trigger ?? null;
            const logicalTime = view.clock.advance(session.frontier.logicalTime as Json, trigger);
            const raw = await this.executeRuntime(proposal, view, reservation, logicalTime, signal);
            const completed = this.recovery?.proposalId === proposal.id ? this.completedOperations(raw) : [];
            for (const operation of completed) this.recovery!.operations.add(operation);
            const merged = this.recovery?.proposalId === proposal.id && this.recovery.operations.size ? { ...raw, requiredOperations: [...new Set([...raw.requiredOperations, ...this.recovery.operations])] } : raw;
            const failed = this.failedActivations(merged, view.runtime);
            const retry = failed.length > 0 && (this.recovery?.proposalId === proposal.id ? this.recovery.attempts : 0) < this.config.autoRecover.maxAttempts;
            const result = failed.length && !retry ? await this.degrade(merged, failed, logicalTime) : merged;
            this.recordActivations(result, logicalTime);
            if (retry) {
                const paused = await this.incompleteProposal(session, view, reservation, result, true);
                return { kind: 'paused', result: paused, advance: result, completed: this.completedActivations(result), failed };
            }
            const completion = view.completion.evaluate(result);
            if (result.status !== 'settled' || !completion.complete) {
                const effective = result.status === 'incomplete' || !completion.complete ? { ...result, status: 'incomplete' as const } : result;
                const termination = view.termination.evaluate({ kind: 'advance', result: effective, cut: session.cut, logicalTime });
                const paused = await this.incompleteProposal(session, view, reservation, result);
                if (termination.action === 'fail') {
                    this.lastFailure ??= termination.reason;
                    this.ctx.executionFence.halt(termination.reason);
                    return { kind: 'failed', result: { ...paused, status: 'failed', reason: termination.reason } };
                }
                return { kind: 'paused', result: paused, advance: result, completed: this.completedActivations(result) };
            }
            await this.verifyRequired(result.requiredOperations, session.required);
            await this.completeProposal(session, proposal, reservation, logicalTime, result);
            return {
                kind: 'advanced', result
            };
        }
        catch (error) {
            reservation.cancel(String(error));
            this.ctx.executionFence.halt(String(error));
            await view.runtime.drain('run failed');
            throw error;
        }
        finally {
            await reservation.release();
        }
    }
    private async executeRuntime(proposal: Proposal, view: GenerationView, reservation: ExecutionReservation, logicalTime: Json, signal: AbortSignal) {
        const recovery = this.recovery?.proposalId === proposal.id ? this.recovery : undefined, recoveryAttempt = recovery?.attempts ?? 0;
        const context: ExecutionContext = {
            ctx: this.ctx, runId: reservation.lease.runId, generation: view.generation, operationId: proposal.id, signal: AbortSignal.any([signal, reservation.signal]), lease: reservation.lease, database: this.ctx.database, logicalTime, selections: [], ...(recoveryAttempt ? { recoveryAttempt } : {}), ...(recovery?.completed.size ? { completedActivations: new Set(recovery.completed) } : {})
        };
        const abort = () => reservation.cancel(String(signal.reason ?? 'run cancelled'));
        signal.addEventListener('abort', abort, {
            once: true
        });
        try {
            this.activeWork = view.runtime.execute(proposal, context);
            const result = await this.activeWork as AdvanceResult;
            this.lastAdvance = result;
            return result;
        }
        finally {
            await view.runtime.drain('proposal settled');
            this.activeWork = undefined;
            signal.removeEventListener('abort', abort);
        }
    }
    private async incompleteProposal(session: RunSession, view: GenerationView, reservation: ExecutionReservation, result: AdvanceResult, retrying = false): Promise<RunResult> {
        if (!retrying) this.deferred()?.discard();
        await reservation.release();
        await view.source.release(session.proposal!);
        session.proposal = undefined;
        const liveRevision = await this.ctx.database.readRevision();
        return {
            status: 'paused', cut: session.cut, reason: result.status, liveRevision, recoveryRequired: liveRevision !== session.cut.revision
        };
    }
    private async verifyRequired(operations: readonly string[], required: Set<string>) {
        for (const operation of operations) {
            const receipt = await this.ctx.database.probe(operation);
            if (receipt.status !== 'committed')
                throw Error('RUN_REQUIRED_OPERATION ' + operation);
            required.add(operation);
        }
    }
    private async completeProposal(session: RunSession, proposal: Proposal, reservation: ExecutionReservation, logicalTime: Json, result: AdvanceResult) {        const acknowledgements = [...this.pending, proposal].filter(item => item.reservation).map(item => ({
            source: item.reservation!.source, reservationId: item.reservation!.id, proposalId: item.id
        }));
        session.frontier = {
            codec: result.frontier.codec, value: result.frontier.value, logicalTime, completedCount: String(BigInt(session.frontier.completedCount) + 1n), pendingAcknowledgements: acknowledgements
        };
        const operationId = 'complete-' + randomUUID();
        const deferred = this.deferred();
        const pending = deferred?.pending().length ?? 0;
        await this.ctx.database.transaction(reservation.lease, {
            operationId, maxDurationMs: this.config.maxTransactionMs, maxStatements: result.requiredOperations.length + 1 + pending, maxInputBytes: 1048576, maxChangedRows: 100 + pending, maxCapturedBytes: 4194304
        }, tx => this.ctx.database.metadata.complete(tx, session.frontier, result.requiredOperations).then(() => deferred?.commit(tx, String(logicalTime))));
        session.required.add(operationId);
        this.complete = true;
        deferred?.wake();
    }
    private deferred(): DeferredWork | undefined {
        return this.ctx.get('deferredWork', false) as DeferredWork | undefined;
    }
    /** 指标是观测：失败不改变推进、恢复与终止结果。 */
    private async flushMetrics(session: RunSession): Promise<void> {
        const metrics = metricsOf(this.ctx);
        if (!metrics) return;
        try {
            await metrics.tick({ sequence: session.frontier.completedCount, logicalTime: session.frontier.logicalTime as Json });
        }
        catch {
        }
    }
    private async advanceBoundary(session: RunSession, view: GenerationView, result: AdvanceResult, signal: AbortSignal): Promise<RunResult | undefined> {
        await this.flushMetrics(session);
        if (session.proposal!.reservation)
            this.pending.push(session.proposal!);
        session.proposal = undefined;
        const completion = view.completion.evaluate(result);
        const checkpoint = view.checkpoint.choose({
            result, previous: session.cut, frontier: session.frontier.value as Json, advancesSinceCut: String(BigInt(session.frontier.completedCount) - BigInt(session.cut.frontier.completedCount)), completion
        });
        if (checkpoint === 'publish-cut') {
            session.cut = await this.publish(session.cut, [...session.required], view, signal);
            session.required.clear();
        }
        const termination = view.termination.evaluate({
            kind: 'advance', result, cut: session.cut, logicalTime: session.frontier.logicalTime as Json
        });
        if (termination.action === 'fail')
            throw Error(termination.reason);
        if (termination.action === 'continue')
            return;
        session.cut = await this.publish(session.cut, [...session.required], view, signal);
        return {
            status: termination.action === 'stop' ? 'completed' : 'paused', cut: session.cut, reason: termination.reason
        };
    }
    private async releaseProposal(session: RunSession | undefined) {
        if (session?.proposal)
            await this.view?.source.release(session.proposal).catch(() => {
            });
    }
    private async failRun(error: unknown, session: RunSession | undefined, cut: CompletePoint | undefined): Promise<RunResult> {
        const reason = error instanceof Error ? error.message : String(error);
        this.lastFailure ??= reason;
        this.ctx.executionFence.halt(this.lastFailure);
        await this.releaseProposal(session);
        const recoveryCut = this.latestCut ?? session?.cut ?? cut;
        const liveRevision = await this.ctx.database.readRevision().catch(() => undefined);
        return {
            status: 'failed', cut: recoveryCut, reason: this.lastFailure, liveRevision, recoveryRequired: liveRevision === undefined || liveRevision !== recoveryCut?.revision
        };
    }
    private async releaseSession(session: RunSession | undefined) {
        if (this.lastFailure || !this.complete) {
            for (const pending of this.pending)
                await this.view?.source.release(pending).catch(() => {
                });
            this.pending = [];
        }
        await session?.held?.release();
        this.view = undefined;
    }
    private async awaitPause(): Promise<RunResult | undefined> {
        const running = this.running;
        if (!running)
            return undefined;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const deadline = new Promise<never>((_, reject) => {
            timer = setTimeout(() => {
                const reason = `RUN_PAUSE_TIMEOUT: no safe boundary after ${this.config.pauseTimeoutMs}ms`;
                this.ctx.executionFence.halt(reason);
                reject(Object.assign(Error(reason), { timeoutMs: this.config.pauseTimeoutMs, progress: this.diagnostics() }));
            }, this.config.pauseTimeoutMs);
            timer.unref();
        });
        try {
            return await Promise.race([running, deadline]);
        }
        finally {
            if (timer)
                clearTimeout(timer);
        }
    }
    async pauseAtBoundary(): Promise<BoundaryLease> {
        this.stop = true;
        this.wait?.abort(Error('boundary requested'));
        const result = await this.awaitPause();
        if (result?.status === 'failed' || !this.complete)
            throw Error('RUN_NO_BOUNDARY');
        const cut = await this.ctx.history.current();
        if (cut.revision !== await this.ctx.database.readRevision())
            throw Error('RUN_DIRTY_BOUNDARY');
        return this.boundaryLease(cut, await this.ctx.executionFence.seal());
    }
    private boundaryLease(cut: CompletePoint, lease: SealLease): BoundaryLease {
        const state = {
            finalized: false
        };
        return {
            generation: cut.generation, cut, finalize: async (outcome) => this.finalizeBoundary(state, cut, lease, outcome), dispose: async () => {
                if (!state.finalized) {
                    state.finalized = true;
                    this.ctx.executionFence.halt('boundary disposed without finalization');
                }
            }
        };
    }
    private finalizeBoundary(state: {
        finalized: boolean;
    }, cut: CompletePoint, lease: SealLease, outcome: Parameters<BoundaryLease['finalize']>[0]) {
        if (state.finalized)
            throw Error('BOUNDARY_FINALIZED');
        state.finalized = true;
        if (outcome.kind === 'halt') {
            this.ctx.executionFence.halt(outcome.reason);
            return;
        }
        if (outcome.generation !== cut.generation || outcome.controlSequence !== cut.controlSequence) {
            this.ctx.executionFence.halt('BOUNDARY_IDENTITY');
            throw Error('BOUNDARY_IDENTITY');
        }
        this.ctx.executionFence.resume(lease);
    }
    diagnostics() {
        return {
            failure: this.lastFailure, advance: this.lastAdvance, active: !!this.running, complete: this.complete
        };
    }
    async close() {
        this.closing = true;
        this.stop = true;
        this.wait?.abort(Error('run closing'));
        await this.running;
        await this.activeWork;
    }
}
export default RunController;
