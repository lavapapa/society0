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
    "provides": [],
    "requires": [
        { "service": { "key": "actions", "protocol": "actions", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" },
        { "service": { "key": "publicSelf", "protocol": "publicSelf", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }
    ]
};

export const OFFERS_PATH = '/self/actions/offers.jsonl';
export const CATALOG_PATH = '/self/actions/catalog.jsonl';

/** 文件视图是完整清单，不受送达截断影响；预算参数保持安全整数上限。 */
export const SELF_ACTION_LIMITS: OfferLimits = {
    maxOffers: Number.MAX_SAFE_INTEGER,
    maxOfferBytes: Number.MAX_SAFE_INTEGER,
    maxSchemaBytes: Number.MAX_SAFE_INTEGER,
};

const scopeOf = (actorId: string, momentId: string) => ({ actorId, momentId, query: null });
const bytes = (text: string) => Buffer.from(text);

function refOf(offer: ActionOffer): ActionRef {
    return {
        act: offer.action,
        ...(offer.target ? { target: `${offer.target.kind}:${offer.target.id}` } : {}),
        ...(offer.prefill !== undefined ? { prefill: offer.prefill } : {}),
        ...(offer.needs ? { needs: offer.needs } : {}),
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
        const offers = providerOf(ctx, OFFERS_PATH, async function* (actorId, view, momentId) {
            const set = await ctx.actions.offers(scopeOf(actorId, momentId), view, SELF_ACTION_LIMITS);
            for (const offer of set.offers)
                yield bytes(canonicalJson(refOf(offer) as unknown as Json) + '\n');
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
