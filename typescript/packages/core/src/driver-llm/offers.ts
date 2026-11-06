import type { Json } from '../contracts/common.js';
import type { DiscoveryScope } from '../contracts/data.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import type { Context } from '@society0/host';
import {
    applyOfferDelta,
    diffOffers,
    offerEntry,
    renderOfferDelta,
    truncateOffers,
    type OfferDelta,
    type OfferEntry,
    type OfferFoldState,
    type OfferLimits,
    type OfferSet
} from '../contracts/affordance.js';

export const DRIVER_OFFER_LIMITS: OfferLimits = { maxOffers: 64, maxOfferBytes: 3072, maxSchemaBytes: 4096 };

export interface OfferDelivery {
    readonly text: string;
    readonly record: Json | undefined;
    readonly delta: OfferDelta;
    readonly fold: OfferFoldState;
}

function entriesOf(set: OfferSet): OfferEntry[] {
    return set.offers.map(offer => offerEntry(offer, set.schemas.get(offer.action)));
}

function recordOf(delta: OfferDelta): Json | undefined {
    if (!delta.entries.length && !delta.expired.length && !delta.schemas.length)
        return undefined;
    return {
        added: delta.entries as unknown as Json,
        expired: delta.expired as unknown as Json,
        schemas: delta.schemas.map(card => card.digest) as unknown as Json
    };
}

/**
 * 计算本次送达。先用 actions 的预算取一次，若被截断再取完整有效集，
 * 以便被预算收起但仍有效的 offer 进入 folded 而不是 expired。
 */
export async function offerDelivery(actions: Context['actions'], scope: DiscoveryScope, view: SqliteReadView, fold: OfferFoldState, limits: OfferLimits = DRIVER_OFFER_LIMITS): Promise<OfferDelivery> {
    const limited = await actions.offers(scope, view, limits);
    const omitted = limited.omitted.reduce((total, item) => total + item.count, 0);
    const full = omitted === 0 ? limited : await actions.offers(scope, view, { ...limits, maxOffers: limited.offers.length + omitted, maxOfferBytes: Number.MAX_SAFE_INTEGER });
    const entries = entriesOf(full);
    const truncated = truncateOffers(entries, limits, fold.schemas);
    const delta = { ...diffOffers(fold, entries, truncated.deliver), omitted: truncated.omitted };
    return {
        text: renderOfferDelta(delta),
        record: recordOf(delta),
        delta,
        fold: applyOfferDelta(fold, delta)
    };
}

export function deliveredKeys(fold: OfferFoldState): ReadonlySet<string> {
    return new Set(fold.delivered.keys());
}
