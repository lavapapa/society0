import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    embeddingProvider: Schema.string().required(), vectorProvider: Schema.string().required(), space: Schema.string().default('social'), batchSize: Schema.natural().min(1).max(256).default(32), maxIndexedPosts: Schema.natural().min(1).default(100)
});
export const metadata: PluginMetadata = {
    "title": "social-semantic", "summary": "\u539f\u59cb\u5411\u91cf\u4e0epending\u5f52SQL\u3001\u5916\u90e8projection\u6709\u754c\u91cd\u653e\u3001\u51c6\u5907\u540e\u590d\u67e5\u6587\u672c\u7248\u672c", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/embeddingProvider", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/vectorProvider", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/space", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/batchSize", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/maxIndexedPosts", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./social-semantic", "provides": [{
            "key": "socialSemantic", "protocol": "socialSemantic", "major": 1, "capabilities": []
        }], "requires": [{
            "service": {
                "key": "socialRepository", "protocol": "socialRepository", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "recommendationSources", "protocol": "recommendationSources", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "embeddings", "protocol": "embeddings", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "vectorProviders", "protocol": "vectorProviders", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "database", "protocol": "database", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "tasks", "protocol": "tasks", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import type { TaskScope } from '@society0/host/tasks';
import type { Embeddings, EmbeddingIdentity } from '@society0/core/contracts/cognition';
import type { WriteLease } from '@society0/core/execution-fence';
import type { Invocation, RecommendationInput, RecommendationPreparation, SocialReader } from '../contracts.js';
import '../social-recommendations/index.js';
import { SemanticProjection, identityKey } from './projection.js';
import { compareCursor, sameIdentity, type MemoryIndex, type MemoryIndexProgress } from '@society0/extras/vector-providers';
import { transactionRequest } from '../paging.js';
export * from './schema.js';
export { SemanticProjection } from './projection.js';
declare module '@society0/host' {
    interface Context {
        socialSemantic: SocialSemantic;
    }
}
export class SocialSemantic extends Service {
    static Config = Config;
    static inject = ['socialRepository', 'recommendationSources', 'embeddings', 'vectorProviders', 'database', 'tasks'];
    readonly projection: SemanticProjection;
    private config: ReturnType<typeof Config>;
    private tasks: TaskScope;
    private syncing = false;
    constructor(ctx: Context, input: {
        embeddingProvider: string;
        vectorProvider: string;
        space?: string;
        batchSize?: number;
        maxIndexedPosts?: number;
    }) {
        super(ctx, 'socialSemantic');
        this.config = Config(input);
        this.projection = new SemanticProjection(ctx, this.config.vectorProvider, this.config.space, this.config.batchSize);
        this.tasks = ctx.tasks.bind();
        ctx.socialRepository.onContent((tx, post) => this.projection.enqueue(tx, post));
        ctx.recommendationSources.register({
            id: 'social/semantic/v1', version: 1, dependencies: ['social/semantic', 'social/semantic/embedding-identity'], versions: async (reader) => [...await ctx.socialRepository.versions(reader, ['social/semantic']), {
                    owner: 'social/semantic/embedding-identity', version: identityKey(this.embeddings.identity(this.config.embeddingProvider))
                }], collect: async (_scope, view) => ({
                items: [], total: '0', revision: view.revision, responseBytes: 0
            }), prepare: (input, context) => this.prepare(input, context)
        });
        ctx.effect(() => () => this.tasks.drain('social semantic closing'));
    }
    private get embeddings(): Embeddings {
        return (this.ctx as Context & {
            embeddings: Embeddings;
        }).embeddings;
    }
    async embedPending(lease: WriteLease, context: Invocation) {
        if (this.syncing)
            throw Error('SOCIAL_SEMANTIC_BUSY');
        this.syncing = true;
        const task = this.tasks.run('social.semantic.embed', signal => this.embedBatch(lease, {
            ...context, signal
        }), {
            signal: context.signal, bytes: 1048576
        });
        try {
            return await task;
        }
        finally {
            await task.settled;
            this.syncing = false;
        }
    }
    private async embedBatch(lease: WriteLease, context: Invocation) {
        if (this.embeddings.runId !== context.runId || lease.runId !== context.runId)
            throw Error('SOCIAL_SEMANTIC_RUN');
        const identity = this.embeddings.identity(this.config.embeddingProvider);
        const view = await this.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        let rows;
        try {
            rows = await view.query({
                sql: 'SELECT * FROM social_semantic_pending ORDER BY post_id LIMIT ?', parameters: [this.config.batchSize]
            }, {
                maxRows: this.config.batchSize, maxBytes: 1048576
            });
        }
        finally {
            await view.close();
        }
        if (!rows.length)
            return {
                status: 'ready' as const, requiredOperations: []
            };
        const uses = await this.embeddings.embed({
            identity, texts: rows.map(row => String(row.content))
        }, context);
        if (uses.length !== rows.length)
            throw Error('SOCIAL_SEMANTIC_EMBEDDING_COUNT');
        const expectedRevision = await this.ctx.database.readRevision(), revision = String(BigInt(expectedRevision) + 1n), operationId = context.operationId + '/vectors';
        await this.ctx.database.transaction(lease, {
            ...transactionRequest(operationId), expectedRevision
        }, async (tx) => {
            for (const [i, row] of rows.entries()) {
                const use = uses.find(x => x.inputIndex === i);
                if (!use)
                    throw Error('SOCIAL_SEMANTIC_EMBEDDING_INDEX');
                await this.projection.save(tx, String(row.post_id), String(row.text_version), {
                    identity, vector: use.vector, origin: use.origin
                }, revision);
            }
        });
        return {
            status: 'ready' as const, requiredOperations: [operationId]
        };
    }
    async syncIndex(lease: WriteLease, context: Invocation) {
        if (this.syncing)
            throw Error('SOCIAL_SEMANTIC_BUSY');
        this.syncing = true;
        const task = this.tasks.run('social.semantic.index', signal => this.projection.sync(lease, {
            ...context, signal
        }), {
            signal: context.signal, bytes: 1048576
        });
        try {
            return await task;
        }
        finally {
            await task.settled;
            this.syncing = false;
        }
    }
    async prepare(input: RecommendationInput, context: Invocation): Promise<RecommendationPreparation> {
        const task = this.tasks.run('social.semantic.prepare', signal => this.prepareOwned(input, {
            ...context, signal
        }), {
            signal: context.signal, bytes: input.maxBytes
        });
        try {
            return await task;
        }
        finally {
            await task.settled;
        }
    }
    private selectedIdentity(index: MemoryIndex, context: Invocation) {
        this.projection.namespace(index);
        if (index.generation !== context.generation)
            throw Error('SOCIAL_SEMANTIC_GENERATION');
        const identity = this.embeddings.identity(this.config.embeddingProvider);
        if (!sameIdentity(identity, index.identity))
            throw Error('SOCIAL_SEMANTIC_IDENTITY');
        return identity;
    }
    private async vectorCurrent(reader: SocialReader, item: RecommendationInput['items'][number], identity: EmbeddingIdentity) {
        const [row] = await reader.query({
            sql: 'SELECT text_version,identity FROM social_semantic_vectors WHERE post_id=?', parameters: [item.id]
        }, {
            maxRows: 1, maxBytes: 65536
        });
        return !!row && String(row.text_version) === String((item.features as any).textVersion) && String(row.identity) === identityKey(identity);
    }
    private async projectionCurrent(reader: SocialReader, progress: MemoryIndexProgress) {
        const [head] = await reader.query({
            sql: 'SELECT revision,id FROM social_semantic_changes ORDER BY revision DESC,id DESC LIMIT 1'
        }, {
            maxRows: 1, maxBytes: 4096
        });
        if (!head)
            return true;
        if (!progress.after)
            return false;
        return compareCursor({
            revision: String(head.revision), id: String(head.id)
        }, progress.after) <= 0;
    }
    private async readProjection(input: RecommendationInput, index: MemoryIndex, identity: EmbeddingIdentity): Promise<{
        status: 'ready';
        progress: MemoryIndexProgress;
    } | {
        status: 'stale';
        changed: readonly string[];
    }> {
        const view = await this.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        try {
            const progress = await this.projection.progress(view, index);
            if (!progress || !await this.projectionCurrent(view, progress))
                return {
                    status: 'stale', changed: ['social/semantic/index']
                };
            for (const item of input.items)
                if (!await this.vectorCurrent(view, item, identity))
                    return {
                        status: 'stale', changed: ['social/semantic']
                    };
            const [count] = await view.query({
                sql: 'SELECT count(*) AS n FROM social_semantic_vectors'
            }, {
                maxRows: 1, maxBytes: 1024
            });
            if (BigInt(String(count!.n)) > BigInt(this.config.maxIndexedPosts))
                throw Error('SOCIAL_SEMANTIC_QUERY_BUDGET');
            return {
                status: 'ready', progress
            };
        }
        finally {
            await view.close();
        }
    }
    private similarity(distance: number, index: MemoryIndex) {
        if (index.distance === 'cosine')
            return 1 - distance;
        if (index.distance === 'dot')
            return -distance;
        return 1 / (1 + distance);
    }
    private scored(input: RecommendationInput, index: MemoryIndex, progress: MemoryIndexProgress, query: import('@society0/core/contracts/cognition').EmbeddingUse, matches: readonly {
        id: string;
        distance: number;
    }[]): RecommendationInput {
        const byId = new Map(matches.map(item => [item.id, item.distance]));
        return {
            ...input, items: input.items.map(item => {
                const distance = byId.get(item.id);
                if (distance === undefined)
                    throw Error('SOCIAL_SEMANTIC_INDEX_INCOMPLETE');
                return {
                    ...item, features: {
                        ...(item.features as object), similarity: this.similarity(distance, index), semantic: {
                            distance, metric: index.distance, identity: index.identity, origin: query.origin, progress
                        }
                    } as any
                };
            })
        };
    }
    private async queryProjection(input: RecommendationInput, index: MemoryIndex, identity: EmbeddingIdentity, progress: MemoryIndexProgress, context: Invocation): Promise<RecommendationPreparation> {
        const text = JSON.stringify((input.items[0]!.features as any).preferences ?? {}), [query] = await this.embeddings.embed({
            identity, texts: [text]
        }, context);
        if (!query)
            throw Error('SOCIAL_SEMANTIC_EMBEDDING_COUNT');
        try {
            const matches = await index.query(query.vector, this.config.maxIndexedPosts, progress, context.signal);
            return {
                status: 'ready', input: this.scored(input, index, progress, query, matches)
            };
        }
        catch (error) {
            if ((error as any).code === 'VECTOR_PROGRESS_MISMATCH')
                return {
                    status: 'stale', changed: ['social/semantic/index']
                };
            throw error;
        }
    }
    private async prepareOwned(input: RecommendationInput, context: Invocation): Promise<RecommendationPreparation> {
        if (input.runId !== context.runId || context.runId !== this.ctx.vectorProviders.runId)
            throw Error('SOCIAL_SEMANTIC_RUN');
        if (!input.items.length)
            return {
                status: 'ready', input
            };
        const selected = await this.ctx.vectorProviders.acquire(this.config.vectorProvider);
        try {
            const index = selected.value, identity = this.selectedIdentity(index, context), state = await this.readProjection(input, index, identity);
            if (state.status === 'stale')
                return state;
            return await this.queryProjection(input, index, identity, state.progress, context);
        }
        finally {
            await selected.release();
        }
    }
}
export default SocialSemantic;
