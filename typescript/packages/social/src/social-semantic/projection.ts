import type { Context } from '@society0/host';
import '@society0/extras/vector-providers';
import type { MemoryIndex, MemoryIndexProgress, MemoryChange, MemoryChangeCursor, MemoryVector } from '@society0/extras/vector-providers';
import type { WriteLease } from '@society0/core/execution-fence';
import type { Invocation, SqliteTransaction, SocialPost, SocialReader } from '../contracts.js';
import '../social-repository/index.js';
import { transactionRequest } from '../paging.js';
export function identityKey(identity: MemoryVector['identity']) {
    return JSON.stringify({
        provider: identity.provider, configId: identity.configId, model: identity.model, dimensions: identity.dimensions
    });
}
export class SemanticProjection {
    constructor(private ctx: Context, readonly provider: string, readonly space: string, readonly batchSize = 64) {
    }
    async enqueue(tx: SqliteTransaction, post: SocialPost) {
        await tx.execute({
            sql: 'INSERT INTO social_semantic_pending VALUES(?,?,?) ON CONFLICT(post_id) DO UPDATE SET text_version=excluded.text_version,content=excluded.content', parameters: [post.id, BigInt(post.textVersion), post.content]
        });
    }
    async save(tx: SqliteTransaction, postId: string, textVersion: string, vector: MemoryVector, revision: string) {
        const post = await this.ctx.socialRepository.get(postId, tx);
        if (!post || post.textVersion !== textVersion)
            throw Error('SOCIAL_SEMANTIC_TEXT_STALE');
        await tx.execute({
            sql: 'INSERT INTO social_semantic_vectors VALUES(?,?,?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET text_version=excluded.text_version,identity=excluded.identity,vector=excluded.vector,origin=excluded.origin,revision=excluded.revision', parameters: [postId, BigInt(textVersion), identityKey(vector.identity), JSON.stringify(vector.vector), JSON.stringify(vector.origin), BigInt(revision)]
        });
        await tx.execute({
            sql: 'INSERT INTO social_semantic_changes VALUES(?,?,?,?,?)', parameters: [BigInt(revision), postId, 'upsert', post.authorId, JSON.stringify(vector)]
        });
        await tx.execute({
            sql: 'DELETE FROM social_semantic_pending WHERE post_id=? AND text_version=?', parameters: [postId, BigInt(textVersion)]
        });
        await this.ctx.socialRepository.bump(tx, 'social/semantic');
    }
    async remove(tx: SqliteTransaction, id: string, revision: string) {
        const post = await this.ctx.socialRepository.get(id, tx);
        if (!post)
            throw Error('SOCIAL_POST_MISSING');
        await tx.execute({
            sql: 'DELETE FROM social_semantic_vectors WHERE post_id=?', parameters: [id]
        });
        await tx.execute({
            sql: 'INSERT INTO social_semantic_changes VALUES(?,?,?,?,NULL)', parameters: [BigInt(revision), id, 'delete', post.authorId]
        });
        await this.ctx.socialRepository.bump(tx, 'social/semantic');
    }
    namespace(index: MemoryIndex) {
        if (index.runId !== this.ctx.vectorProviders.runId || index.space !== this.space)
            throw Error('SOCIAL_SEMANTIC_NAMESPACE');
        return [index.runId, index.generation, index.space, identityKey(index.identity), index.distance];
    }
    async progress(reader: SocialReader, index: MemoryIndex): Promise<MemoryIndexProgress | undefined> {
        const [row] = await reader.query({
            sql: 'SELECT progress FROM social_semantic_progress WHERE run_id=? AND generation=? AND space=? AND identity=? AND distance=?', parameters: this.namespace(index)
        }, {
            maxRows: 1, maxBytes: 65536
        });
        return row ? JSON.parse(String(row.progress)) : undefined;
    }
    async recordProgress(tx: SqliteTransaction, index: MemoryIndex, progress: MemoryIndexProgress) {
        await tx.execute({
            sql: 'INSERT INTO social_semantic_progress VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,generation,space,identity,distance) DO UPDATE SET progress=excluded.progress', parameters: [...this.namespace(index), JSON.stringify(progress)]
        });
    }
    private async readChanges(after: MemoryChangeCursor | undefined, throughRevision: string): Promise<MemoryChange[]> {
        const view = await this.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        try {
            const rows = await view.query({
                sql: 'SELECT * FROM social_semantic_changes WHERE revision<=? AND (revision>? OR (revision=? AND id>?)) ORDER BY revision,id LIMIT ?', parameters: [BigInt(throughRevision), BigInt(after?.revision ?? '0'), BigInt(after?.revision ?? '0'), after?.id ?? '', this.batchSize]
            }, {
                maxRows: this.batchSize, maxBytes: 1048576
            });
            return rows.map(row => row.kind === 'delete' ? {
                kind: 'delete', cursor: {
                    revision: String(row.revision), id: String(row.id)
                }, actorId: String(row.actor_id)
            } : {
                kind: 'upsert', cursor: {
                    revision: String(row.revision), id: String(row.id)
                }, actorId: String(row.actor_id), vector: JSON.parse(String(row.vector))
            });
        }
        finally {
            await view.close();
        }
    }
    private async applyPage(index: MemoryIndex, changes: readonly MemoryChange[], throughRevision: string, page: number, lease: WriteLease, context: Invocation) {
        await index.apply(changes, context.signal);
        const after = changes.at(-1)!.cursor, progress: MemoryIndexProgress = {
            runId: index.runId, generation: index.generation, space: index.space, identity: index.identity, distance: index.distance, after, throughRevision
        };
        const operationId = context.operationId + '/index/' + page;
        await this.ctx.database.transaction(lease, transactionRequest(operationId), tx => this.recordProgress(tx, index, progress));
        return {
            after, operationId
        };
    }
    private async advanceProjection(index: MemoryIndex, lease: WriteLease, context: Invocation) {
        let after: MemoryChangeCursor | undefined;
        const throughRevision = await this.ctx.database.readRevision(), operations: string[] = [];
        let page = 0;
        // 有界SQL变更流重放同时覆盖后端空投影和apply之后、进度写入之前的崩溃。
        while (true) {
            context.signal.throwIfAborted();
            const changes = await this.readChanges(after, throughRevision);
            if (!changes.length)
                break;
            const result = await this.applyPage(index, changes, throughRevision, page++, lease, context);
            after = result.after;
            operations.push(result.operationId);
            if (changes.length < this.batchSize)
                break;
        }
        return {
            requiredOperations: operations, after, throughRevision
        };
    }
    async sync(lease: WriteLease, context: Invocation) {
        const selected = await this.ctx.vectorProviders.acquire(this.provider);
        try {
            const index = selected.value;
            this.namespace(index);
            if (context.runId !== index.runId || lease.runId !== index.runId || context.generation !== index.generation)
                throw Error('SOCIAL_SEMANTIC_RUN');
            return await this.advanceProjection(index, lease, context);
        }
        finally {
            await selected.release();
        }
    }
}
