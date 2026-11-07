/** plain 的 /public/actors.jsonl 投影：只写公开字段。 */
import type { Context } from '@society0/host';
import type { PublicTableDecl } from '@society0/core/contracts/public';
import type { SqliteTransaction } from '@society0/core/sqlite/contracts';
import type { Json } from '../contracts/common.js';

export const actorsDecl: PublicTableDecl = {
    path: '/public',
    title: '主体',
    description: '主体公开资料；属性按原样公开，参与状态与版本单独列出。',
    schema: {
        type: 'object',
        properties: {
            id: { type: 'string' },
            participation: { type: 'string' },
            version: { type: 'integer' },
            attributes: {}
        }
    },
    key: ['id'],
    sort: [{ field: 'id' }],
    shard: { by: '', format: 'none', file: () => 'actors' },
    notify: 'digest'
};

export interface PublicActor {
    readonly id: string;
    readonly participation: 'active' | 'inactive';
    readonly version: string;
    readonly attributes: Json;
}

interface PublicHost {
    registry: { table(decl: PublicTableDecl): () => void };
    writer(tx: SqliteTransaction): { upsert(table: string, rows: Iterable<Json>): Promise<{ changed: number }> };
}

const installed = new WeakSet<object>();

function register(ctx: Context, service: PublicHost): void {
    if (installed.has(ctx))
        return;
    try {
        service.registry.table(actorsDecl);
    } catch (error) {
        if (!String(error).includes('PUBLIC_TABLE_EXISTS'))
            throw error;
    }
    installed.add(ctx);
}

/** /public 存在时登记 actors 表；重复调用幂等。 */
export function ensureActorsTable(ctx: Context): void {
    const service = ctx.get('public', false) as PublicHost | undefined;
    if (service) {
        register(ctx, service);
        return;
    }
    ctx.inject(['public'], scoped => {
        const scopedService = scoped.get('public') as PublicHost;
        if (scopedService)
            register(ctx, scopedService);
    });
}

export function publicActor(actor: PublicActor): Json {
    return {
        id: actor.id,
        participation: actor.participation,
        version: Number(actor.version),
        attributes: actor.attributes
    };
}

/** 与领域写入同事务；/public 未装配时不报错，保持旧 profile 可用。 */
export async function writePublicActor(ctx: Context, tx: SqliteTransaction, actor: PublicActor): Promise<void> {
    const service = ctx.get('public', false) as PublicHost | undefined;
    if (!service)
        return;
    await service.writer(tx).upsert(actorsDecl.path, [publicActor(actor)]);
}
