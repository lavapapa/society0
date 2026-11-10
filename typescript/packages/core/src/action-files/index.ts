/** /self/actions：行动清单的只读文件视图，数据只来自 actions.offers() 与 describe()。 */
import Schema from '@society0/schemastery';
import { compareCodePoints } from '@society0/host/order';
import type { Context } from '@society0/cordis';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { Json } from '../contracts/common.js';
import type { DomainVersion } from '../contracts/data.js';
import type { SelfProvider } from '../contracts/public-self.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import { canonicalJson, type ActionOffer, type ActionRef, type OfferLimits } from '../contracts/affordance.js';
import '../actions/index.js';
import '../public-self/index.js';

export const Config = Schema.object({});
export const metadata: PluginMetadata = {
    "title": "action-files",
    "summary": "把当前主体的 offer 集与 schema 卡片挂到 /self/actions，供行动清单的完整视图读取。",
    "configVersion": 1,
    "configPolicy": [],
    "scope": "generation",
    "allowedScopes": ["generation"],
    "hostAbi": "society0/host/1"
};
export const api: ApiEntry = {
    "exportPath": "./action-files",
    "provides": [{ "key": "actionFiles", "protocol": "actionFiles", "major": 1, "capabilities": [] }],
    "requires": [
        { "service": { "key": "actions", "protocol": "actions", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" },
        { "service": { "key": "publicSelf", "protocol": "publicSelf", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }
    ]
};

export const OFFERS_PATH = '/self/actions/offers.jsonl';
export const CATALOG_PATH = '/self/actions/catalog.jsonl';

/** 只读标记服务：告知其它插件完整行动清单文件的位置。 */
export interface ActionFilesService {
    readonly offersPath: string;
    readonly catalogPath: string;
}

export const ACTION_FILES_SERVICE: ActionFilesService = Object.freeze({
    offersPath: OFFERS_PATH,
    catalogPath: CATALOG_PATH,
});

declare module '@society0/cordis' {
    interface Context {
        actionFiles: ActionFilesService;
    }
}

/**
 * 文件视图的具名上限：与 SQLite 默认单次查询预算一致（maxQueryRows=10000、maxQueryBytes=1 MiB）。
 * offer provider 会把 maxOffers 当作 SQL 的 maxRows，超过数据库上限会直接 SQLITE_QUERY_BUDGET；
 * 这里取同一组具名值，既不放任 provider 无界查询，也让生成的 JSONL 不超过 shell 单次读取上限。
 */
export const SELF_ACTION_MAX_OFFERS = 10000;
export const SELF_ACTION_MAX_BYTES = 1024 * 1024;
export const SELF_ACTION_LIMITS: OfferLimits = {
    maxOffers: SELF_ACTION_MAX_OFFERS,
    maxOfferBytes: SELF_ACTION_MAX_BYTES,
    maxSchemaBytes: SELF_ACTION_MAX_BYTES,
};

const scopeOf = (actorId: string, momentId: string) => ({ actorId, momentId, query: null });
const bytes = (text: string) => Buffer.from(text);

/** 一行只保留行动名、摘要与执行必需的可供性字段；完整 input/output schema 只在 catalog.jsonl。 */
function fileLine(offer: ActionOffer, description?: string): ActionRef & Readonly<Record<string, Json>> {
    return {
        act: offer.action,
        ...(description ? { summary: description } : {}),
        ...(offer.target ? { target: `${offer.target.kind}:${offer.target.id}` } : {}),
        ...(offer.prefill !== undefined ? { prefill: offer.prefill } : {}),
        ...(offer.locked ? { locked: [...offer.locked] } : {}),
        ...(offer.needs ? { needs: offer.needs } : {}),
        ...(offer.reason ? { reason: offer.reason } : {}),
        ...(offer.until !== undefined ? { until: offer.until } : {}),
    };
}

function providerOf(ctx: Context, path: string, render: (actorId: string, view: SqliteReadView, momentId: string) => AsyncIterable<Uint8Array>): SelfProvider {
    return {
        path,
        media: 'application/jsonl',
        async versions(actorId: string, view: SqliteReadView, scope): Promise<readonly DomainVersion[]> {
            void actorId;
            return [
                { owner: 'actions', version: (await ctx.actions.version()).version },
                { owner: 'view', version: view.revision },
                { owner: 'moment', version: scope?.momentId ?? '' },
            ];
        },
        render(actorId, view, _signal, scope) {
            return render(actorId, view, scope?.momentId ?? '');
        },
    };
}

export default {
    Config,
    inject: ['actions', 'publicSelf'],
    apply(ctx: Context) {
        ctx.provide('actionFiles', ACTION_FILES_SERVICE);
        const offers = providerOf(ctx, OFFERS_PATH, async function* (actorId, view, momentId) {
            const set = await ctx.actions.offers(scopeOf(actorId, momentId), view, SELF_ACTION_LIMITS);
            for (const offer of set.offers)
                yield bytes(canonicalJson(fileLine(offer, set.schemas.get(offer.action)?.description) as unknown as Json) + '\n');
        });
        const catalog = providerOf(ctx, CATALOG_PATH, async function* (actorId, view, momentId) {
            const set = await ctx.actions.offers(scopeOf(actorId, momentId), view, SELF_ACTION_LIMITS);
            const names = [...new Set(set.offers.map(offer => offer.action))].sort(compareCodePoints);
            for (const name of names) {
                const card = await ctx.actions.describe(name);
                if (card)
                    yield bytes(canonicalJson(card as unknown as Json) + '\n');
            }
        });
        ctx.effect(() => ctx.publicSelf.provide(offers));
        ctx.effect(() => ctx.publicSelf.provide(catalog));
    }
};
