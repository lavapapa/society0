import { setImmediate } from 'node:timers/promises';
export type Algorithm = 'provided' | 'random' | 'small-world' | 'scale-free' | 'complete' | 'cv-targeted';
export interface TopologyOptions {
    algorithm: Algorithm;
    directed: boolean;
    probability: number;
    kNeighbors: number;
    mEdges: number;
    targetCV: number;
    convergenceThreshold: number;
    maxIterations: number;
    baseAlgorithm: 'random' | 'small-world' | 'scale-free';
    maxEdges: number;
}
type Edge = readonly [
    number,
    number
];
class Graph {
    readonly adjacency: Map<number, Set<number>>;
    private size = 0;
    constructor(readonly count: number, readonly maxEdges: number) {
        this.adjacency = new Map(Array.from({
            length: count
        }, (_, i) => [i, new Set<number>()]));
    }
    has(a: number, b: number) {
        return this.adjacency.get(a)!.has(b);
    }
    add(a: number, b: number) {
        if (a === b || this.has(a, b))
            return;
        if (this.size >= this.maxEdges)
            throw Error('SOCIAL_TOPOLOGY_EDGE_BUDGET');
        this.adjacency.get(a)!.add(b);
        this.size++;
    }
    remove(a: number, b: number) {
        if (this.adjacency.get(a)!.delete(b))
            this.size--;
    }
    *edges(): Generator<Edge> {
        for (const [a, neighbors] of this.adjacency)
            for (const b of [...neighbors].sort((x, y) => x - y))
                yield [a, b];
    }
    reciprocal(a: number) {
        const outgoing = this.adjacency.get(a)!, incoming = new Set([...this.adjacency].filter(([, set]) => set.has(a)).map(([id]) => id));
        const union = new Set([...outgoing, ...incoming]);
        return union.size ? [...outgoing].filter(id => incoming.has(id)).length / union.size : 0;
    }
    meanCV() {
        let total = 0;
        for (let i = 0; i < this.count; i++)
            total += this.reciprocal(i);
        return this.count ? total / this.count : 0;
    }
}
function choose<T>(items: readonly T[], draw: () => number): T | undefined {
    return items.length ? items[Math.floor(draw() * items.length)] : undefined;
}
function pickPair(graph: Graph, predicate: (a: number, b: number) => boolean, draw: () => number, ordered: boolean) {
    let found: Edge | undefined, count = 0;
    for (let a = 0; a < graph.count; a++)
        for (let b = ordered ? 0 : a + 1; b < graph.count; b++) {
            if (a === b || !predicate(a, b))
                continue;
            count++;
            if (draw() < 1 / count)
                found = [a, b];
        }
    return found;
}
function addUndirected(graph: Graph, a: number, b: number) {
    graph.add(a, b);
    graph.add(b, a);
}
function orient(base: Graph, options: TopologyOptions) {
    const result = new Graph(base.count, options.maxEdges);
    for (const [a, b] of base.edges())
        if (a < b) {
            result.add(a, b);
            if (!options.directed)
                result.add(b, a);
        }
    return result;
}
function complete(count: number, options: TopologyOptions) {
    const graph = new Graph(count, options.maxEdges);
    for (let a = 0; a < count; a++)
        for (let b = a + 1; b < count; b++) {
            graph.add(a, b);
            if (!options.directed)
                graph.add(b, a);
        }
    return graph;
}
function random(count: number, options: TopologyOptions, draw: () => number) {
    const graph = new Graph(count, options.maxEdges);
    for (let a = 0; a < count; a++)
        for (let b = options.directed ? 0 : a + 1; b < count; b++) {
            if (a === b || draw() >= options.probability)
                continue;
            graph.add(a, b);
            if (!options.directed)
                graph.add(b, a);
        }
    return graph;
}
function smallWorld(count: number, options: TopologyOptions, draw: () => number) {
    const base = new Graph(count, options.maxEdges * 2), degree = Math.floor(Math.min(count - 1, options.kNeighbors) / 2);
    for (let a = 0; a < count; a++)
        for (let offset = 1; offset <= degree; offset++)
            addUndirected(base, a, (a + offset) % count);
    for (let offset = 1; offset <= degree; offset++)
        for (let a = 0; a < count; a++) {
            if (draw() >= options.probability)
                continue;
            const previous = (a + offset) % count;
            const choices = Array.from({
                length: count
            }, (_, i) => i).filter(b => b !== a && !base.has(a, b));
            const target = choose(choices, draw);
            if (target === undefined)
                continue;
            base.remove(a, previous);
            base.remove(previous, a);
            addUndirected(base, a, target);
        }
    return orient(base, options);
}
function weightedTarget(degrees: readonly number[], excluded: Set<number>, draw: () => number) {
    const total = degrees.reduce((sum, n, i) => sum + (excluded.has(i) ? 0 : n), 0);
    let target = draw() * total;
    for (let i = 0; i < degrees.length; i++) {
        if (excluded.has(i))
            continue;
        target -= degrees[i]!;
        if (target < 0)
            return i;
    }
    throw Error('SOCIAL_TOPOLOGY_DEGREE');
}
function scaleFree(count: number, options: TopologyOptions, draw: () => number) {
    const base = new Graph(count, options.maxEdges * 2);
    if (count < 2)
        return base;
    const m = Math.min(count - 1, options.mEdges), degrees = Array.from({
        length: m + 1
    }, (_, i) => i ? 1 : m);
    for (let i = 1; i <= m; i++)
        addUndirected(base, 0, i);
    for (let node = m + 1; node < count; node++) {
        const selected = new Set<number>();
        while (selected.size < m)
            selected.add(weightedTarget(degrees, selected, draw));
        for (const target of selected) {
            addUndirected(base, node, target);
            degrees[target]++;
        }
        degrees.push(m);
    }
    return orient(base, options);
}
function traditional(count: number, options: TopologyOptions, draw: () => number) {
    const algorithms = {
        complete, random, 'small-world': smallWorld, 'scale-free': scaleFree
    };
    const name = options.algorithm as keyof typeof algorithms;
    return algorithms[name](count, options, draw);
}
function increaseReciprocity(graph: Graph, draw: () => number) {
    const edge = choose([...graph.edges()].filter(([a, b]) => !graph.has(b, a)), draw);
    if (edge) {
        graph.add(edge[1], edge[0]);
        return;
    }
    const pair = pickPair(graph, (a, b) => !graph.has(a, b) && !graph.has(b, a), draw, false);
    if (pair)
        addUndirected(graph, ...pair);
}
function decreaseReciprocity(graph: Graph, draw: () => number) {
    const edge = choose([...graph.edges()].filter(([a, b]) => graph.has(b, a)), draw);
    if (edge) {
        if (draw() < 0.5)
            graph.remove(edge[1], edge[0]);
        else
            graph.remove(edge[0], edge[1]);
        return;
    }
    const pair = pickPair(graph, (a, b) => !graph.has(a, b), draw, true);
    if (pair)
        graph.add(...pair);
}
export async function generate(count: number, options: TopologyOptions, draw: () => number, signal: AbortSignal) {
    signal.throwIfAborted();
    if (options.algorithm !== 'cv-targeted')
        return [...traditional(count, options, draw).edges()];
    const graph = traditional(count, {
        ...options, algorithm: options.baseAlgorithm, directed: true
    }, draw);
    for (let i = 0; i < options.maxIterations; i++) {
        await setImmediate();
        signal.throwIfAborted();
        const current = graph.meanCV();
        if (Math.abs(current - options.targetCV) < options.convergenceThreshold)
            break;
        if (current < options.targetCV)
            increaseReciprocity(graph, draw);
        else
            decreaseReciprocity(graph, draw);
    }
    return [...graph.edges()];
}
