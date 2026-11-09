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
        }], "slots": { "provides": ["core/drivers/measurements"] }, "requires": [{
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
        }, {
            "service": {
                "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": []
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
import type { MeasurementObservation, PreparedMeasurement } from '../contracts.js';
import type { AttemptIdentity, AttemptRecord } from '@society0/core/attempts';
import type { ArtifactRef } from '@society0/core/artifacts-local';
import type { StoredValue } from '@society0/core/contracts/data';
import { jsonByteBound, prepareValue, textChunks } from '@society0/core/results';
import { transactionRequest } from '../paging.js';
export interface SamplingPolicy {
    id: string;
    shouldSample(frontier: Json): boolean;
}
export const samplingSlot = slotKey<SamplingPolicy>('society/measurement-sampling');
export interface MeasurementSpec {
    id: string;
    kind: 'measurement' | 'interview';
    required: boolean;
    observe(context: ActivationContext): Promise<MeasurementObservation>;
}
declare module '@society0/host' {
    interface Context {
        measurements: Measurements;
    }
}
const FAILURE_INLINE = 4096;
const ENVELOPE_SLACK = 256;
interface InterviewEnvelope {
    ok: boolean;
    failure?: StoredValue;
    original?: StoredValue;
    observation?: PreparedMeasurement;
    evidence?: ArtifactRef | null;
    bytes?: string;
    envelope?: ArtifactRef;
}
type PersistResult = {
    status: 'stored';
    measurement: PreparedMeasurement;
} | {
    status: 'rejected';
    code: 'MEASUREMENT_RESULT_BYTES' | 'MEASUREMENT_RESULT_INVALID';
};
class MeasurementFailure extends Error {
    constructor(readonly text: string, options?: {
        cause?: unknown;
    }) {
        super(text, options);
        this.name = 'MeasurementFailure';
    }
    override toString() {
        return this.text;
    }
}
const failureText = (error: unknown) => String(error);
function measurementRefs(value?: PreparedMeasurement) {
    const refs: ArtifactRef[] = [];
    for (const stored of value ? [value.value, value.original, value.source] : [])
        if (stored.kind === 'reference')
            refs.push(stored.ref.object);
    return refs;
}
function refsOf(envelope: InterviewEnvelope) {
    const refs = measurementRefs(envelope.observation);
    for (const stored of [envelope.failure, envelope.original])
        if (stored?.kind === 'reference')
            refs.push(stored.ref.object);
    if (envelope.evidence)
        refs.push(envelope.evidence);
    return refs;
}
export class Measurements extends Service {
    static Config = Config;
    static inject = ['measurementRepository', 'database', 'drivers', 'slots', 'tasks', 'attempts', 'artifacts'];
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
        const stored = spec.kind === 'interview' ? await this.interview(spec, context, operationId) : await this.measure(spec, context);
        context.signal.throwIfAborted();
        await context.database.transaction(context.writeLease, transactionRequest(operationId), tx => this.ctx.measurementRepository.append(tx, {
            id: operationId, specId: spec.id, kind: spec.kind, actorId: context.actorId, logicalTime: context.logicalTime, ...stored, required: spec.required
        }));
        return stored;
    }
    private async measure(spec: MeasurementSpec, context: ActivationContext) {
        const observation = await spec.observe(context);
        const check = this.checkResult(observation);
        if (check !== 'ok')
            throw new MeasurementFailure(check);
        return this.ctx.measurementRepository.prepare(observation, context.signal);
    }
    private checkResult(observation: MeasurementObservation): 'ok' | 'MEASUREMENT_RESULT_BYTES' | 'MEASUREMENT_RESULT_INVALID' {
        try {
            return jsonByteBound(observation, this.config.maxResultBytes) > this.config.maxResultBytes ? 'MEASUREMENT_RESULT_BYTES' : 'ok';
        }
        catch (error) {
            if ((error as {
                code?: string;
            }).code === 'PAYLOAD_JSON_VALUE')
                return 'MEASUREMENT_RESULT_INVALID';
            throw error;
        }
    }
    private async interview(spec: MeasurementSpec, context: ActivationContext, operationId: string) {
        const identity = {
            lineage: this.ctx.attempts.authority().lineage, operationId, attempt: 0
        };
        const claim = await this.ctx.attempts.claim(identity, {
            actorId: context.actorId, momentId: context.momentId, logicalTime: context.logicalTime, specId: spec.id
        });
        if (claim.status === 'unknown')
            throw new MeasurementFailure('MEASUREMENT_ATTEMPT_UNKNOWN');
        if (claim.status === 'existing')
            return this.replayInterview(claim.record);
        let observation: MeasurementObservation;
        try {
            observation = await spec.observe(context);
        }
        catch (error) {
            return this.failedInterview(identity, error, context.signal);
        }
        let persisted: PersistResult;
        try {
            persisted = await this.persistObservation(identity, observation, context.signal);
        }
        catch (cause) {
            return this.raiseUnknown(cause);
        }
        if (persisted.status === 'rejected')
            throw new MeasurementFailure(persisted.code);
        return persisted.measurement;
    }
    private async failedInterview(identity: AttemptIdentity, error: unknown, signal?: AbortSignal): Promise<never> {
        let text: string;
        try {
            text = failureText(error);
            const original = await this.storeOne((error as {
                original?: Json;
            })?.original ?? null, signal);
            const failure = await this.storeFailure(text, signal);
            await this.settleEnvelope(identity, {
                ok: false, failure, original
            }, signal);
        }
        catch (cause) {
            return this.raiseUnknown(cause);
        }
        throw new MeasurementFailure(text, {
            cause: error
        });
    }
    private async persistObservation(identity: AttemptIdentity, observation: MeasurementObservation, signal?: AbortSignal): Promise<PersistResult> {
        const check = this.checkResult(observation);
        if (check === 'MEASUREMENT_RESULT_INVALID') {
            await this.settleEnvelope(identity, {
                ok: false, failure: await this.storeFailure(check, signal)
            }, signal);
            return {
                status: 'rejected', code: check
            };
        }
        if (check === 'MEASUREMENT_RESULT_BYTES') {
            await this.settleOversize(identity, observation, signal);
            return {
                status: 'rejected', code: check
            };
        }
        const stored = await this.ctx.measurementRepository.prepare(observation, signal);
        await this.settleEnvelope(identity, {
            ok: true, observation: stored
        }, signal);
        return {
            status: 'stored', measurement: stored
        };
    }
    private raiseUnknown(cause: unknown): never {
        if (cause instanceof MeasurementFailure && cause.text === 'MEASUREMENT_ATTEMPT_UNKNOWN')
            throw cause;
        throw new MeasurementFailure('MEASUREMENT_ATTEMPT_UNKNOWN', {
            cause
        });
    }
    private async replayInterview(record: AttemptRecord): Promise<PreparedMeasurement> {
        if (record.status !== 'settled')
            throw new MeasurementFailure('MEASUREMENT_ATTEMPT_UNKNOWN');
        const saved = await this.envelope(record.result);
        if (!saved.ok)
            throw new MeasurementFailure(await this.failure(saved.failure!));
        for (const ref of measurementRefs(saved.observation))
            await this.ctx.attempts.materialize(ref);
        return saved.observation!;
    }
    private async envelope(result: unknown): Promise<InterviewEnvelope> {
        if (!result || typeof result !== 'object' || !('envelope' in result))
            return result as InterviewEnvelope;
        const ref = (result as {
            envelope: ArtifactRef;
        }).envelope;
        return JSON.parse((await this.read(ref, this.ctx.attempts.evidenceMaxObjectBytes)).toString()) as InterviewEnvelope;
    }
    private async failure(value: StoredValue) {
        if (value.kind === 'inline')
            return String(value.value);
        return String(JSON.parse((await this.read(value.ref.object, this.ctx.artifacts.maxObjectBytes)).toString()));
    }
    private async read(ref: ArtifactRef, limit: number) {
        const bytes = Number(ref.bytes);
        if (!Number.isSafeInteger(bytes) || bytes < 0 || bytes > limit)
            throw new MeasurementFailure('MEASUREMENT_ENVELOPE_BUDGET');
        const chunks: Uint8Array[] = [];
        for await (const chunk of this.ctx.attempts.readArtifact(ref))
            chunks.push(chunk);
        return Buffer.concat(chunks);
    }
    private async settleEnvelope(identity: AttemptIdentity, envelope: InterviewEnvelope, signal?: AbortSignal) {
        const refs = refsOf(envelope), text = JSON.stringify(envelope);
        if (Buffer.byteLength(text) + ENVELOPE_SLACK <= this.ctx.attempts.maxRecordBytes) {
            await this.ctx.attempts.settle(identity, envelope, refs);
            return;
        }
        const ref = await this.ctx.attempts.stageEvidence(textChunks(text, this.ctx.attempts.evidenceChunkBytes, signal), 'application/json', {
            signal
        });
        await this.ctx.attempts.settle(identity, {
            envelope: ref
        }, [ref, ...refs]);
    }
    private async settleOversize(identity: AttemptIdentity, observation: MeasurementObservation, signal?: AbortSignal) {
        const failure: StoredValue = {
            kind: 'inline', value: 'MEASUREMENT_RESULT_BYTES'
        };
        const limit = this.ctx.attempts.evidenceMaxObjectBytes;
        const bound = jsonByteBound(observation, limit);
        if (bound > limit) {
            await this.settleEnvelope(identity, {
                ok: false, failure, evidence: null, bytes: '>' + limit
            }, signal);
            return;
        }
        const evidence = await this.ctx.attempts.stageEvidence(textChunks(JSON.stringify(observation), this.ctx.attempts.evidenceChunkBytes, signal), 'application/json', {
            signal
        });
        await this.settleEnvelope(identity, {
            ok: false, failure, evidence, bytes: String(bound)
        }, signal);
    }
    private async storeOne(value: Json, signal?: AbortSignal) {
        return (await this.ctx.measurementRepository.prepare({
            value: null, original: value, source: null
        }, signal)).original;
    }
    private storeFailure(text: string, signal?: AbortSignal) {
        // 内联与主库引用分别沿用各自的有效容量。
        const limit = Math.max(FAILURE_INLINE, this.ctx.artifacts.maxObjectBytes);
        if (jsonByteBound(text, limit) > limit)
            throw new Error('MEASUREMENT_FAILURE_BYTES');
        return prepareValue(text, FAILURE_INLINE, this.ctx.artifacts, signal);
    }
}
export default Measurements;
