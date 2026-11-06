import { compareCodePoints } from '@society0/host/order';
import type { Json } from '../contracts/common.js';
import type { DiscoveryScope, DomainVersion } from '../contracts/data.js';
import type { ActionDefinition } from '../contracts/domain.js';
import type { SqliteReadView, SqliteTransaction } from '../sqlite/contracts.js';
import { offerEntry, offerKey, schemaCard, targetText, truncateOffers, type ActionOffer, type ActionSchemaCard, type OfferLimits, type OfferProvider, type OfferSet, type TargetRef } from '../contracts/affordance.js';

const coded = (code: string) => Object.assign(new Error(code), { code });

export interface OfferFilter {
    readonly action?: string;
    readonly actions?: readonly string[];
    readonly targets?: readonly TargetRef[];
}

/** offers() 的全部外部输入；gate 已绑定当前 reader 与快照。 */
export interface OfferSources {
    readonly scope: DiscoveryScope;
    readonly actions: readonly ActionDefinition[];
    readonly providers: readonly OfferProvider[];
    readonly targetAware: boolean;
    readonly versions: readonly DomainVersion[];
    readonly limits: OfferLimits;
    readonly filter?: OfferFilter;
    readonly gate: (action: string, target: TargetRef | undefined) => Promise<boolean>;
}

interface Candidate {
    offer: ActionOffer;
    schema: ActionSchemaCard;
    reasons: string[];
}

const exposure = (definition: ActionDefinition) => definition.exposure ?? 'offer';

function compareCandidates(left: Candidate, right: Candidate): number {
    const priority = (right.offer.priority ?? 0) - (left.offer.priority ?? 0);
    if (priority !== 0)
        return priority;
    const action = compareCodePoints(left.offer.action, right.offer.action);
    if (action !== 0)
        return action;
    return compareCodePoints(left.offer.target ? targetText(left.offer.target) : '', right.offer.target ? targetText(right.offer.target) : '');
}

function mergeReasons(reasons: readonly string[]): string[] {
    const unique: string[] = [];
    for (const reason of reasons) {
        if (reason && !unique.includes(reason))
            unique.push(reason);
        if (unique.length === 2)
            break;
    }
    return unique;
}

function accepts(filter: OfferFilter | undefined, action: string, target: TargetRef | undefined): boolean {
    if (!filter)
        return true;
    if (filter.action !== undefined && filter.action !== action)
        return false;
    if (filter.actions !== undefined && !filter.actions.includes(action))
        return false;
    if (filter.targets !== undefined) {
        if (!target || !filter.targets.some((item) => targetText(item) === targetText(target)))
            return false;
    }
    return true;
}

/**
 * 计算当前 offer 集：门槛 ∧ available ∧（ambient ∪ provider），去重后按
 * priority 降序、action、target 文本排序，再按预算整条截断。
 */
export async function computeOffers(sources: OfferSources, reader: SqliteReadView | SqliteTransaction): Promise<OfferSet> {
    const { scope, limits } = sources;
    const byName = new Map(sources.actions.map((definition) => [definition.name, definition]));
    const gates = new Map<string, boolean>();
    const gateOf = async (action: string): Promise<boolean> => {
        const cached = gates.get(action);
        if (cached !== undefined)
            return cached;
        const value = await sources.gate(action, undefined);
        gates.set(action, value);
        return value;
    };
    const candidates = new Map<string, Candidate>();

    for (const definition of sources.actions) {
        if (exposure(definition) !== 'offer' || definition.targets || definition.ambient === false)
            continue;
        if (!accepts(sources.filter, definition.name, undefined))
            continue;
        if (!await gateOf(definition.name))
            continue;
        if (!await definition.available(scope, reader))
            continue;
        const offer: ActionOffer = { action: definition.name, provider: 'ambient' };
        candidates.set(offerKey(offer), { offer, schema: schemaCard(definition.name, { description: definition.description, input: definition.inputSchema, targets: definition.targets }), reasons: [] });
    }

    for (const provider of [...sources.providers].sort((left, right) => compareCodePoints(left.id, right.id))) {
        const query = {
            actorId: scope.actorId,
            momentId: scope.momentId,
            ...(scope.logicalTime !== undefined ? { logicalTime: scope.logicalTime } : {}),
            ...(sources.filter?.action !== undefined ? { action: sources.filter.action } : {}),
            ...(sources.filter?.targets !== undefined ? { targets: sources.filter.targets } : {}),
            maxItems: limits.maxOffers,
        };
        for (const raw of await provider.offers(query, reader)) {
            if (!provider.actions.includes(raw.action))
                throw coded('OFFER_UNDECLARED');
            const definition = byName.get(raw.action);
            if (!definition || exposure(definition) !== 'offer')
                throw coded('OFFER_UNDECLARED');
            if (!accepts(sources.filter, raw.action, raw.target))
                continue;
            if (definition.targets) {
                if (!raw.target || !definition.targets.kinds.includes(raw.target.kind))
                    throw coded('OFFER_TARGET_INVALID');
            }
            else if (raw.target)
                throw coded('OFFER_TARGET_INVALID');
            if (!await gateOf(raw.action))
                continue;
            if (sources.targetAware && raw.target && !await sources.gate(raw.action, raw.target))
                continue;
            if (!await definition.available(raw.target ? { ...scope, target: raw.target } : scope, reader))
                continue;
            const offer: ActionOffer = { ...raw, provider: provider.id };
            const key = offerKey(offer);
            const schema = schemaCard(definition.name, { description: definition.description, input: definition.inputSchema, targets: definition.targets });
            const reasons = mergeReasons([...(raw.reason ? [raw.reason] : [])]);
            const existing = candidates.get(key);
            if (!existing) {
                candidates.set(key, { offer, schema, reasons });
                continue;
            }
            const merged = mergeReasons([...existing.reasons, ...reasons]);
            const priority = offer.priority ?? 0, previous = existing.offer.priority ?? 0;
            const wins = priority > previous || (priority === previous && compareCodePoints(provider.id, existing.offer.provider) < 0);
            if (wins)
                candidates.set(key, { offer: { ...offer, ...(merged.length ? { reason: merged.join('；') } : {}) }, schema, reasons: merged });
            else if (merged.length)
                existing.offer = { ...existing.offer, reason: merged.join('；') };
        }
    }

    const entries = [...candidates.values()].sort(compareCandidates).map((candidate) => offerEntry(candidate.offer, candidate.schema));
    const truncated = truncateOffers(entries, limits);
    const schemas = new Map<string, ActionSchemaCard>();
    for (const entry of truncated.deliver) {
        if (entry.schema)
            schemas.set(entry.schema.action, entry.schema);
    }
    return { offers: truncated.deliver.map((entry) => entry.offer), schemas, omitted: truncated.omitted, versions: sources.versions };
}
