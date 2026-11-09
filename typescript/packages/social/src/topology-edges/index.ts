import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    actors: Schema.array(Schema.object({
        id: Schema.string().required(), attributes: Schema.any().default({})
    })).required(),
    edges: Schema.array(Schema.object({
        from: Schema.string().required(), to: Schema.string().required()
    })).default([]),
    algorithm: Schema.union(['provided', 'random', 'small-world', 'scale-free', 'complete', 'cv-targeted']).default('provided'), directed: Schema.boolean().default(true),
    probability: Schema.number().min(0).max(1).default(0.1), kNeighbors: Schema.natural().default(4), mEdges: Schema.natural().min(1).default(2),
    targetCV: Schema.number().min(0).max(1).default(0.5), convergenceThreshold: Schema.number().min(0).max(1).default(0.01), maxIterations: Schema.natural().max(10000).default(1000),
    baseAlgorithm: Schema.union(['random', 'small-world', 'scale-free']).default('small-world'), maxActors: Schema.natural().min(1).default(1024), maxEdges: Schema.natural().min(1).default(65536)
});
export const metadata: PluginMetadata = {
    "title": "topology-edges", "summary": "\u663e\u5f0f\u8fb9\u3001\u968f\u673a\u3001\u5c0f\u4e16\u754c\u3001\u65e0\u6807\u5ea6\u3001\u5b8c\u5168\u56fe\u4e0e\u76ee\u6807\u4e92\u60e0\u7387\u521d\u59cb\u5316", "scope": "bootstrap", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/actors", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/edges", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/algorithm", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/directed", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/probability", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/kNeighbors", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/mEdges", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/targetCV", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/convergenceThreshold", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/maxIterations", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/baseAlgorithm", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/maxActors", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/maxEdges", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./topology-edges", "provides": [], "requires": [{
            "service": {
                "key": "slots", "protocol": "slots", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "random", "protocol": "random", "major": 1, "capabilities": []
            }, "optional": true, "phase": "module"
        }]
};
import { topologySlot } from '../social-init/index.js';
import type { Random } from '@society0/core/contracts/random';
import { generate } from './algorithms.js';
import type { GraphSeed } from '../contracts.js';
export default {
    Config,
    inject: ['slots'],
    apply(ctx: Context, input = {}) {
        const config = Config(input);
        const contribute = (owner: Context) => owner.slots.contribute(topologySlot, {
            id: 'provided-edges/v1',
            value: {
                id: 'provided-edges/v1',
                async build(input, context) {
                    const world = input as { actors?: readonly { id: string; attributes?: unknown }[] } | undefined;
                    const actors = Array.isArray(world?.actors) && world.actors.length
                        ? world.actors.map(actor => ({ id: actor.id, attributes: (actor.attributes ?? {}) as never }))
                        : config.actors;
                    if (actors.length > config.maxActors || config.edges.length > config.maxEdges)
                        throw Error('SOCIAL_TOPOLOGY_BUDGET');
                    if (config.algorithm === 'provided')
                        return structuredClone({
                            actors, edges: config.edges
                        }) as GraphSeed;
                    let index = 0n;
                    const draw = () => {
                        const random = owner.get('random') as Random | undefined;
                        if (!random)
                            throw Error('SOCIAL_TOPOLOGY_RANDOM_REQUIRED');
                        return random.at({
                            run: context.runId, actor: 'topology', operation: context.operationId, stream: config.algorithm, index: String(index++)
                        }).uniform;
                    };
                    const edges = await generate(actors.length, config, draw, context.signal);
                    return {
                        actors: structuredClone(actors), edges: edges.map(([a, b]) => ({
                            from: actors[a]!.id, to: actors[b]!.id
                        }))
                    };
                }
            }
        });
        if (config.algorithm === 'provided' || config.algorithm === 'complete')
            contribute(ctx);
        else
            ctx.inject(['random'], owner => {
                contribute(owner);
            });
    }
};
