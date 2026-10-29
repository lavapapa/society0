import { Retention } from '@society0/host/composition-strategies';
import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    sampling: Schema.string().default('every-advance/v1'), maxResultBytes: Schema.natural().min(1).default(49152)
});
export const metadata: PluginMetadata = {
    "title": "measurements", "summary": "measurements", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/sampling", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/maxResultBytes", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./measurements", "provides": [{
            "key": "measurements", "protocol": "measurements", "major": 1, "capabilities": []
        }], "requires": [{
            "service": {
                "key": "measurementRepository", "protocol": "measurementRepository", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "database", "protocol": "database", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "drivers", "protocol": "drivers", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "slots", "protocol": "slots", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "tasks", "protocol": "tasks", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "attempts", "protocol": "attempts", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import { slotKey } from '@society0/host/slots';
import type { TaskScope } from '@society0/host/tasks';
import '@society0/core/drivers';
import '@society0/core/attempts';
import '../measurement-repository/index.js';
import type { ActivationContext, ActivationResult } from '@society0/core/contracts/runtime';
import type { Json } from '../contracts.js';
import { bytes, transactionRequest } from '../paging.js';
export interface SamplingPolicy {
    id: string;
    shouldSample(frontier: Json): boolean;
}
export const samplingSlot = slotKey<SamplingPolicy>('society/measurement-sampling');
export interface MeasurementSpec {
    id: string;
    kind: 'measurement' | 'interview';
    required: boolean;
    observe(context: ActivationContext): Promise<{
        value: Json;
        original: Json;
        source: Json;
    }>;
}
declare module '@society0/host' {
    interface Context {
        measurements: Measurements;
    }
}
export class Measurements extends Service {
    static Config = Config;
    static inject = ['measurementRepository', 'database', 'drivers', 'slots', 'tasks', 'attempts'];
    private specs = new Map<string, {
        spec: MeasurementSpec;
        retention: Retention;
    }>();
    private tasks: TaskScope;
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input = {}) {
        super(ctx, 'measurements');
        this.config = Config(input);
        this.tasks = ctx.tasks.bind();
        ctx.slots.declare({
            key: samplingSlot, strategy: 'keyed', required: true, validate: (v: unknown) => {
                if (typeof (v as SamplingPolicy)?.shouldSample !== 'function')
                    throw Error('MEASUREMENT_SAMPLING');
                return v as SamplingPolicy;
            }
        });
        ctx.drivers.register('measurements', {
            activate: (_intent, context) => this.collect(context)
        });
        ctx.effect(() => () => this.tasks.drain('measurements closing'));
    }
    register(spec: MeasurementSpec) {
        if (this.specs.has(spec.id))
            throw Error('MEASUREMENT_DUPLICATE');
        const record = {
            spec, retention: new Retention()
        };
        this.specs.set(spec.id, record);
        return this.ctx.effect(() => async () => {
            this.specs.delete(spec.id);
            await record.retention.retire();
        });
    }
    async collect(context: ActivationContext): Promise<ActivationResult> {
        const sampling = await this.ctx.slots.acquire(samplingSlot, context);
        const records = [...this.specs.values()];
        const releases = records.map(record => record.retention.hold());
        try {
            const policy = sampling.items.find(x => x.id === this.config.sampling)?.value;
            if (!policy)
                throw Error('MEASUREMENT_SAMPLING_MISSING');
            if (!policy.shouldSample(context.logicalTime))
                return {
                    status: 'completed', value: [], evidence: {
                        sampling: policy.id
                    }, requiredOperations: []
                };
            return await this.runSpecs(records.map(record => record.spec), context);
        }
        finally {
            await Promise.all([sampling.release(), ...releases.map(release => release())]);
        }
    }
    private async runSpecs(specs: readonly MeasurementSpec[], context: ActivationContext): Promise<ActivationResult> {
        const operations: string[] = [], outcomes: Json[] = [];
        let incomplete = false;
        for (const spec of specs) {
            context.signal.throwIfAborted();
            const result = await this.runOne(spec, context);
            outcomes.push(result.outcome);
            if (result.operationId)
                operations.push(result.operationId);
            if (result.incomplete)
                incomplete = true;
        }
        return {
            status: incomplete ? 'incomplete' : 'completed', value: outcomes, evidence: {
                outcomes
            }, requiredOperations: operations
        };
    }
    private async runOne(spec: MeasurementSpec, context: ActivationContext) {
        const operationId = context.operationId + '/measurement/' + spec.id;
        const task = this.tasks.run('measure:' + spec.id, signal => this.executeSpec(spec, {
            ...context, signal
        }, operationId), {
            signal: context.signal, bytes: this.config.maxResultBytes
        });
        try {
            const result = await task;
            return {
                operationId, incomplete: false, outcome: {
                    id: spec.id, status: 'completed', ...result
                } as Json
            };
        }
        catch (error) {
            return {
                incomplete: spec.required, outcome: {
                    id: spec.id, status: 'incomplete', failure: String(error), required: spec.required
                } as Json
            };
        }
        finally {
            await task.settled;
        }
    }
    private async executeSpec(spec: MeasurementSpec, context: ActivationContext, operationId: string) {
        const observation = spec.kind === 'interview' ? await this.interview(spec, context, operationId) : await spec.observe(context);
        if (bytes(observation) > this.config.maxResultBytes)
            throw Error('MEASUREMENT_RESULT_BYTES');
        context.signal.throwIfAborted();
        await context.database.transaction(context.writeLease, transactionRequest(operationId), tx => this.ctx.measurementRepository.append(tx, {
            id: operationId, specId: spec.id, kind: spec.kind, actorId: context.actorId, logicalTime: context.logicalTime, ...observation, required: spec.required
        }));
        return observation;
    }
    private async interview(spec: MeasurementSpec, context: ActivationContext, operationId: string) {
        const identity = {
            lineage: this.ctx.attempts.authority().lineage, operationId, attempt: 0
        };
        const claim = await this.ctx.attempts.claim(identity, {
            actorId: context.actorId, momentId: context.momentId, logicalTime: context.logicalTime, specId: spec.id
        });
        if (claim.status === 'unknown')
            throw Error('MEASUREMENT_ATTEMPT_UNKNOWN');
        if (claim.status === 'existing')
            return this.replayInterview(claim.record);
        let observation;
        try {
            observation = await spec.observe(context);
        }
        catch (error) {
            await this.settleInterview(identity, {
                ok: false, failure: String(error), original: (error as {
                    original?: Json;
                })?.original ?? null
            });
            throw error;
        }
        await this.settleInterview(identity, {
            ok: true, observation
        });
        return observation;
    }
    private replayInterview(record: import('@society0/core/attempts').AttemptRecord) {
        if (record.status !== 'settled')
            throw Error('MEASUREMENT_ATTEMPT_PENDING');
        const saved = record.result as {
            ok: boolean;
            observation: Awaited<ReturnType<MeasurementSpec['observe']>>;
            failure?: string;
            evidence?: unknown;
        };
        if (saved.evidence)
            throw Error('MEASUREMENT_RESULT_BYTES');
        if (!saved.ok)
            throw Error(saved.failure);
        return saved.observation;
    }
    private async settleInterview(identity: import('@society0/core/attempts').AttemptIdentity, value: unknown) {
        if (bytes(value) <= this.config.maxResultBytes) {
            await this.ctx.attempts.settle(identity, value);
            return;
        }
        const data = Buffer.from(JSON.stringify(value)), chunkBytes = this.ctx.attempts.evidenceChunkBytes;
        const ref = await this.ctx.attempts.stageEvidence((async function* () {
            for (let offset = 0; offset < data.byteLength; offset += chunkBytes)
                yield data.subarray(offset, offset + chunkBytes);
        })(), 'application/json');
        await this.ctx.attempts.settle(identity, {
            evidence: ref, bytes: String(data.byteLength)
        }, [ref]);
    }
}
export default Measurements;
