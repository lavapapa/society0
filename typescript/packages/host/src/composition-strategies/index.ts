import Schema from '@society0/schemastery';
import { hostMetadata, hostApi } from '../module-resolver/metadata.js';
import { Context, Service, type Fiber } from '@society0/cordis';
import { Retention, type Lease } from './lifecycle.js';
export const Config = Schema.object({});
export const metadata = hostMetadata('composition-strategies', 'Register open named selection strategies', [], ['host', 'run', 'generation']);
export const api = hostApi('./composition-strategies', ["compositionStrategies"], []);
export { Retention, type Lease } from './lifecycle.js';
export interface Candidate {
    readonly id: string;
    readonly order?: number;
    readonly before?: readonly string[];
    readonly after?: readonly string[];
    readonly replace?: string;
}
export interface CompositionStrategy {
    readonly name: string;
    select(candidates: readonly Candidate[]): readonly string[];
}
export interface StrategyDescriptor {
    readonly name: string;
    readonly registrationId: string;
    readonly owner: {
        readonly uid: number | null;
        readonly name: string;
    };
    readonly retiring: boolean;
    readonly leases: number;
}
interface Registration {
    value: CompositionStrategy;
    descriptor: StrategyDescriptor;
    retention: Retention;
}
interface Watcher {
    ctx: Context;
    name: string;
    apply: (ctx: Context, descriptor: StrategyDescriptor) => unknown;
    fiber?: Fiber;
    registration?: Registration;
    stopped: boolean;
    transition: Promise<void>;
}
declare module '@society0/cordis' {
    interface Context {
        compositionStrategies: CompositionStrategies;
    }
}
export class CompositionStrategies extends Service {
    static Config = Config;
    private readonly registrations = new Map<string, Registration>();
    private readonly watchers = new Set<Watcher>();
    private sequence = 0;
    constructor(ctx: Context) { super(ctx, 'compositionStrategies'); }
    private refresh(w: Watcher): Promise<void> {
        w.transition = w.transition.then(async () => {
            const desired = w.stopped ? undefined : this.registrations.get(w.name);
            if (desired === w.registration)
                return;
            const previous = w.fiber;
            w.fiber = undefined;
            w.registration = undefined;
            await previous?.dispose();
            const current = w.stopped ? undefined : this.registrations.get(w.name);
            w.registration = current;
            if (current && !w.stopped)
                w.fiber = await w.ctx.plugin({ apply: ctx => w.apply(ctx, current.descriptor) });
        });
        return w.transition;
    }
    private notify(name: string) { return Promise.all([...this.watchers].filter(w => w.name === name).map(w => this.refresh(w))); }
    register(strategy: CompositionStrategy): () => Promise<void> {
        if (!strategy.name || typeof strategy.select !== 'function')
            throw Error('STRATEGY_INVALID');
        if (this.registrations.has(strategy.name))
            throw Error(`STRATEGY_DUPLICATE ${strategy.name}`);
        const retention = new Retention();
        const owner = { uid: this.ctx.fiber.uid, name: this.ctx.fiber.name };
        const descriptor: StrategyDescriptor = { name: strategy.name, registrationId: `${strategy.name}:${++this.sequence}`, owner, get retiring() { return retention.retiring; }, get leases() { return retention.leases; } };
        const record = { value: Object.freeze({ name: strategy.name, select: strategy.select.bind(strategy) }), retention, descriptor };
        return this.ctx.effect(() => {
            this.registrations.set(strategy.name, record);
            void this.notify(strategy.name).catch(error => this.ctx.logger.error(error));
            return async () => {
                if (this.registrations.get(strategy.name) === record)
                    this.registrations.delete(strategy.name);
                await Promise.all([this.notify(strategy.name), retention.retire()]);
            };
        }, `strategy.register(${strategy.name})`);
    }
    inject(name: string, apply: (ctx: Context, descriptor: StrategyDescriptor) => unknown): () => Promise<void> {
        const w: Watcher = { ctx: this.ctx, name, apply, stopped: false, transition: Promise.resolve() };
        return this.ctx.effect(() => { this.watchers.add(w); void this.refresh(w).catch(error => this.ctx.logger.error(error)); return async () => { w.stopped = true; this.watchers.delete(w); await this.refresh(w); }; }, `strategy.inject(${name})`);
    }
    async acquire(name: string, options: {
        readonly signal?: AbortSignal;
    } = {}): Promise<Lease<CompositionStrategy>> {
        options.signal?.throwIfAborted();
        const record = this.registrations.get(name);
        if (!record)
            throw Error(`STRATEGY_PENDING ${name}`);
        return Object.freeze({ value: record.value, release: record.retention.hold() });
    }
    describe(): readonly StrategyDescriptor[] { return [...this.registrations.values()].map(r => ({ ...r.descriptor })); }
}
export default CompositionStrategies;
