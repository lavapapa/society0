import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    topology: Schema.string().default('provided-edges/v1'), batchSize: Schema.natural().min(1).max(256).default(32),
    startRound: Schema.natural().min(1), pairingGroups: Schema.array(Schema.array(Schema.string())).default([]), pairingSessionMinutes: Schema.natural().min(1).max(120).default(10)
});
export const metadata: PluginMetadata = {
    "title": "social-init", "summary": "social-init", "scope": "bootstrap", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/topology", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/batchSize", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/startRound", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/pairingGroups", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/pairingSessionMinutes", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./social-init", "provides": [], "requires": [{
            "service": {
                "key": "initializers", "protocol": "initializers", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "socialRepository", "protocol": "socialRepository", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "slots", "protocol": "slots", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import { slotKey } from '@society0/host/slots';
import '@society0/core/initializers';
import '../social-repository/index.js';
import { startPairingRound } from '../social/index.js';
import type { TopologyProvider, Json } from '../contracts.js';
import { transactionRequest } from '../paging.js';
export const topologySlot = slotKey<TopologyProvider>('society/social-topology');
type BootstrapContext = Parameters<Parameters<Context['initializers']['register']>[0]['initialize']>[0];
type Graph = Awaited<ReturnType<TopologyProvider['build']>>;
function validateGraph(graph: Graph) {
    const ids = new Set(graph.actors.map(actor => actor.id));
    if (ids.size !== graph.actors.length)
        throw Error('SOCIAL_ACTOR_DUPLICATE');
    for (const edge of graph.edges)
        if (!ids.has(edge.from) || !ids.has(edge.to))
            throw Error('SOCIAL_TOPOLOGY_UNKNOWN_ACTOR');
}
async function seedActors(ctx: Context, context: BootstrapContext, graph: Graph, batchSize: number) {
    const operations: string[] = [];
    for (let offset = 0; offset < graph.actors.length; offset += batchSize) {
        const id = 'social.seed/actors/' + offset;
        await context.database.transaction(context.writeLease, transactionRequest(id), async (tx) => {
            for (const actor of graph.actors.slice(offset, offset + batchSize))
                await ctx.socialRepository.addMember(tx, actor);
        });
        operations.push(id);
    }
    return operations;
}
async function seedEdges(ctx: Context, context: BootstrapContext, graph: Graph, batchSize: number) {
    const operations: string[] = [];
    for (let offset = 0; offset < graph.edges.length; offset += batchSize) {
        const id = 'social.seed/edges/' + offset;
        await context.database.transaction(context.writeLease, transactionRequest(id), async (tx) => {
            for (const [i, edge] of graph.edges.slice(offset, offset + batchSize).entries())
                await ctx.socialRepository.follow(tx, edge.from, edge.to, '0', id + '/' + i);
        });
        operations.push(id);
    }
    return operations;
}
export const initializerAfter = ['actors.seed'] as const;
async function initialize(ctx: Context, config: ReturnType<typeof Config>, context: BootstrapContext) {
    const selection = await ctx.slots.acquire(topologySlot, {
        generation: context.generation
    });
    try {
        const provider = selection.items.find(item => item.id === config.topology)?.value;
        if (!provider)
            throw Error('SOCIAL_TOPOLOGY_MISSING');
        const graph = await provider.build(context.input as Json, {
            runId: context.runId, generation: context.generation, operationId: 'social.seed', signal: context.signal
        });
        validateGraph(graph);
        const actors = await seedActors(ctx, context, graph, config.batchSize), edges = await seedEdges(ctx, context, graph, config.batchSize);
        const operations = [...actors, ...edges];
        if (config.startRound !== undefined) {
            const id = 'social.seed/round/' + config.startRound;
            await context.database.transaction(context.writeLease, transactionRequest(id), tx => startPairingRound(tx, { groups: config.pairingGroups, sessionMinutes: config.pairingSessionMinutes }, config.startRound));
            operations.push(id);
        }
        return {
            operationIds: operations
        };
    }
    finally {
        await selection.release();
    }
}
export default {
    Config,
    inject: ['initializers', 'socialRepository', 'slots'],
    apply(ctx: Context, input = {}) {
        const config = Config(input);
        ctx.slots.declare({
            key: topologySlot, strategy: 'keyed', required: true, validate: (value: unknown) => {
                if (typeof (value as TopologyProvider)?.build !== 'function')
                    throw Error('SOCIAL_TOPOLOGY');
                return value as TopologyProvider;
            }
        });
        ctx.initializers.register({
            id: 'social.seed', after: [...initializerAfter], initialize: context => initialize(ctx, config, context)
        });
    }
};
