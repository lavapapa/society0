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
    maxTransactionMs: Schema.natural().min(1).default(15000), maxPendingAcknowledgements: Schema.natural().min(1).default(1024), pauseTimeoutMs: Schema.natural().min(1).default(30000)
});
export const metadata: PluginMetadata = {
    title: 'run-controller', summary: '固定代视图、真实排空与SQL完整点推进', configVersion: 1, configPolicy: [{
            path: '/maxTransactionMs', mode: 'new-run', affectsResults: false
        }, {
            path: '/maxPendingAcknowledgements', mode: 'new-run', affectsResults: true
        }, {
            path: '/pauseTimeoutMs', mode: 'boundary', affectsResults: false
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
import type { RunResult, ProposalResult } from '../contracts/runtime.js';
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
        const task = this.loop(signal);
        this.running = task;
        void task.finally(() => {
            this.running = undefined;
        }).catch(() => {
        });
        return task;
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
        let next: CompletePoint;
        try {
            next = await this.ctx.history.publish(lease, {
                operationId, id: operationId, parentId: cut.id, expectedRevision: sealedRevision, requiredOperations
            });
        }
        catch (error) {
            const probe = await this.ctx.history.probe(operationId);
            if (probe.status !== 'published')
                throw error;
            next = probe.point;
        }
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
            const advance = await this.executeProposal(session, view, signal);
            if (advance.kind === 'paused' || advance.kind === 'failed')
                return advance.result;
            const boundary = await this.advanceBoundary(session, view, advance.result, signal);
            if (boundary)
                return boundary;
            await session.held!.release();
            session.held = undefined;
        }
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
    private async executeProposal(session: RunSession, view: GenerationView, signal: AbortSignal): Promise<{
        kind: 'paused';
        result: RunResult;
    } | {
        kind: 'advanced';
        result: AdvanceResult;
    } | {
        kind: 'failed';
        result: RunResult;
    }> {
        const proposal = session.proposal!;
        this.deferred()?.begin();
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
            const result = await this.executeRuntime(proposal, view, reservation, logicalTime, signal);
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
                return { kind: 'paused', result: paused };
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
        const context: ExecutionContext = {
            ctx: this.ctx, runId: reservation.lease.runId, generation: view.generation, operationId: proposal.id, signal: AbortSignal.any([signal, reservation.signal]), lease: reservation.lease, database: this.ctx.database, logicalTime, selections: []
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
    private async incompleteProposal(session: RunSession, view: GenerationView, reservation: ExecutionReservation, result: AdvanceResult): Promise<RunResult> {
        this.deferred()?.discard();
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
