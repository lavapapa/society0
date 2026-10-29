import { Retention } from '@society0/host/composition-strategies';
import { RuntimeWork, activationStatus } from '../runtime-staged/lifecycle.js';
import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = {
    "title": "runtime-events", "summary": "runtime-events", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1"
};
export const api: ApiEntry = {
    "exportPath": "./runtime-events", "provides": [{
            "key": "runtimeDriver", "protocol": "runtimeDriver", "major": 1, "capabilities": []
        }, {
            key: "eventHandlers", protocol: "eventHandlers", major: 1, capabilities: []
        }], "requires": [{
            "service": {
                "key": "database", "protocol": "database", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            service: {
                key: 'activation', protocol: 'activation', major: 1, capabilities: []
            }, optional: false, phase: 'module'
        }]
};
import type { ActivationIntent, ActivationResult, AdvanceResult, ExecutionContext, Proposal, RuntimeDriver as Runtime, Json } from '../contracts/runtime.js';
import type { SqliteTransaction } from '../sqlite/contracts.js';
import { integer } from '../clock-ticks/index.js';
import '../sqlite/index.js';
import '../activation/index.js';
export * from './schema.js';
export interface RuntimeEvent {
    readonly id: string;
    readonly offset: string;
    readonly time: string;
    readonly payload: Json;
}
export interface EventHandler {
    readonly id: string;
    execute(event: RuntimeEvent, tx: SqliteTransaction, context: ExecutionContext): Promise<void>;
    /** 事件事务提交后生成意图；执行与物理等待由 Activation 管理。 */
    activations?(event: RuntimeEvent): readonly ActivationIntent[];
}
export type EventHandlers = Pick<RuntimeDriver, 'register'>;
declare module '@society0/host' {
    interface Context {
        eventHandlers: EventHandlers;
    }
}
interface EventPlan {
    events: RuntimeEvent[];
    nextCursor: string;
}
export class RuntimeDriver extends Service implements Runtime {
    static Config = Config;
    static inject = ['database', 'activation'];
    readonly planKinds = ['events/1'];
    private handlers = new Map<string, {
        value: EventHandler;
        retention: Retention;
    }>();
    private work: RuntimeWork;
    constructor(ctx: Context) {
        super(ctx, 'runtimeDriver');
        this.work = new RuntimeWork(ctx.activation);
        ctx.provide('eventHandlers', this);
    }
    register(handler: EventHandler) {
        if (this.handlers.has(handler.id))
            throw Error('EVENT_HANDLER_DUPLICATE');
        const record = {
            value: handler, retention: new Retention()
        };
        this.handlers.set(handler.id, record);
        return this.ctx.effect(() => async () => {
            this.handlers.delete(handler.id);
            await record.retention.retire();
        });
    }
    execute(proposal: Proposal, context: ExecutionContext): Promise<AdvanceResult> {
        return this.work.track(context, this.advance(proposal, context));
    }
    private plan(proposal: Proposal) {
        if (proposal.planKind !== 'events/1')
            throw Error('RUNTIME_PLAN_KIND');
        const plan = proposal.payload as unknown as EventPlan;
        if (!Array.isArray(plan?.events) || typeof plan.nextCursor !== 'string')
            throw Error('EVENT_PLAN');
        if (integer(plan.nextCursor) <= integer(proposal.cursor))
            throw Error('EVENT_CURSOR');
        this.validateOrder(plan, proposal.cursor);
        return plan;
    }
    private validateOrder(plan: EventPlan, cursor: Json) {
        let previous = integer(cursor);
        for (const event of plan.events) {
            integer(event.time);
            const offset = integer(event.offset);
            if (offset <= previous || offset > integer(plan.nextCursor))
                throw Error('EVENT_ORDER');
            previous = offset;
        }
    }
    private async executeEvent(event: RuntimeEvent, proposal: Proposal, context: ExecutionContext, admissions: Json[]) {
        context.signal.throwIfAborted();
        const handlerId = (event.payload as {
            handler?: string;
        })?.handler;
        if (!handlerId)
            throw Error('EVENT_HANDLER_REQUIRED');
        const handler = this.handlers.get(handlerId);
        if (!handler)
            throw Error('EVENT_HANDLER_MISSING ' + handlerId);
        const release = handler.retention.hold();
        try {
            const operationId = context.operationId + '/event/' + event.id, source = proposal.reservation?.source ?? 'events';
            await context.database.transaction(context.lease, {
                operationId, maxDurationMs: 15000, maxStatements: 128, maxInputBytes: 1048576, maxChangedRows: 10000, maxCapturedBytes: 4194304
            }, tx => this.writeEvent(tx, source, event, handler.value, context));
            const status = await this.activateEvent(handler.value, event, context, admissions);
            return {
                operationId, status
            };
        }
        finally {
            await release();
        }
    }
    private async writeEvent(tx: SqliteTransaction, source: string, event: RuntimeEvent, handler: EventHandler, context: ExecutionContext) {
        const found = await tx.query({
            sql: 'SELECT offset FROM s0_runtime_events WHERE source=? AND id=?', parameters: [source, event.id]
        }, {
            maxRows: 1, maxBytes: 4096
        });
        if (found.length) {
            if (String(found[0]!.offset) !== event.offset)
                throw Error('EVENT_ID_CONFLICT');
            return;
        }
        await handler.execute(event, tx, context);
        await tx.execute({
            sql: 'INSERT INTO s0_runtime_events VALUES (?,?,?)', parameters: [source, event.id, event.offset]
        });
    }
    private async activateEvent(handler: EventHandler, event: RuntimeEvent, context: ExecutionContext, admissions: Json[]) {
        context.signal.throwIfAborted();
        let rejected = false;
        for (const intent of handler.activations?.(event) ?? []) {
            const admission = await this.ctx.activation.submit(intent, context);
            admissions.push({
                ...admission
            });
            if (admission.kind === 'rejected')
                rejected = true;
        }
        const results = await this.ctx.activation.drain(context);
        return activationStatus(rejected, results);
    }
    private async executeEvents(plan: EventPlan, proposal: Proposal, context: ExecutionContext, admissions: Json[]) {
        const requiredOperations: string[] = [], eventIds: string[] = [];
        let status: AdvanceResult['status'] = 'settled';
        for (const event of plan.events) {
            const result = await this.executeEvent(event, proposal, context, admissions);
            requiredOperations.push(result.operationId);
            eventIds.push(event.id);
            status = result.status;
            if (status !== 'settled')
                break;
        }
        return {
            requiredOperations, eventIds, status
        };
    }
    private async advance(proposal: Proposal, context: ExecutionContext): Promise<AdvanceResult> {
        const plan = this.plan(proposal), admissions: Json[] = [];
        try {
            const executed = await this.executeEvents(plan, proposal, context, admissions), activations = await this.ctx.activation.drain(context);
            return {
                status: executed.status, frontier: {
                    codec: 'events/1', value: plan.nextCursor
                }, requiredOperations: [...executed.requiredOperations, ...activations.flatMap(result => result.requiredOperations ?? [])], evidence: {
                    eventIds: executed.eventIds, admissions, activations: activations as unknown as Json
                }
            };
        }
        catch (error) {
            await this.ctx.activation.cancel(context, 'event runtime failed').catch(() => {
            });
            throw error;
        }
    }
    async drain(reason: string) {
        await this.work.drain(reason);
    }
}
export default RuntimeDriver;
