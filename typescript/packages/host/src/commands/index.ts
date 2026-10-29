import Schema from '@society0/schemastery';
import { hostMetadata, hostApi } from '../module-resolver/metadata.js';
import { Context, Service, ValidationError, getTraceable } from '@society0/cordis';
import type { StandardSchemaV1 } from '@standard-schema/spec';
import { Retention, type Lease } from '../composition-strategies/lifecycle.js';
import type { Json, ProgramSource } from '../config-resolvers/types.js';
import '../resources/index.js';
export const Config = Schema.object({});
export const metadata = hostMetadata('commands', 'Execute schema-validated caller-owned commands');
export const api = hostApi('./commands', ["commands"], ["resources"]);
export interface CommandRunHandle {
    readonly runId: string;
    readonly ctx: Context;
    readonly generation?: string;
}
export type CommandInvocation = {
    readonly requestId: string;
    readonly signal: AbortSignal;
    readonly source?: ProgramSource;
} & ({
    readonly kind: 'host';
} | {
    readonly kind: 'run';
    readonly run: Lease<CommandRunHandle>;
    readonly expectedGeneration?: string;
});
export type CommandSchema<T> = StandardSchemaV1<unknown, T> | ((value: any) => T);
export interface CommandSpec<I = unknown, O = unknown> {
    readonly name: string;
    readonly summary: string;
    readonly scope: 'host' | 'run';
    readonly input: CommandSchema<I>;
    readonly output?: CommandSchema<O>;
    execute(ctx: Context, input: I, invocation: CommandInvocation): O | Promise<O>;
}
interface RecordEntry {
    spec: CommandSpec;
    retention: Retention;
    owner: {
        uid: number | null;
        name: string;
    };
}
function validate<T>(schema: CommandSchema<T>, value: unknown): T {
    if (typeof schema === 'function')
        return schema(value);
    const result = schema['~standard'].validate(value);
    if ('then' in result)
        throw Error('ASYNC_COMMAND_SCHEMA');
    if (result.issues)
        throw new ValidationError(result.issues);
    return result.value;
}
declare module '@society0/cordis' {
    interface Context {
        commands: Commands;
    }
}
export class Commands extends Service {
    static Config = Config;
    static inject = ['resources'];
    private readonly entries = new Map<string, RecordEntry>();
    constructor(ctx: Context) { super(ctx, 'commands'); }
    register<I, O>(spec: CommandSpec<I, O>): () => void | Promise<void> {
        if (this.entries.has(spec.name))
            throw Error(`COMMAND_DUPLICATE ${spec.name}`);
        const record: RecordEntry = { spec: spec as CommandSpec, retention: new Retention(), owner: { uid: this.ctx.fiber.uid, name: this.ctx.fiber.name } };
        const close = this.ctx.resources.use(`commands.register(${spec.name})`, async () => {
            if (this.entries.get(spec.name) === record)
                this.entries.delete(spec.name);
            await record.retention.retire();
        });
        this.entries.set(spec.name, record);
        return close;
    }
    private admit(name: string, invocation: CommandInvocation) {
        const record = this.entries.get(name);
        if (!record)
            throw Error(`COMMAND_MISSING ${name}`);
        if (record.spec.scope !== invocation.kind)
            throw Error(`COMMAND_SCOPE ${name}`);
        if (invocation.kind === 'run' && invocation.expectedGeneration !== undefined && invocation.run.value.generation !== invocation.expectedGeneration)
            throw Error(`COMMAND_GENERATION ${name}`);
        return record;
    }
    async execute(name: string, input: unknown, invocation: CommandInvocation): Promise<unknown> {
        let release: (() => Promise<void>) | undefined;
        let close: (() => void | Promise<void>) | undefined;
        const finished = Promise.withResolvers<void>();
        try {
            invocation.signal.throwIfAborted();
            const record = this.admit(name, invocation);
            release = record.retention.hold();
            close = this.ctx.resources.use(`commands.execute(${name}:${invocation.requestId})`, () => finished.promise);
            const caller = invocation.kind === 'run' ? invocation.run.value.ctx : this.ctx;
            const ctx = getTraceable(caller, caller);
            const value = await record.spec.execute(ctx, validate(record.spec.input, input), invocation);
            return record.spec.output ? validate(record.spec.output, value) : value;
        }
        finally {
            try {
                await release?.();
                if (invocation.kind === 'run')
                    await invocation.run.release();
            }
            finally {
                finished.resolve();
                await close?.();
            }
        }
    }
    describe() { return [...this.entries.values()].map(record => ({ name: record.spec.name, summary: record.spec.summary, scope: record.spec.scope, owner: { ...record.owner }, leases: record.retention.leases })); }
}
export default Commands;
