import Schema from '@society0/schemastery';
import { hostMetadata, hostApi } from '../module-resolver/metadata.js';
import type { Context } from '@society0/cordis';
import type { Candidate, CompositionStrategy } from '../composition-strategies/index.js';
export const Config = Schema.object({});
export const metadata = hostMetadata('slot-strategies', 'Register single ordered keyed and middleware strategies', [], ['host', 'run', 'generation']);
export const api = hostApi('./slot-strategies', [], ["compositionStrategies"]);
class CandidateOrder {
    private nodes: Map<string, Candidate>;
    private edges: Map<string, Set<string>>;
    private incoming: Map<string, number>;
    constructor(candidates: readonly Candidate[]) {
        this.nodes = new Map(candidates.map(candidate => [candidate.id, candidate]));
        if (this.nodes.size !== candidates.length)
            throw Error('CONTRIBUTION_DUPLICATE');
        this.edges = new Map(candidates.map(candidate => [candidate.id, new Set<string>()]));
        this.incoming = new Map(candidates.map(candidate => [candidate.id, 0]));
        for (const candidate of candidates) {
            for (const id of candidate.before ?? [])
                this.edge(candidate.id, id);
            for (const id of candidate.after ?? [])
                this.edge(id, candidate.id);
        }
    }
    private edge(from: string, to: string): void {
        if (!this.nodes.has(from) || !this.nodes.has(to))
            throw Error(`ORDER_TARGET_MISSING ${from} -> ${to}`);
        if (this.edges.get(from)!.has(to))
            return;
        this.edges.get(from)!.add(to);
        this.incoming.set(to, this.incoming.get(to)! + 1);
    }
    private compare(a: string, b: string): number { return (this.nodes.get(a)!.order ?? 0) - (this.nodes.get(b)!.order ?? 0) || a.localeCompare(b); }
    result(): readonly string[] {
        const result: string[] = [];
        const ready = [...this.incoming].filter(([, count]) => !count).map(([id]) => id).sort((a, b) => this.compare(a, b));
        while (ready.length) {
            const id = ready.shift()!;
            result.push(id);
            for (const to of this.edges.get(id)!) {
                this.incoming.set(to, this.incoming.get(to)! - 1);
                if (!this.incoming.get(to)) {
                    ready.push(to);
                    ready.sort((a, b) => this.compare(a, b));
                }
            }
        }
        if (result.length !== this.nodes.size)
            throw Error(`ORDER_CYCLE ${[...this.incoming].filter(([, count]) => count).map(([id]) => id).join(' -> ')}`);
        return result;
    }
}
export function ordered(candidates: readonly Candidate[]): readonly string[] { return new CandidateOrder(candidates).result(); }
export const strategies: readonly CompositionStrategy[] = [{ name: 'single', select: items => {
            if (items.length > 1)
                throw Error(`SINGLE_CONFLICT ${items.map(i => i.id).join(', ')}`);
            return items.map(i => i.id);
        } }, { name: 'ordered', select: ordered }, { name: 'keyed', select: ordered }, { name: 'middleware', select: ordered }];
export interface Middleware<I, O> {
    readonly name: string;
    execute(input: I, next: (input: I) => Promise<O>, signal: AbortSignal): Promise<O>;
}
export function composeMiddleware<I, O>(items: readonly Middleware<I, O>[], terminal: (input: I, signal: AbortSignal) => Promise<O>) {
    return (input: I, signal: AbortSignal): Promise<O> => {
        const dispatch = (index: number, value: I): Promise<O> => {
            signal.throwIfAborted();
            const item = items[index];
            if (!item)
                return terminal(value, signal);
            let called = false;
            return Promise.resolve().then(() => item.execute(value, next => {
                if (called)
                    return Promise.reject(Error(`MIDDLEWARE ${item.name}: next called more than once`));
                called = true;
                return dispatch(index + 1, next);
            }, signal));
        };
        return dispatch(0, input);
    };
}
export const inject = ['compositionStrategies'];
function apply(ctx: Context) {
    for (const strategy of strategies)
        ctx.compositionStrategies.register(strategy);
}
export default { Config, name: 'slot-strategies', inject, apply };
