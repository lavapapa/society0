import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ freeze: Schema.union(['clock', 'first-acquire', 'live']).default('clock') });
export const metadata: PluginMetadata = {
    "title": "moment-views",
    "summary": "按moment冻结的读视图：记录R_m并以rev_from<=R<rev_to做as-of读取；无时钟调用点时可first-acquire。",
    "configVersion": 1,
    "configPolicy": [{ "path": "/freeze", "mode": "new-run", "affectsResults": true, "secret": false }],
    "scope": "generation",
    "allowedScopes": ["generation"],
    "hostAbi": "society0/host/1"
};
export const api: ApiEntry = {
    "exportPath": "./moment-views",
    "provides": [{ "key": "momentViews", "protocol": "momentViews", "major": 1, "capabilities": [] }],
    "requires": [{ "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }]
};
import { Context, Service } from '@society0/cordis';
import type { Invocation, Lease } from '../contracts/common.js';
import type { MomentReadModel, MomentView, MomentViews } from '../contracts/moment-views.js';
import type { SqliteDatabase } from '../sqlite/index.js';
import type { WriteLease } from '../execution-fence/index.js';
import { fault } from '../sqlite/contracts.js';
import type { SqliteReadView, SqliteSnapshot } from '../sqlite/contracts.js';
import '../sqlite/index.js';
export { momentViewsMigration } from './schema.js';
import { momentViewsMigration } from './schema.js';
export const migrations = (source: Readonly<Record<string, unknown>>) => [momentViewsMigration(source)];

export interface MomentViewsOptions { readonly freeze: 'clock' | 'first-acquire' | 'live' }
declare module '@society0/cordis' {
    interface Context {
        momentViews: MomentViewsService;
    }
}
const request = (operationId: string) => ({
    operationId, maxDurationMs: 10000, maxStatements: 16, maxInputBytes: 65536, maxChangedRows: 8, maxCapturedBytes: 65536
});
export class MomentViewsService extends Service implements MomentViews {
    static Config = Config;
    static inject = ['database'];
    constructor(ctx: Context, readonly options: MomentViewsOptions) {
        super(ctx, 'momentViews');
    }
    async open(tx: Parameters<MomentViews['open']>[0], momentId: string): Promise<string> {
        const at = BigInt(tx.baseRevision);
        await tx.execute({ sql: 'INSERT INTO s0_moment_views(moment_id,revision,opened_at_revision) VALUES(?,?,?) ON CONFLICT(moment_id) DO NOTHING', parameters: [momentId, at, at] });
        const [row] = await tx.query({ sql: 'SELECT revision FROM s0_moment_views WHERE moment_id=?', parameters: [momentId] }, { maxRows: 1, maxBytes: 1024 });
        return String(row!.revision);
    }
    async revision(momentId: string, reader: SqliteReadView): Promise<string | null> {
        const [row] = await reader.query({ sql: 'SELECT revision FROM s0_moment_views WHERE moment_id=?', parameters: [momentId] }, { maxRows: 1, maxBytes: 1024 });
        return row ? String(row.revision) : null;
    }
    private async lookup(momentId: string): Promise<string | null> {
        const snapshot = await this.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            return await this.revision(momentId, snapshot);
        }
        finally {
            await snapshot.close();
        }
    }
    async acquire(scope: { readonly momentId: string }, context: Invocation & { readonly writeLease?: WriteLease; readonly database?: SqliteDatabase }): Promise<Lease<MomentView>> {
        if (!scope.momentId)
            throw fault('MOMENT_VIEW_SCOPE');
        let revision = await this.lookup(scope.momentId), frozen = true;
        if (revision === null && this.options.freeze === 'live') {
            const snapshot = await this.ctx.database.snapshot({ maxAgeMs: 30000 });
            revision = snapshot.revision;
            await snapshot.close();
            frozen = false;
        }
        else if (revision === null) {
            const lease = context.writeLease, database = context.database;
            if (!lease || !database)
                throw fault('MOMENT_VIEW_OPEN', 'first acquire requires the activation write lease');
            try {
                const recorded = await database.transaction(lease, request(`moment-view:${context.runId}:${scope.momentId}`), tx => this.open(tx, scope.momentId));
                revision = recorded.value;
            }
            catch (error) {
                if ((error as { code?: string }).code !== 'OPERATION_EXISTS')
                    throw error;
                revision = await this.lookup(scope.momentId);
                if (revision === null)
                    throw error;
            }
        }
        return this.lease(scope.momentId, revision, frozen);
    }
    async read(scope: { readonly momentId: string }, context: Invocation & { readonly writeLease?: WriteLease; readonly database?: SqliteDatabase }): Promise<Lease<MomentReadModel>> {
        const frozen = await this.acquire(scope, context);
        const opened = new Set<SqliteSnapshot>();
        let closed = false;
        const value: MomentReadModel = {
            frozen: frozen.value,
            self: async () => {
                if (closed)
                    throw fault('MOMENT_VIEW_RELEASED');
                const snapshot = await this.ctx.database.snapshot({ maxAgeMs: 30000 });
                opened.add(snapshot);
                return snapshot;
            }
        };
        return {
            value,
            release: async () => {
                if (closed)
                    return;
                closed = true;
                await frozen.release();
                for (const snapshot of opened)
                    await snapshot.close();
            }
        };
    }
    private lease(momentId: string, revision: string, frozen: boolean): Lease<MomentView> {
        let snapshot: SqliteSnapshot | undefined, closed = false;
        const read = async () => {
            if (closed)
                throw fault('MOMENT_VIEW_RELEASED');
            snapshot ??= await this.ctx.database.snapshot({ maxAgeMs: 30000 });
            return snapshot;
        };
        const value: MomentView = {
            momentId, revision, frozen,
            query: async (statement, bounds) => (await read()).query(statement, bounds),
            pinned: read
        };
        return {
            value,
            release: async () => {
                if (closed)
                    return;
                closed = true;
                if (snapshot)
                    await snapshot.close();
            }
        };
    }
}
export default MomentViewsService;
export type { MomentReadModel, MomentView, MomentViews } from '../contracts/moment-views.js';
